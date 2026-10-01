"""Việc nền của Decision Engine (KHÔNG chạy trong request của khách): rolling summary + embed lịch sử chat.

Chạy bởi workers/context_jobs.py theo đúng pattern workers/process_documents.py: DB là hàng đợi (cờ
conversation_state.summary_pending; tin chưa có dòng conversation_message_embeddings), 1 worker tại 1 thời điểm (khoá
Redis), việc idempotent nên worker chết giữa chừng thì lần sau làm lại an toàn.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from core import rag_engine
from core.context_engine import history_retrieval
from core.context_engine.cost import LLMUsageTracker, parse_usage
from core.context_engine.prompts import texts
from core.context_engine.settings import EngineSettings
from core.context_engine.structured import LLMReply

logger = logging.getLogger("context_engine.jobs")

EMBED_BATCH = 16
MAX_SUMMARY_INPUT_TOKENS = 12000  # 1 lượt tóm tắt xử lý tối đa chừng này token tin mới; phần còn lại để lượt sau
SUMMARY_MARGIN_TOKENS = 100  # LLM hay viết lố nhẹ so với "tối đa khoảng N token"
SUMMARY_LOCK_TTL_SECONDS = 120  # dài hơn 1 lệnh gọi tóm tắt; chết giữa chừng thì tự nhả

_RELEASE_LOCK = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


def summary_lock_key(conversation_id: int) -> str:
    return f"context-jobs:summary-lock:{conversation_id}"


def acquire_summary_lock(redis, conversation_id: int) -> str | None:
    """Khóa MỖI HỘI THOẠI khi đang tóm tắt: việc nền và công cụ tóm tắt của agent (agent/…/internal) cùng đọc-tính-ghi 1 bản tóm tắt,
    không khóa thì 2 bên cùng gọi LLM (tốn gấp đôi) và bản ghi sau đè bản trước. Trả token của chủ khóa, None nếu đang bị giữ."""
    token = uuid.uuid4().hex
    return token if redis.set(summary_lock_key(conversation_id), token, nx=True, ex=SUMMARY_LOCK_TTL_SECONDS) else None


def release_summary_lock(redis, conversation_id: int, token: str) -> None:
    """Chỉ chủ khóa mới nhả được (khóa đã hết hạn và người khác giữ thì không xóa nhầm)."""
    redis.eval(_RELEASE_LOCK, 1, summary_lock_key(conversation_id), token)


def summary_llm_call(max_tokens: int):
    """LLM thật cho tóm tắt: văn bản thường (không JSON mode), thinking tắt như mọi lệnh gọi khác."""
    from core.llm_client import get_llm

    llm = get_llm(0.2, max_tokens)

    def call(messages: list[dict]) -> LLMReply:
        response = llm.invoke(messages)
        content = response.content if isinstance(response.content, str) else ""
        return LLMReply(content=content, token_usage=(getattr(response, "response_metadata", None) or {}).get("token_usage"))

    return call


def _label(sender: str, t: dict) -> str:
    return t["bot"] if sender == "bot" else t["staff"] if sender == "staff" else t["customer"]


def build_summary_messages(old_summary: str | None, rows: list, settings: EngineSettings) -> list[dict]:
    t = texts(settings.language)
    lines = "\n".join(f"{_label(m.sender, t)}: {m.content.strip()}" for m in rows)
    body = (
        f"<tom_tat_cu>\n{(old_summary or '').strip() or t['no_summary']}\n</tom_tat_cu>\n\n"
        f"<tin_nhan_moi>\n{lines}\n</tin_nhan_moi>"
    )
    return [
        {"role": "system", "content": t["summary_prompt"].format(max_tokens=settings.summary_max_tokens)},
        {"role": "user", "content": f"{t['summary_old']} / {t['summary_new']}\n\n{body}"},
    ]


def messages_to_summarize(state, settings: EngineSettings):
    """Tin CHƯA tóm tắt và CŨ HƠN cửa sổ tin gần đây (recent_message_limit tin không-nhân-viên mới nhất): cửa sổ đó
    luôn được đưa nguyên văn vào prompt nên tóm tắt chúng chỉ tốn token mà không thêm thông tin."""
    from app.models import Message

    newest = (
        Message.query.filter(Message.conversation_id == state.conversation_id, Message.sender != "staff")
        .order_by(Message.id.desc())
        .limit(settings.recent_message_limit)
        .all()
    )
    window_start = min((m.id for m in newest), default=None)
    if window_start is None:
        return []
    rows = (
        Message.query.filter(
            Message.conversation_id == state.conversation_id,
            Message.id > (state.last_summarized_message_id or 0),
            Message.id < window_start,
        )
        .order_by(Message.id.asc())
        .all()
    )
    if not rows:
        return []
    counts = rag_engine.count_tokens_many([m.content for m in rows])
    chosen, used = [], 0
    for row, tokens in zip(rows, counts):
        if chosen and used + tokens > MAX_SUMMARY_INPUT_TOKENS:
            break
        chosen.append(row)
        used += tokens
    return chosen


def summarize_conversation(state, settings: EngineSettings, call=None, tracker: LLMUsageTracker | None = None) -> bool:
    """Tóm tắt lũy tiến: (summary cũ + tin mới) -> summary mới (<= summary_max_tokens), ghi đè và tiến
    last_summarized_message_id. Trả True nếu đã cập nhật summary. Lỗi LLM/API được ném nguyên (worker ghi log + chờ
    rồi thử lại; cờ summary_pending giữ nguyên)."""
    from extensions import db

    if not settings.summary_enabled:
        state.summary_pending = False
        db.session.commit()
        return False
    rows = messages_to_summarize(state, settings)
    if not rows:
        state.summary_pending = False  # chưa có tin nào nằm ngoài cửa sổ gần đây để tóm tắt
        db.session.commit()
        return False

    tracker = tracker or LLMUsageTracker()
    call = call or summary_llm_call(settings.summary_max_tokens + SUMMARY_MARGIN_TOKENS)
    reply = call(build_summary_messages(state.summary, rows, settings))
    tracker.record("summary", parse_usage(reply.token_usage), conversation_id=state.conversation_id, bot_id=state.bot_id)
    summary = (reply.content or "").strip()
    if not summary:
        raise RuntimeError("LLM không trả nội dung tóm tắt")

    state.summary = summary
    state.last_summarized_message_id = rows[-1].id
    state.summary_updated_at = datetime.utcnow()
    # Còn tin cũ chưa tóm tắt (do trần MAX_SUMMARY_INPUT_TOKENS) thì giữ cờ để lượt sau làm tiếp
    state.summary_pending = bool(messages_to_summarize(state, settings))
    db.session.commit()
    return True


def embed_pending_messages(limit: int = EMBED_BATCH) -> int:
    """Embed các tin (khách + bot) chưa có dòng trong conversation_message_embeddings vào history_<bot_id>. Chroma
    upsert trước, ghi dòng đánh dấu sau -> chết giữa chừng thì lần sau upsert lại (idempotent). Trả số tin đã xử lý."""
    from app.models import Conversation, ConversationMessageEmbedding, Message
    from extensions import db

    pending = (
        db.session.query(Message, Conversation.bot_id)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .outerjoin(ConversationMessageEmbedding, ConversationMessageEmbedding.message_id == Message.id)
        .filter(ConversationMessageEmbedding.id.is_(None), Message.sender.in_(("customer", "bot")))
        .order_by(Message.id.asc())
        .limit(limit)
        .all()
    )
    if not pending:
        return 0
    by_bot: dict[int, list] = {}
    for message, bot_id in pending:
        by_bot.setdefault(bot_id, []).append(message)
    for bot_id, messages in by_bot.items():
        history_retrieval.index_messages(bot_id, [
            {
                "message_id": m.id, "conversation_id": m.conversation_id, "sender": m.sender,
                "content": m.content, "created_at": m.created_at,
            }
            for m in messages
        ])
        for m in messages:  # tin rỗng cũng đánh dấu để không quét lại mãi
            db.session.add(ConversationMessageEmbedding(message_id=m.id, bot_id=bot_id, conversation_id=m.conversation_id))
    db.session.commit()
    return len(pending)
