"""Phase 4 — Decision Engine: cây quyết định tường minh (Bước C), không gọi LLM.

`decide()` là hàm THUẦN: chỉ nhận tín hiệu đã tính xong + cấu hình + output của LLM, trả quyết định và lý do. Nhờ vậy
mỗi nhánh của cây được test độc lập bằng số liệu giả (tests/test_decision.py).

Về "context_pressure quá cao" (nhánh 5 của đặc tả): nén ngữ cảnh phải xảy ra TRƯỚC lệnh gọi LLM (Bước A, xem
builder.compress) vì đó mới là thứ quyết định số token gửi đi; đến Bước C thì việc nén đã xong. Vì vậy kích thước
ngữ cảnh không bao giờ là lý do để CLARIFY ở đây — chỉ được ghi vào trace ("context_compressed").
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

from core.context_engine.prompts import texts
from core.context_engine.settings import EngineSettings
from core.context_engine.structured import StructuredOutput


class Decision(str, Enum):
    ANSWER = "answer"
    CLARIFY = "clarify"
    DECLINE = "decline"


@dataclass
class Signals:
    """Tín hiệu tính xong TRƯỚC khi gọi LLM (Bước A) cộng với phần LLM trả về (Bước B)."""

    rag_used: bool  # bot bật Knowledge Base và đã có ít nhất 1 chunk trong kho
    candidate_count: int
    top_distance: float | None
    distance_gap: float | None
    spread_ambiguous: bool  # nhiều nguồn cùng liên quan ngang nhau (RetrievalResult.spread_is_ambiguous)
    context_pressure: float
    context_compressed: bool
    intent_confidence: float | None
    slot_completion: float
    has_required_slots: bool
    clarification_turns_used: int
    is_ambiguous_reference: bool = False
    scope_narrowing: bool = False  # chunk tìm được vượt ngân sách token -> chỉ đưa phần mở đầu và yêu cầu khách thu hẹp phạm vi


@dataclass
class DecisionResult:
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    text_source: str = "proposed_answer"  # proposed_answer | proposed_clarification_question | configured_clarify | configured_decline
    forced_answer: bool = False  # CLARIFY bị ép thành ANSWER do hết lượt/tắt hỏi làm rõ


# ---- AmbiguityDetector ----

_AMBIGUOUS_PATTERNS = [
    r"\bcai\s+(nay|kia|do|no|nao|vua\s+roi)\b",
    r"\bloai\s+nao\b",
    r"\bmau\s+(tren|nay|do|kia|nao)\b",
    r"\bcon\s+cai\s+(do|nay|kia)\b",
    r"\b(nhu|o|ben)\s+tren\b",
    r"\bthe\s+con\b",
    r"\bcai\s+dau\b",
    r"\b(no|chung)\s+(co|la|the\s+nao|bao\s+nhieu)\b",
]
_AMBIGUOUS_RE = re.compile("|".join(_AMBIGUOUS_PATTERNS))


def strip_diacritics(text: str) -> str:
    """'Cái này' -> 'cai nay' (khách hay gõ không dấu: đo trước đây cho thấy cả bộ dữ liệu mẫu có loại câu này)."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def is_ambiguous_reference(question: str) -> bool:
    """Heuristic regex nhận diện cụm chỉ định mơ hồ ("cái này", "loại nào", "mẫu trên"...). CHỈ gắn cờ vào
    decision_trace, KHÔNG tự resolve — LLM đã có recent messages + memory ở Bước B để tự hiểu."""
    return bool(_AMBIGUOUS_RE.search(strip_diacritics(question or "")))


# ---- ClarificationController ----

def can_clarify(settings: EngineSettings, turns_used: int) -> bool:
    """Hỏi làm rõ khi được bật và chưa hết max_clarification_turns lượt LIÊN TIẾP (mỗi lượt CLARIFY chỉ có đúng 1 câu hỏi vì
    LLM chỉ trả 1 proposed_clarification_question) — không hỏi lại vô hạn."""
    return settings.clarification_enabled and turns_used < settings.max_clarification_turns


# ---- Answerability Engine ----

def decide(signals: Signals, settings: EngineSettings, output: StructuredOutput) -> DecisionResult:
    reasons: list[str] = []
    if signals.context_compressed:
        reasons.append("context_compressed")
    if signals.is_ambiguous_reference:
        reasons.append("ambiguous_reference")
    clarify_allowed = can_clarify(settings, signals.clarification_turns_used)

    # 1. Không có chunk nào đạt ngưỡng liên quan -> KHÔNG dùng proposed_answer của LLM (không trả lời tự tin dựa trên
    #    ngữ cảnh dưới ngưỡng), dùng câu chủ bot cấu hình sẵn.
    if signals.rag_used and signals.candidate_count == 0:
        reasons.append("no_relevant_context")
        if settings.low_confidence_reply_mode == "ask_clarify" and clarify_allowed:
            return DecisionResult(Decision.CLARIFY, reasons, "configured_clarify")
        if settings.low_confidence_reply_mode == "ask_clarify":
            reasons.append("clarification_limit_reached" if settings.clarification_enabled else "clarification_disabled")
        return DecisionResult(Decision.DECLINE, reasons, "configured_decline")

    # 2-4. Thiếu thông tin để trả lời chính xác -> hỏi làm rõ (nhánh nào khớp trước thì dùng nhánh đó)
    wants_clarify = None
    if signals.scope_narrowing:
        # Bước A đã đưa cho LLM chỉ phần mở đầu của từng chunk và yêu cầu 1 câu hỏi thu hẹp phạm vi -> hỏi tiếp cho tới khi
        # nội dung tìm được vừa ngân sách (lượt sau khách nêu rõ hơn, tìm lại, vừa thì trả lời trực tiếp)
        wants_clarify = "context_exceeds_budget"
    elif (
        settings.intent_tracking_enabled
        and signals.intent_confidence is not None
        and signals.intent_confidence < settings.intent_confidence_threshold
    ):
        wants_clarify = "low_intent_confidence"
    elif settings.slot_filling_enabled and signals.has_required_slots and signals.slot_completion < settings.slot_completion_threshold:
        wants_clarify = "missing_required_slots"
    elif signals.rag_used and signals.spread_ambiguous:
        wants_clarify = "too_many_relevant_candidates"

    if wants_clarify:
        reasons.append(wants_clarify)
        if clarify_allowed:
            return DecisionResult(Decision.CLARIFY, reasons, "proposed_clarification_question")
        # Hết lượt / tắt hỏi làm rõ: ép trả lời thay vì hỏi vô hạn
        reasons.append("clarification_limit_reached" if settings.clarification_enabled else "clarification_disabled")
        return _answer_or_fallback(output, reasons, settings, signals, forced=True)

    return _answer_or_fallback(output, reasons, settings, signals, forced=False)


def _answer_or_fallback(
    output: StructuredOutput, reasons: list[str], settings: EngineSettings, signals: Signals, *, forced: bool
) -> DecisionResult:
    if output.proposed_answer:
        return DecisionResult(Decision.ANSWER, reasons, "proposed_answer", forced_answer=forced)
    # LLM tự cho là chưa đủ thông tin (chỉ đưa câu hỏi làm rõ) dù mọi tín hiệu backend đều ổn
    reasons.append("empty_proposed_answer")
    if output.proposed_clarification_question and can_clarify(settings, signals.clarification_turns_used):
        return DecisionResult(Decision.CLARIFY, reasons, "proposed_clarification_question")
    return DecisionResult(Decision.DECLINE, reasons, "configured_decline")


def resolve_reply_text(result: DecisionResult, output: StructuredOutput, settings: EngineSettings) -> str:
    """Nội dung gửi cho khách theo quyết định. Câu chủ bot cấu hình trống thì dùng câu mặc định theo ngôn ngữ."""
    t = texts(settings.language)
    if result.text_source == "configured_decline":
        return settings.low_confidence_decline_message or t["default_decline"]
    if result.text_source == "configured_clarify":
        return settings.low_confidence_clarify_message or t["default_clarify"]
    if result.text_source == "proposed_clarification_question":
        return output.proposed_clarification_question or settings.low_confidence_clarify_message or t["default_clarify"]
    answer = output.proposed_answer
    if result.forced_answer and (output.self_assessed_confidence is None or output.self_assessed_confidence < 0.5):
        answer = f"{answer}\n\n{t['low_confidence_note']}"
    return answer
