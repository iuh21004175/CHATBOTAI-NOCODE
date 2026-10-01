"""Cấu hình hiệu lực của Decision Engine cho 1 lượt trả lời.

Nguồn duy nhất cho: giá trị mặc định (DEFAULTS), khoảng hợp lệ (RANGES) và các công tắc bật/tắt tính năng cố định
(FIXED_TOGGLES). Cột trong app/models.py:BotSettings có server_default trùng DEFAULTS (test_settings kiểm tra khớp).

Các công tắc bật/tắt tính năng (rag_enabled, summary_enabled, structured_memory_enabled, intent_tracking_enabled,
slot_filling_enabled, clarification_enabled) không còn cho chủ bot chỉnh: hệ thống đã cố định giá trị vận hành
(FIXED_TOGGLES) áp dụng cho mọi bot, bất kể giá trị đang lưu trong DB (cột DB vẫn còn nhưng không còn được đọc).

Tham số nội bộ của RAG/Context Engine (ENGINE_INTERNAL: ngưỡng khoảng cách, số đoạn giữ lại, ngưỡng nén ngữ cảnh...) cũng
không còn cho chủ bot chỉnh: engine luôn dùng DEFAULTS bất kể giá trị đang lưu trong DB (cột DB vẫn còn, không được đọc).

Chế độ cấu hình Bước 1 (cột bot_settings.config_tier): "basic" (mặc định) chỉ chọn mức nhớ hội thoại theo MEMORY_LEVELS;
"advanced" chỉnh được ADVANCED_FIELDS dạng số. Trường ADVANCED_ONLY không thuộc chế độ hiện tại luôn dùng mặc định
(hạ về basic không để lại giá trị ẩn còn hiệu lực; lên lại advanced thì giá trị đã lưu có hiệu lực trở lại).
"""
from __future__ import annotations

from dataclasses import dataclass, fields

DEFAULT_LANGUAGE = "vi"
# Nhiệt độ lấy mẫu của đường 1 lệnh gọi (AGENT_ENABLED tắt): cố định cho mọi bot, không còn cấu hình theo bot — Harness của agent
# không truyền được tham số này nên giữ cấu hình riêng cho đường cũ chỉ tạo cảm giác có tác dụng. Cột bot_settings.temperature
# vẫn còn nhưng không được đọc.
DEFAULT_TEMPERATURE = 0.7

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

# Tham số nội bộ của RAG/Context Engine: engine luôn dùng DEFAULTS (không đọc DB), chủ bot không chỉnh được ở bất kỳ chế độ nào.
# Với agent, các giá trị này là giá trị khởi tạo cho công cụ tra cứu (search_knowledge_base), do agent tự điều chỉnh khi cần.
ENGINE_INTERNAL: tuple[str, ...] = (
    "rag_top_k", "rag_rerank_top_n", "rag_distance_threshold", "rag_max_context_tokens", "max_candidate_count",
    "intent_confidence_threshold", "slot_completion_threshold",
    "context_pressure_warning", "context_pressure_hard_limit", "max_context_tokens",
)

CONFIG_TIERS = ("basic", "advanced")
DEFAULT_TIER = "basic"

# Chế độ Nâng cao: các thông số bộ nhớ hội thoại chỉnh dạng số. Chế độ Cơ bản chỉ chọn MEMORY_LEVELS (ghi vào 2 trường
# recent_*); 2 trường summary_* ngoài chế độ Nâng cao luôn dùng mặc định.
ADVANCED_FIELDS: tuple[str, ...] = ("recent_message_limit", "recent_token_limit", "summary_trigger_tokens", "summary_max_tokens")
ADVANCED_ONLY: tuple[str, ...] = ("summary_trigger_tokens", "summary_max_tokens")

# Mức nhớ hội thoại của chế độ Cơ bản: khóa -> (recent_message_limit, recent_token_limit). "medium" = mặc định hệ thống.
MEMORY_LEVELS: dict[str, tuple[int, int]] = {
    "short": (6, 1200),
    "medium": (DEFAULTS["recent_message_limit"], DEFAULTS["recent_token_limit"]),
    "long": (20, 4000),
}
CUSTOM_MEMORY_LEVEL = "custom"  # giá trị đang lưu không khớp mức nào (chỉnh ở chế độ Nâng cao): giữ nguyên, không ghi đè


def normalize_tier(value) -> str:
    """Giá trị cột config_tier -> "basic" | "advanced". "expert" (giá trị còn lại của enum cũ) coi là advanced;
    giá trị lạ/rỗng về mặc định."""
    if value in ("advanced", "expert"):
        return "advanced"
    return DEFAULT_TIER


def memory_level_of(recent_message_limit, recent_token_limit) -> str:
    """Mức nhớ khớp cặp giá trị đang lưu, hoặc CUSTOM_MEMORY_LEVEL."""
    for key, pair in MEMORY_LEVELS.items():
        if (recent_message_limit, recent_token_limit) == pair:
            return key
    return CUSTOM_MEMORY_LEVEL


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
        advanced = normalize_tier(getattr(row, "config_tier", None)) == "advanced"
        for name, default in DEFAULTS.items():
            if name in FIXED_TOGGLES:  # công tắc cố định: không đọc DB/form
                values[name] = FIXED_TOGGLES[name]
                continue
            if name in ENGINE_INTERNAL or (name in ADVANCED_ONLY and not advanced):  # không phải thứ chủ bot chỉnh ở chế độ này
                values[name] = default
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

        max_tokens = getattr(row, "max_tokens", None)
        language = getattr(row, "language", None)
        return cls(
            language=language if language in ("vi", "en") else DEFAULT_LANGUAGE,
            instructions=(getattr(row, "instructions", None) or "").strip(),
            temperature=DEFAULT_TEMPERATURE,
            max_tokens=int(max_tokens) if max_tokens else 500,
            **values,
        )

    @classmethod
    def defaults(cls, **overrides) -> "EngineSettings":
        """Cấu hình mặc định (dùng cho test và khung chat thử chưa có bản ghi)."""
        base = {f.name: None for f in fields(cls)}
        base.update(DEFAULTS)
        base.update(FIXED_TOGGLES)
        base.update(language=DEFAULT_LANGUAGE, instructions="", temperature=DEFAULT_TEMPERATURE, max_tokens=500)
        base.update(overrides)
        return cls(**base)
