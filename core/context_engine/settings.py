"""Cấu hình hiệu lực của Decision Engine cho 1 lượt trả lời.

Nguồn duy nhất cho: giá trị mặc định (DEFAULTS), khoảng hợp lệ (RANGES) và các công tắc bật/tắt tính năng cố định
(FIXED_TOGGLES). Cột trong app/models.py:BotSettings có server_default trùng DEFAULTS (test_settings kiểm tra khớp).

Các công tắc bật/tắt tính năng (rag_enabled, summary_enabled, structured_memory_enabled, intent_tracking_enabled,
slot_filling_enabled, clarification_enabled) không còn cho chủ bot chỉnh: hệ thống đã cố định giá trị vận hành
(FIXED_TOGGLES) áp dụng cho mọi bot, bất kể giá trị đang lưu trong DB (cột DB vẫn còn nhưng không còn được đọc).
Mọi thông số còn lại (RANGES) luôn có thể chỉnh — không còn khái niệm "mức cấu hình" giới hạn trường nào hiện/ẩn.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

DEFAULT_LANGUAGE = "vi"

DEFAULTS: dict = {
    "rag_enabled": True,
    "recent_message_limit": 10,
    "recent_token_limit": 2000,
    "summary_enabled": True,
    "summary_trigger_tokens": 4000,
    "summary_max_tokens": 500,
    "structured_memory_enabled": True,
    "memory_max_items": 30,
    "memory_min_confidence": 0.70,
    "intent_tracking_enabled": True,
    "intent_confidence_threshold": 0.70,
    "slot_filling_enabled": True,
    "slot_completion_threshold": 0.80,
    "rag_top_k": 8,
    "rag_rerank_top_n": 5,
    # Bình phương L2 của Chroma (d = 2·(1−cos) trên vector đã chuẩn hóa), KHÔNG phải thang cosine 0-1.
    # 1,50 ≡ cosine 0,25 — đúng ngưỡng đã đo trước đây (min_similarity). Đo trên dữ liệu thật: câu hỏi đúng chủ đề
    # d ≈ 1,19-1,48, lạc đề d ≥ 1,58 → gợi ý mặc định 0,70 (cosine 0,65) sẽ loại gần như mọi chunk.
    "rag_distance_threshold": 1.50,
    "rag_max_context_tokens": 3000,
    "max_candidate_count": 5,
    "clarification_enabled": True,
    "context_pressure_warning": 0.80,
    "context_pressure_hard_limit": 0.90,
    "low_confidence_reply_mode": "ask_clarify",
    "low_confidence_decline_message": None,
    "low_confidence_clarify_message": None,
    "max_context_tokens": 8000,
}

# (kiểu, nhỏ nhất, lớn nhất) — dùng để kiểm tra form (báo lỗi) và để ép về khoảng hợp lệ khi đọc dữ liệu cũ/sai
RANGES: dict[str, tuple[type, float, float]] = {
    "recent_message_limit": (int, 1, 30),
    "recent_token_limit": (int, 200, 8000),
    "summary_trigger_tokens": (int, 500, 20000),
    "summary_max_tokens": (int, 100, 2000),
    "memory_max_items": (int, 1, 100),
    "memory_min_confidence": (float, 0.0, 1.0),
    "intent_confidence_threshold": (float, 0.0, 1.0),
    "slot_completion_threshold": (float, 0.0, 1.0),
    "rag_top_k": (int, 1, 20),
    "rag_rerank_top_n": (int, 1, 20),
    "rag_distance_threshold": (float, 0.20, 1.90),
    "rag_max_context_tokens": (int, 200, 8000),
    "max_candidate_count": (int, 1, 20),
    "context_pressure_warning": (float, 0.30, 0.95),
    "context_pressure_hard_limit": (float, 0.50, 0.99),
    "max_context_tokens": (int, 2000, 100000),
}

REPLY_MODES = ("decline", "ask_clarify")

# Giá trị cố định của các công tắc bật/tắt tính năng — không còn đọc từ DB/form. "structured_memory_enabled" tắt hẳn
# vì trùng chức năng với "summary_enabled" (tóm tắt hội thoại dài đã đủ để AI không quên phần đầu hội thoại).
FIXED_TOGGLES: dict[str, bool] = {
    "rag_enabled": True,
    "summary_enabled": True,
    "structured_memory_enabled": False,
    "intent_tracking_enabled": True,
    "slot_filling_enabled": True,
    "clarification_enabled": True,
}

# Ngân sách JSON có cấu trúc (intent, slots, memory_updates, confidence...) cộng thêm vào max_tokens của câu trả lời;
# thiếu phần này JSON bị cắt ngang ở max_tokens -> không parse được.
JSON_OVERHEAD_TOKENS = 600


def clamp(name: str, value):
    """Ép giá trị đọc từ DB về đúng kiểu + khoảng hợp lệ; không phải số thì dùng mặc định."""
    kind, low, high = RANGES[name]
    try:
        number = kind(value)
    except (TypeError, ValueError):
        return DEFAULTS[name]
    if number != number:  # NaN
        return DEFAULTS[name]
    return kind(min(max(number, low), high))


@dataclass(frozen=True)
class EngineSettings:
    language: str
    instructions: str
    temperature: float
    max_tokens: int  # token đầu ra của CÂU TRẢ LỜI (chưa gồm JSON_OVERHEAD_TOKENS)
    rag_enabled: bool
    recent_message_limit: int
    recent_token_limit: int
    summary_enabled: bool
    summary_trigger_tokens: int
    summary_max_tokens: int
    structured_memory_enabled: bool
    memory_max_items: int
    memory_min_confidence: float
    intent_tracking_enabled: bool
    intent_confidence_threshold: float
    slot_filling_enabled: bool
    slot_completion_threshold: float
    rag_top_k: int
    rag_rerank_top_n: int
    rag_distance_threshold: float
    rag_max_context_tokens: int
    max_candidate_count: int
    clarification_enabled: bool
    context_pressure_warning: float
    context_pressure_hard_limit: float
    low_confidence_reply_mode: str
    low_confidence_decline_message: str | None
    low_confidence_clarify_message: str | None
    max_context_tokens: int

    @classmethod
    def from_model(cls, row) -> "EngineSettings":
        """row: app.models.BotSettings (hoặc bất kỳ đối tượng có cùng thuộc tính)."""
        values: dict = {}
        for name, default in DEFAULTS.items():
            if name in FIXED_TOGGLES:  # công tắc cố định: không đọc DB/form
                values[name] = FIXED_TOGGLES[name]
                continue
            stored = getattr(row, name, None)
            if stored is None:
                values[name] = default
            elif name in RANGES:
                values[name] = clamp(name, stored)
            elif name == "low_confidence_reply_mode":
                values[name] = stored if stored in REPLY_MODES else default
            else:  # 2 câu trả lời sẵn có của chủ bot
                values[name] = (str(stored).strip() or None)

        # Bất biến giữa các trường (đọc dữ liệu cũ/sai không được làm hỏng engine)
        values["rag_rerank_top_n"] = min(values["rag_rerank_top_n"], values["rag_top_k"])
        if values["context_pressure_hard_limit"] <= values["context_pressure_warning"]:
            values["context_pressure_warning"] = DEFAULTS["context_pressure_warning"]
            values["context_pressure_hard_limit"] = DEFAULTS["context_pressure_hard_limit"]

        temperature = getattr(row, "temperature", None)
        max_tokens = getattr(row, "max_tokens", None)
        language = getattr(row, "language", None)
        return cls(
            language=language if language in ("vi", "en") else DEFAULT_LANGUAGE,
            instructions=(getattr(row, "instructions", None) or "").strip(),
            temperature=0.7 if temperature is None else float(temperature),  # 0 là giá trị hợp lệ
            max_tokens=int(max_tokens) if max_tokens else 500,
            **values,
        )

    @classmethod
    def defaults(cls, **overrides) -> "EngineSettings":
        """Cấu hình mặc định (dùng cho test và khung chat thử chưa có bản ghi)."""
        base = {f.name: None for f in fields(cls)}
        base.update(DEFAULTS)
        base.update(FIXED_TOGGLES)
        base.update(language=DEFAULT_LANGUAGE, instructions="", temperature=0.7, max_tokens=500)
        base.update(overrides)
        return cls(**base)
