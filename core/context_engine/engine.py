"""Bộ điều phối 1 lượt trả lời (Bước A -> B -> C -> [F] -> phần tính của D). KHÔNG đụng DB, KHÔNG commit — nhận trạng thái
dưới dạng ảnh chụp và trả về kết quả để tầng service ghi (app/dashboard/service.py). Nhờ vậy khung chat thử ở Bước 1 (không
lưu hội thoại) và widget thật dùng chung đúng 1 luồng quyết định.

  A (backend, không gọi LLM): tin gần đây + summary + memory; RAG (khoảng cách, spread, candidate_count); áp lực ngữ cảnh + nén
  B (ĐÚNG 1 lệnh gọi DeepSeek chính): JSON có cấu trúc (gọi lại 1 lần chỉ khi JSON lỗi — ghi riêng là "retry")
  C (backend): cây quyết định ANSWER / CLARIFY / DECLINE
  F (hiếm): needs_history_lookup=true -> tìm lại lịch sử cùng hội thoại -> lệnh gọi LLM phụ, ghi riêng ("history_lookup").
     Chạy TRƯỚC khi lưu tin trả lời (Bước D): nếu chạy sau, tin đã lưu/đã phát tới khách phải sửa lại.
  D (phần tính): nội dung trả lời, trạng thái mới, memory, decision_trace, usage — service ghi vào DB.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from dataclasses import dataclass

from core import rag_engine
from core.context_engine import builder, decision as decision_mod, history_retrieval, state
from core.context_engine.cost import LLMUsageTracker, Usage
from core.context_engine.settings import JSON_OVERHEAD_TOKENS, EngineSettings
from core.context_engine.structured import LLMCall, StructuredOutput, call_structured, deepseek_call

logger = logging.getLogger("context_engine.engine")


@dataclass
class TurnRequest:
    bot_id: int
    question: str
    settings: EngineSettings
    snapshot: state.ConversationSnapshot
    intents: list[state.IntentSpec]
    recent_rows: list[builder.RecentRow]  # cũ -> mới, KHÔNG gồm câu hỏi hiện tại
    conversation_id: int | None = None  # None = khung chat thử không lưu hội thoại -> không có Historical Retrieval


@dataclass
class TurnResult:
    decision: decision_mod.Decision
    reply: str
    output: StructuredOutput
    state_update: state.StateUpdate
    trace: dict
    usage: Usage  # lệnh gọi DeepSeek chính (gồm lần gọi lại do JSON lỗi)
    extra_calls: list[dict]  # lệnh gọi phụ (history_lookup)


def _r(value, digits: int = 4):
    return None if value is None else round(float(value), digits)


def run_turn(
    request: TurnRequest,
    *,
    llm_call: LLMCall | None = None,
    retrieve_fn=None,
    history_search_fn=None,
) -> TurnResult:
    """retrieve_fn/history_search_fn/llm_call: điểm tiêm phụ thuộc cho test; mặc định là RAG, Chroma và DeepSeek thật
    (phân giải lúc gọi, không phải lúc định nghĩa hàm, để có thể patch)."""
    retrieve_fn = retrieve_fn or rag_engine.retrieve
    history_search_fn = history_search_fn or history_retrieval.search_history
    settings = request.settings
    tracker = LLMUsageTracker()

    # ---- A. Tín hiệu backend, tất cả tính TRƯỚC khi gọi LLM ----
    recent = builder.RecentMessageSelector(settings.recent_message_limit, settings.recent_token_limit).select(request.recent_rows)

    retrieval = rag_engine.RetrievalResult()
    if settings.rag_enabled:
        retrieval = retrieve_fn(
            request.bot_id,
            request.question,
            top_k=settings.rag_top_k,
            distance_threshold=settings.rag_distance_threshold,
            rerank_top_n=settings.rag_rerank_top_n,
        )
    # Bot chưa có tri thức nào (hoặc tắt Knowledge Base): không có ngữ cảnh RAG nào để "dưới ngưỡng", nên nhánh
    # candidate_count == 0 / spread không áp dụng — quy tắc "không bịa" trong prompt vẫn còn hiệu lực.
    rag_used = settings.rag_enabled and not retrieval.knowledge_empty

    # Chỉ được hỏi thu hẹp khi còn lượt hỏi làm rõ; hết lượt (hoặc tắt hỏi làm rõ) thì đưa các chunk liên quan nhất còn vừa
    # ngân sách và trả lời — không hỏi vô hạn.
    plan = builder.build_plan(
        settings, request.intents, request.snapshot, recent, retrieval.passages, request.question,
        allow_scope_narrowing=rag_used and decision_mod.can_clarify(settings, request.snapshot.clarification_turns_used),
    )
    pressure_before = plan.pressure
    level = builder.pressure_level(pressure_before, settings)
    builder.compress(plan, level)
    # Ngân sách cạn nên không đoạn nào vào được prompt: nhường chỗ bằng cách bỏ thêm tin cũ (xem ensure_rag_room)
    builder.ensure_rag_room(plan, retrieval.passages)
    compression = list(plan.compression_steps)
    # Vẫn không còn chỗ (system + memory + summary + dự phòng đầu ra đã ăn hết ngân sách): bot sẽ không thấy tài liệu nào,
    # nên phải xử lý như "không có ngữ cảnh" (nhánh 1) thay vì trả lời như thể đã có thông tin.
    context_unusable = bool(retrieval.passages) and not plan.passages
    messages = builder.MessageBuilder.build(plan)

    # ---- B. Đúng 1 lệnh gọi DeepSeek chính ----
    call = llm_call or deepseek_call(settings.temperature, settings.max_tokens + JSON_OVERHEAD_TOKENS)
    started = time.monotonic()
    output = call_structured(messages, call, tracker)
    llm_seconds = time.monotonic() - started

    # ---- C. Cây quyết định ----
    update = state.resolve_state(request.snapshot, output, request.intents, settings)
    signals = decision_mod.Signals(
        rag_used=rag_used,
        candidate_count=0 if context_unusable else retrieval.candidate_count,
        top_distance=retrieval.top_distance,
        distance_gap=retrieval.distance_gap,
        spread_ambiguous=retrieval.spread_is_ambiguous(settings.max_candidate_count),
        context_pressure=pressure_before,
        context_compressed=bool(compression),
        intent_confidence=output.intent_confidence,
        slot_completion=update.slot_completion,
        has_required_slots=bool(update.required_slots),
        clarification_turns_used=request.snapshot.clarification_turns_used,
        is_ambiguous_reference=decision_mod.is_ambiguous_reference(request.question),
        scope_narrowing=plan.scope_narrowing,
    )
    result = decision_mod.decide(signals, settings, output)
    if context_unusable:
        result.reasons.append("context_budget_exhausted")

    # ---- F. Ngoại lệ duy nhất được gọi LLM lần 2 ----
    history_trace = None
    if output.needs_history_lookup and result.decision == decision_mod.Decision.ANSWER and request.conversation_id is not None:
        output, history_trace = _history_lookup(request, plan, output, call, tracker, history_search_fn)

    # ---- D (phần tính) ----
    reply = decision_mod.resolve_reply_text(result, output, settings)
    update = state.finalize_turns(update, request.snapshot, result.decision == decision_mod.Decision.CLARIFY)

    trace = {
        "decision": result.decision.value,
        "intent": update.current_intent,
        "intent_confidence": _r(output.intent_confidence),
        "slot_completion": _r(update.slot_completion),
        "top_retrieval_distance": _r(retrieval.top_distance),
        "distance_gap": _r(retrieval.distance_gap),
        "candidate_count": retrieval.candidate_count,
        "context_pressure": _r(pressure_before),
        "clarification_turns_used": update.clarification_turns_used,
        "reasons": result.reasons,
        # ---- mở rộng (ngoài khung tối thiểu của đặc tả, để dựng màn xem lại sau mà không phải backfill) ----
        "intent_changed": update.intent_changed,
        "is_ambiguous_reference": signals.is_ambiguous_reference,
        "pressure_level": level,
        "context_pressure_after_compression": _r(plan.pressure),
        "compression_steps": compression,
        "input_tokens_estimate": plan.input_tokens,
        "self_assessed_confidence": _r(output.self_assessed_confidence),
        "needs_history_lookup": output.needs_history_lookup if history_trace is None else True,
        "forced_answer": result.forced_answer,
        "rag": {
            "enabled": settings.rag_enabled,
            "knowledge_empty": retrieval.knowledge_empty,
            "considered": retrieval.considered,
            "over_threshold": retrieval.over_threshold,
            "duplicates_dropped": retrieval.duplicates_dropped,
            "passages_in_prompt": len(plan.passages),
            "rag_tokens": plan.rag_tokens,
            "distance_threshold": settings.rag_distance_threshold,
        },
        "context_overflow": plan.overflow_info,
        "memory_items_dropped_invalid": output.dropped_memory_items,
        "main_llm_calls": tracker.main_call_count,
        "llm_seconds": _r(llm_seconds, 2),
        "history_lookup": history_trace,
        "extra_llm_calls": tracker.extra_calls,
    }
    return TurnResult(
        decision=result.decision, reply=reply, output=output, state_update=update, trace=trace,
        usage=tracker.main_usage, extra_calls=tracker.extra_calls,
    )


def _history_lookup(request, plan, output, call, tracker, history_search_fn):
    """Bước F. Trả (output có thể đã được hoàn thiện, phần ghi vào trace). Không tìm thấy gì -> không gọi LLM lần 2
    (không có thông tin mới thì gọi thêm chỉ tốn tiền). Lệnh gọi phụ hỏng -> vẫn dùng câu trả lời hợp lệ của lệnh
    gọi chính (đã qua cây quyết định) và ghi lỗi vào log + trace, không làm mất câu trả lời của khách."""
    settings = request.settings
    exclude = {message_id for turn in plan.recent for message_id in turn.ids}  # đã nằm nguyên văn trong prompt
    hits = history_search_fn(
        request.bot_id,
        request.conversation_id,
        request.question,
        exclude_message_ids=exclude,
        distance_threshold=settings.rag_distance_threshold,
    )
    trace = {"hits": len(hits), "called_llm": False}
    if not hits:
        return output, trace
    context = history_retrieval.format_history_context(hits, settings.language)
    try:
        second = call_structured(builder.MessageBuilder.build(plan, history_context=context), call, tracker, kind="history_lookup")
    except Exception:
        logger.exception("history_lookup: lệnh gọi LLM phụ lỗi, dùng câu trả lời của lệnh gọi chính")
        trace.update(called_llm=True, status="error")
        return output, trace
    trace.update(called_llm=True, status="ok")
    if not second.proposed_answer:
        trace["status"] = "empty_answer"
        return output, trace
    return dataclasses.replace(
        output,
        proposed_answer=second.proposed_answer,
        self_assessed_confidence=second.self_assessed_confidence
        if second.self_assessed_confidence is not None
        else output.self_assessed_confidence,
    ), trace
