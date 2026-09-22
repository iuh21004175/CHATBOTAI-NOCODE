"""Bước B: lệnh gọi DeepSeek chính trả JSON có cấu trúc — parse + validate chặt, gọi lại tối đa 1 lần khi JSON lỗi.

Đã xác minh (2026-09-21, langchain-deepseek 1.1.0 + deepseek-flash, thinking tắt): JSON mode chạy bằng
`response_format={"type": "json_object"}` (tài liệu DeepSeek: prompt phải chứa chữ "json" + ví dụ; API đôi khi trả
content rỗng -> phải có nhánh gọi lại). Không dùng with_structured_output(method="function_calling") vì tool calling
thêm token cho schema mỗi lượt và làm prefix khó cache hơn; json_object + validate tự viết đủ chặt và rẻ hơn.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from core.context_engine.cost import LLMUsageTracker, parse_usage

logger = logging.getLogger("context_engine.structured")

MEMORY_CATEGORIES = ("requirement", "preference", "entity", "constraint", "confirmed_fact")
MAX_KEY_CHARS = 100
MAX_VALUE_CHARS = 500
MAX_JSON_ATTEMPTS = 2  # lần đầu + gọi lại 1 lần


class StructuredOutputError(ValueError):
    """JSON không đúng hợp đồng (sau khi đã gọi lại)."""


@dataclass
class LLMReply:
    content: str
    token_usage: dict | None = None


# Hàm gọi LLM: nhận danh sách [{"role","content"}] -> LLMReply. Tách ra để test không cần mạng.
LLMCall = Callable[[list[dict]], LLMReply]


def deepseek_call(temperature: float, max_tokens: int) -> LLMCall:
    """LLMCall thật: ChatDeepSeek (core.llm_client — thinking đã tắt) + JSON mode."""
    from core.llm_client import get_llm

    llm = get_llm(temperature, max_tokens).bind(response_format={"type": "json_object"})

    def call(messages: list[dict]) -> LLMReply:
        response = llm.invoke(messages)
        content = response.content if isinstance(response.content, str) else ""
        metadata = getattr(response, "response_metadata", None) or {}
        return LLMReply(content=content, token_usage=metadata.get("token_usage"))

    return call


@dataclass
class StructuredOutput:
    intent: str | None
    intent_confidence: float | None
    slots: dict
    memory_updates: list[dict]
    needs_history_lookup: bool
    self_assessed_confidence: float | None
    proposed_answer: str
    proposed_clarification_question: str
    dropped_memory_items: int = 0
    raw: str = field(default="", repr=False)


_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    fenced = _FENCE_RE.match(text)
    return fenced.group(1) if fenced else text


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _optional_unit(data: dict, key: str) -> float | None:
    """Số trong [0, 1] hoặc thiếu/null. Sai kiểu/ngoài khoảng là lỗi hợp đồng (không ép về khoảng để vượt lỗi)."""
    value = data.get(key)
    if value is None:
        return None
    if not _is_number(value) or not 0.0 <= value <= 1.0:
        raise StructuredOutputError(f"{key} phải là số trong [0, 1], nhận {value!r}")
    return float(value)


def _optional_text(data: dict, key: str) -> str:
    value = data.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise StructuredOutputError(f"{key} phải là chuỗi, nhận {type(value).__name__}")
    return value.strip()


def _clean_memory_item(item) -> dict | None:
    """Mục bộ nhớ hợp lệ hoặc None. 1 mục hỏng không làm hỏng cả câu trả lời (chỉ mất mục đó, có đếm trong trace)."""
    if not isinstance(item, dict):
        return None
    category, key, value, confidence = item.get("category"), item.get("key"), item.get("value"), item.get("confidence")
    if category not in MEMORY_CATEGORIES or not isinstance(key, str) or not key.strip():
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        return None
    if not _is_number(confidence) or not 0.0 <= confidence <= 1.0:
        return None
    return {
        "category": category,
        "key": key.strip()[:MAX_KEY_CHARS],
        "value": value.strip()[:MAX_VALUE_CHARS],
        "confidence": float(confidence),
    }


def parse_structured_output(text: str) -> StructuredOutput:
    payload = _extract_json(text)
    if not payload:
        raise StructuredOutputError("phản hồi rỗng")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as e:
        raise StructuredOutputError(f"không phải JSON hợp lệ: {e.msg}") from e
    if not isinstance(data, dict):
        raise StructuredOutputError("JSON gốc phải là đối tượng")

    intent = data.get("intent")
    if intent is not None and not isinstance(intent, str):
        raise StructuredOutputError("intent phải là chuỗi")
    slots = data.get("slots", {})
    if slots is None:
        slots = {}
    if not isinstance(slots, dict):
        raise StructuredOutputError("slots phải là đối tượng")
    raw_memory = data.get("memory_updates", [])
    if raw_memory is None:
        raw_memory = []
    if not isinstance(raw_memory, list):
        raise StructuredOutputError("memory_updates phải là danh sách")
    needs_lookup = data.get("needs_history_lookup", False)
    if not isinstance(needs_lookup, bool):
        raise StructuredOutputError("needs_history_lookup phải là true/false")

    memory = [cleaned for cleaned in map(_clean_memory_item, raw_memory) if cleaned]
    output = StructuredOutput(
        intent=(intent.strip() or None) if isinstance(intent, str) else None,
        intent_confidence=_optional_unit(data, "intent_confidence"),
        slots={str(k): v for k, v in slots.items()},
        memory_updates=memory,
        needs_history_lookup=needs_lookup,
        self_assessed_confidence=_optional_unit(data, "self_assessed_confidence"),
        proposed_answer=_optional_text(data, "proposed_answer"),
        proposed_clarification_question=_optional_text(data, "proposed_clarification_question"),
        dropped_memory_items=len(raw_memory) - len(memory),
        raw=payload,
    )
    if not output.proposed_answer and not output.proposed_clarification_question:
        raise StructuredOutputError("cả proposed_answer và proposed_clarification_question đều rỗng")
    return output


def call_structured(messages: list[dict], call: LLMCall, tracker: LLMUsageTracker, *, kind: str = "main") -> StructuredOutput:
    """Gọi LLM và parse. JSON lỗi thì gọi lại đúng 1 lần (ghi là "retry"); lỗi lần 2 ném StructuredOutputError để route
    trả 502 như mọi lỗi LLM khác. Lỗi mạng/API ném nguyên (không nuốt). Usage của MỌI lần gọi đều được ghi."""
    last_error: StructuredOutputError | None = None
    retry_kind = "retry" if kind == "main" else f"{kind}_retry"  # lần gọi lại của lệnh phụ vẫn là lệnh phụ
    for attempt in range(MAX_JSON_ATTEMPTS):
        reply = call(messages)
        tracker.record(kind if attempt == 0 else retry_kind, parse_usage(reply.token_usage))
        try:
            return parse_structured_output(reply.content)
        except StructuredOutputError as e:
            last_error = e
            logger.warning("JSON có cấu trúc không hợp lệ (lần %d/%d, %s): %s", attempt + 1, MAX_JSON_ATTEMPTS, kind, e)
    raise StructuredOutputError(f"LLM không trả JSON hợp lệ sau {MAX_JSON_ATTEMPTS} lần: {last_error}")
