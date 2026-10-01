"""Route NỘI BỘ (không dành cho trình duyệt/khách): công cụ tra cứu của AI Agent gọi ngược vào Flask để dùng đúng bộ truy xuất
(Chroma + model embedding đã nạp trong tiến trình web) thay vì nạp thêm 2,2 GB ở tiến trình khác.

Bảo vệ (mỗi lớp có test trong tests/test_agent_internal.py):
1. chỉ nhận từ loopback (127.0.0.1 / ::1) — không dựa vào X-Forwarded-For (giả được);
2. token ký HMAC gắn với ĐÚNG (bot_id, run_id) và hết hạn nhanh (core/context_engine/agent/protocol.py) — không đoán/tái dùng cho bot khác;
3. bot_id lấy từ token đã ký + biến môi trường của tiến trình MCP, KHÔNG phải do model chọn.
4. (công cụ tóm tắt) hội thoại cần tóm tắt lấy từ ngữ cảnh do CHÍNH Flask đặt vào Redis theo run_id (agent/runtime.py), không phải do model
   truyền; hội thoại phải thuộc đúng bot của token; mỗi lượt chỉ được dùng 1 lần.
"""
import json
import logging

from flask import Blueprint, current_app, jsonify, request

from app.dashboard import service as dashboard_service
from app.models import Bot, Conversation, ConversationState
from core import rag_engine
from core.context_engine import builder, jobs
from core.context_engine.agent import cache as agent_cache
from core.context_engine.agent import protocol
from core.context_engine.cost import LLMUsageTracker
from core.context_engine.prompts import texts
from core.context_engine.settings import EngineSettings
from extensions import db, redis_client

bp = Blueprint("internal", __name__, url_prefix="/internal")
logger = logging.getLogger("internal")

LOOPBACK = ("127.0.0.1", "::1")
MAX_QUERY_CHARS = 500
MAX_SEARCHES_KEPT = 20
SEARCH_RECORD_TTL_SECONDS = 300
SUMMARY_RECORD_TTL_SECONDS = 300


def _denied(status: int = 403):
    return jsonify(error="forbidden"), status


def _verified_run():
    """Kiểm tra lớp 1-2 chung cho mọi route nội bộ. Trả ((payload, bot_id, run_id), None) hoặc (None, phản hồi từ chối)."""
    if request.remote_addr not in LOOPBACK:
        return None, _denied()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, _denied(400)
    bot_id, run_id = payload.get("bot_id"), payload.get("run_id")
    if not protocol.verify_run_token(current_app.config["SECRET_KEY"], bot_id, run_id, payload.get("token")):
        return None, _denied()
    return (payload, bot_id, run_id), None


@bp.route("/rag/search", methods=["POST"])
def rag_search():
    verified, denied = _verified_run()
    if denied is not None:
        return denied
    payload, bot_id, run_id = verified
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        return _denied(400)
    bot = Bot.query.filter_by(id=bot_id).first()
    if bot is None:
        return _denied(404)

    query = query.strip()[:MAX_QUERY_CHARS]
    settings = EngineSettings.from_model(dashboard_service.get_or_create_settings(bot))
    cache = agent_cache.RagCache(redis_client, current_app.config["AGENT_REDIS_PREFIX"], current_app.config["AGENT_RAG_CACHE_SECONDS"])
    signature = agent_cache.settings_signature(settings)
    result = cache.get(bot.id, signature, query)  # khóa gồm bot_id: không thể trả kết quả của bot khác
    cached = result is not None
    if result is None:
        retrieval = rag_engine.retrieve(
            bot.id, query, top_k=settings.rag_top_k, distance_threshold=settings.rag_distance_threshold, rerank_top_n=settings.rag_rerank_top_n,
        )
        if retrieval.passages:
            fitted, _ = rag_engine.fit_passages_to_budget(retrieval.passages, settings.rag_max_context_tokens)
            text = builder.render_rag_text(fitted)
        else:
            text = texts(settings.language)["no_context"]
        result = {
            "text": text, "candidate_count": retrieval.candidate_count, "top_distance": retrieval.top_distance,
            "distance_gap": retrieval.distance_gap, "knowledge_empty": retrieval.knowledge_empty,
            "spread_ambiguous": retrieval.spread_is_ambiguous(settings.max_candidate_count),
        }
        cache.put(bot.id, signature, query, result)

    # Mỗi lần agent tra cứu đều được ghi nhận cho lượt (kể cả khi trúng cache): engine gộp tín hiệu ở Bước C
    summary = {"query": query, "cached": cached, **{k: v for k, v in result.items() if k != "text"}}
    key = protocol.Keys(current_app.config["AGENT_REDIS_PREFIX"]).searches(run_id)
    pipe = redis_client.pipeline()
    pipe.rpush(key, json.dumps(summary, ensure_ascii=False))
    pipe.ltrim(key, -MAX_SEARCHES_KEPT, -1)
    pipe.expire(key, SEARCH_RECORD_TTL_SECONDS)
    pipe.execute()
    return jsonify(**result)


def _summary_response(status: str, summary: str | None = None):
    return jsonify(status=status, summary=summary if status in (protocol.SUMMARY_SUMMARIZED, protocol.SUMMARY_NOTHING) else None)


def _record_summary_attempt(run_id: str, status: str, calls: list[dict]) -> None:
    """Ghi kết quả lần gọi để AgentRunner đưa vào agent_executions/trace và cộng usage của lệnh gọi tóm tắt vào giá vốn của lượt."""
    key = protocol.Keys(current_app.config["AGENT_REDIS_PREFIX"]).summaries(run_id)
    pipe = redis_client.pipeline()
    pipe.rpush(key, json.dumps({"status": status, "calls": calls}, ensure_ascii=False))
    pipe.expire(key, SUMMARY_RECORD_TTL_SECONDS)
    pipe.execute()


@bp.route("/conversation/summarize", methods=["POST"])
def conversation_summarize():
    """Công cụ summarize_conversation của agent: tóm tắt NGAY trong lượt (job nền chỉ phục vụ lượt sau). Cùng hàm với job nền
    (jobs.summarize_conversation) nên summary_max_tokens/trần đầu vào/ghi last_summarized_message_id giữ nguyên một chỗ."""
    verified, denied = _verified_run()
    if denied is not None:
        return denied
    _, bot_id, run_id = verified
    keys = protocol.Keys(current_app.config["AGENT_REDIS_PREFIX"])

    raw = redis_client.get(keys.run_context(run_id))
    try:
        context = json.loads(raw) if raw else None
    except ValueError:
        context = None
    conversation_id = context.get("conversation_id") if isinstance(context, dict) else None
    if (not isinstance(context, dict) or context.get("bot_id") != bot_id or context.get("summarize_allowed") is not True
            or isinstance(conversation_id, bool) or not isinstance(conversation_id, int)):
        return _summary_response(protocol.SUMMARY_NOT_ALLOWED)
    if not redis_client.set(keys.summary_used(run_id), "1", nx=True, ex=SUMMARY_RECORD_TTL_SECONDS):
        return _summary_response(protocol.SUMMARY_ALREADY_USED)

    bot = Bot.query.filter_by(id=bot_id).first()
    conversation = Conversation.query.filter_by(id=conversation_id, bot_id=bot_id).first() if bot is not None else None
    if conversation is None:
        return _summary_response(protocol.SUMMARY_NOT_ALLOWED)
    settings = EngineSettings.from_model(dashboard_service.get_or_create_settings(bot))
    if not settings.summary_enabled:
        return _summary_response(protocol.SUMMARY_NOT_ALLOWED)
    state = ConversationState.query.filter_by(conversation_id=conversation.id).first()
    if state is None:  # hội thoại mới (state tạo lười, chưa commit): chưa có tin nào cũ để tóm tắt
        return _summary_response(protocol.SUMMARY_NOTHING)

    lock = jobs.acquire_summary_lock(redis_client, conversation.id)
    if lock is None:
        _record_summary_attempt(run_id, protocol.SUMMARY_BUSY, [])
        return _summary_response(protocol.SUMMARY_BUSY)
    tracker = LLMUsageTracker()
    try:
        db.session.refresh(state)
        updated = jobs.summarize_conversation(state, settings, tracker=tracker)
        status = protocol.SUMMARY_SUMMARIZED if updated else protocol.SUMMARY_NOTHING
        summary = state.summary
    except Exception:
        db.session.rollback()
        logger.exception("summarize_conversation: tóm tắt lỗi (conversation_id=%s), agent tiếp tục không có bản tóm tắt mới", conversation.id)
        _record_summary_attempt(run_id, protocol.SUMMARY_ERROR, tracker.calls)
        return _summary_response(protocol.SUMMARY_ERROR)
    finally:
        jobs.release_summary_lock(redis_client, conversation.id, lock)
    _record_summary_attempt(run_id, status, tracker.calls)
    return _summary_response(status, summary)


@bp.route("/actions/dispatch", methods=["POST"])
def actions_dispatch():
    """Công cụ hành động website của agent (Phase M3): giao lệnh cho widget của khách rồi chờ kết quả thật. Cùng lớp bảo vệ như các route nội bộ khác
    (loopback + token ký theo (bot_id, run_id)); bot_id lấy từ token đã ký; hội thoại/khách lấy từ ngữ cảnh do Flask đặt vào Redis (model không truyền được).
    Toàn bộ điều kiện (đã duyệt, đúng website, giới hạn lượt, thanh toán) kiểm ở app/modules/dispatch.py."""
    verified, denied = _verified_run()
    if denied is not None:
        return denied
    payload, bot_id, run_id = verified
    tool = payload.get("tool")
    if not isinstance(tool, str) or not tool:
        return _denied(400)
    bot = Bot.query.filter_by(id=bot_id).first()
    if bot is None:
        return _denied(404)
    from app.modules import dispatch as action_dispatch  # import trong hàm: tránh kéo module hành động vào lúc nạp blueprint nội bộ

    return jsonify(**action_dispatch.dispatch(bot, run_id=run_id, tool_name=tool, params=payload.get("params")).as_dict())
