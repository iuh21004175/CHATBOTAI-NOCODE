"""Ước tính chi phí DeepSeek Flash cho 1 câu hỏi của khách theo cấu hình trợ lý (hiện ở Bước 1, cập nhật khi đổi cấu hình).

Giá và công thức lấy từ docs/BANG_GIA_API_AI.md (mục 3-5 và 32-33): tính RIÊNG cache-hit / cache-miss / output, giá theo 1M token,
tỷ giá tham chiếu 26.200 VND/USD, có giá giờ cao điểm và ngoài cao điểm. Đây là ƯỚC LƯỢNG (khác cost.py là số đo thật từ usage):
- Token đếm bằng tokenizer của model embedding (giống Context Builder), không phải tokenizer DeepSeek.
- Cận dưới/cận trên lấy từ chính các trần trong cấu hình (recent_token_limit, rag_max_context_tokens, summary_max_tokens,
  memory_max_items, max_tokens + JSON_OVERHEAD_TOKENS, max_context_tokens...) nên đổi cấu hình nào thì con số đổi theo đó.
- Những hằng số "giả định" (câu hỏi ngắn nhất, câu trả lời ngắn nhất...) đặt tên rõ ở đầu file — không có số đo thật cho chúng.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from core import rag_engine
from core.context_engine import builder
from core.context_engine.prompts import JSON_EXAMPLE, texts
from core.context_engine.settings import JSON_OVERHEAD_TOKENS, EngineSettings
from core.context_engine.state import IntentSpec, MemoryItem

# ---- Bảng giá (docs/BANG_GIA_API_AI.md, cập nhật 21/09/2026) ----
PRICING_MODEL = "deepseek-flash"
USD_TO_VND = 26200
PEAK_HOURS_TEXT = "8:00–11:00 và 13:00–17:00 giờ Việt Nam, thứ Hai đến thứ Sáu"
CACHE_BLOCK_TOKENS = 128  # cost.py: cache hit tính theo khối 128 token


@dataclass(frozen=True)
class Price:
    """USD cho 1 triệu token."""
    cache_hit: float
    cache_miss: float
    output: float


OFF_PEAK = Price(cache_hit=0.003, cache_miss=0.15, output=0.60)
PEAK = Price(cache_hit=0.006, cache_miss=0.30, output=1.20)

# ---- Giả định (không có số đo thật) ----
MIN_QUESTION_TOKENS = 10   # câu hỏi ngắn nhất thường gặp
MIN_ANSWER_TOKENS = 20     # câu trả lời ngắn nhất (chào hỏi, xác nhận)
MIN_SUMMARY_TOKENS = 50    # bản tóm tắt ngắn nhất
CHARS_PER_TOKEN = 3        # "1 token ≈ 3–4 ký tự tiếng Việt": lấy 3 để ra cận trên
_MEMORY_SAMPLE = ("preference", "budget", "khoảng 25 triệu đồng")  # mẫu 1 mục bộ nhớ để đo token/mục


def call_cost_vnd(cache_hit_tokens: int, cache_miss_tokens: int, output_tokens: int, price: Price) -> float:
    """docs/BANG_GIA_API_AI.md mục 33: cộng riêng 3 loại token rồi quy đổi VND."""
    usd = (cache_hit_tokens * price.cache_hit + cache_miss_tokens * price.cache_miss + output_tokens * price.output) / 1_000_000
    return usd * USD_TO_VND


def question_tokens_for_chars(max_chars: int) -> int:
    return math.ceil(max_chars / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class CallTokens:
    input: int
    cache_hit: int
    output: int

    def as_dict(self) -> dict:
        miss = self.input - self.cache_hit
        return {
            "input": self.input, "cache_hit": self.cache_hit, "cache_miss": miss, "output": self.output,
            "vnd": {
                "off_peak": round(call_cost_vnd(self.cache_hit, miss, self.output, OFF_PEAK), 4),
                "peak": round(call_cost_vnd(self.cache_hit, miss, self.output, PEAK), 4),
            },
        }


def _cached_prefix(tokens: int) -> int:
    """Phần tiền tố tĩnh có thể trúng cache: làm tròn xuống theo khối cache."""
    return tokens // CACHE_BLOCK_TOKENS * CACHE_BLOCK_TOKENS


def estimate_turn(
    settings: EngineSettings,
    *,
    chunk_size: int,
    intents: Sequence[IntentSpec] = (),
    max_question_tokens: int,
    count: Callable[[list[str]], list[int]] | None = None,
) -> dict:
    """Cận dưới/cận trên chi phí 1 lượt trả lời + phần chi tiết token.

    Thấp nhất: đầu hội thoại (chưa có tóm tắt/bộ nhớ/tin cũ), không tìm được tài liệu, câu hỏi và câu trả lời ngắn, phần chỉ dẫn
    cố định đã nằm trong cache. Cao nhất: mọi phần động đầy tới trần cấu hình, câu trả lời chạm trần đầu ra, không trúng cache.
    Luôn có ĐÚNG 1 lệnh gọi chính mỗi lượt (kể cả khi bot sau đó hỏi lại/từ chối — engine.py gọi LLM trước bước quyết định)."""
    count = count or rag_engine.count_tokens_many
    lang = settings.language
    t = texts(lang)
    memory_one = builder.render_memory_block([MemoryItem(*_MEMORY_SAMPLE, 1.0)], lang)
    memory_two = builder.render_memory_block([MemoryItem(*_MEMORY_SAMPLE, 1.0)] * 2, lang)
    (system, scaffold, summary_block, memory_1, memory_2, json_skeleton, summary_prompt) = count([
        builder.render_system_static(settings, list(intents)),
        builder.render_final_user("", "", lang),
        builder.render_summary_block("x", lang),
        memory_one,
        memory_two,
        JSON_EXAMPLE,
        t["summary_prompt"].format(max_tokens=settings.summary_max_tokens),
    ])
    memory_item = max(memory_2 - memory_1, 1)
    memory_header = max(memory_1 - memory_item, 0)

    # ---- Cận trên của từng phần động ----
    summary_max = summary_block + settings.summary_max_tokens if settings.summary_enabled else 0
    memory_max = memory_header + settings.memory_max_items * memory_item if settings.structured_memory_enabled else 0
    limit = settings.recent_message_limit
    # tin gần đây xen kẽ khách/bot: tin khách tối đa max_question_tokens, tin bot tối đa max_tokens (trần câu trả lời)
    recent_max = min(settings.recent_token_limit, math.ceil(limit / 2) * max_question_tokens + limit // 2 * settings.max_tokens)
    fixed_max = system + summary_max + memory_max + recent_max + scaffold + max_question_tokens
    output_reserve = builder.ContextBudgetManager.output_reserve(settings)
    if settings.rag_enabled:
        # mỗi đoạn = chunk trúng + chunk lân cận 2 phía; ngân sách = min(còn lại của ngữ cảnh, trần tra cứu) như ContextBudgetManager
        rag_cap = settings.rag_rerank_top_n * (2 * rag_engine.NEIGHBOR_WINDOW + 1) * chunk_size
        available = settings.max_context_tokens - fixed_max - output_reserve
        rag_max = max(0, min(settings.rag_max_context_tokens, available, rag_cap))
    else:
        rag_max = 0
    input_max = min(fixed_max + rag_max, settings.max_context_tokens)  # vượt tổng ngân sách thì engine nén lại

    low = CallTokens(
        input=system + scaffold + MIN_QUESTION_TOKENS,
        cache_hit=_cached_prefix(system),
        output=json_skeleton + MIN_ANSWER_TOKENS,
    )
    high = CallTokens(input=input_max, cache_hit=0, output=settings.max_tokens + JSON_OVERHEAD_TOKENS)

    components = [
        {"key": "system", "label": "Chỉ dẫn + quy tắc (cố định)", "min": system, "max": system},
        {"key": "summary", "label": "Tóm tắt hội thoại", "min": 0, "max": summary_max},
        {"key": "memory", "label": "Bộ nhớ về khách", "min": 0, "max": memory_max},
        {"key": "recent", "label": "Tin nhắn gần đây", "min": 0, "max": recent_max},
        {"key": "rag", "label": "Tài liệu tra cứu", "min": 0, "max": rag_max},
        {"key": "question", "label": "Câu hỏi + khung nhắc", "min": scaffold + MIN_QUESTION_TOKENS, "max": scaffold + max_question_tokens},
    ]
    return {
        "model": PRICING_MODEL,
        "usd_to_vnd": USD_TO_VND,
        "peak_hours": PEAK_HOURS_TEXT,
        "question": {"min": low.as_dict(), "max": high.as_dict()},
        "components": components,
        "over_budget": fixed_max > settings.max_context_tokens,
        # Hiếm: JSON lỗi -> gọi lại, hoặc Bước F tra cứu lại lịch sử -> thêm 1 lệnh gọi cùng cỡ (cận trên: không trúng cache)
        "extra_call_max": high.as_dict()["vnd"],
        "summary_job": _summary_job(settings, summary_prompt, max_question_tokens),
    }


def _summary_job(settings: EngineSettings, prompt_tokens: int, max_question_tokens: int) -> dict | None:
    """Việc nền tóm tắt (jobs.summarize_conversation): KHÔNG tính vào từng câu hỏi; chạy khi các tin chưa tóm tắt vượt
    summary_trigger_tokens. Đầu vào = lời nhắc + tóm tắt cũ + tin mới (≈ ngưỡng, cộng tối đa 1 lượt hỏi-đáp vượt ngưỡng)."""
    if not settings.summary_enabled:
        return None
    from core.context_engine.jobs import MAX_SUMMARY_INPUT_TOKENS, SUMMARY_MARGIN_TOKENS

    new_messages_max = min(settings.summary_trigger_tokens + max_question_tokens + settings.max_tokens, MAX_SUMMARY_INPUT_TOKENS)
    low = CallTokens(
        input=prompt_tokens + min(settings.summary_trigger_tokens, MAX_SUMMARY_INPUT_TOKENS),
        cache_hit=_cached_prefix(prompt_tokens),
        output=min(MIN_SUMMARY_TOKENS, settings.summary_max_tokens),
    )
    high = CallTokens(
        input=prompt_tokens + settings.summary_max_tokens + new_messages_max,
        cache_hit=0,
        output=settings.summary_max_tokens + SUMMARY_MARGIN_TOKENS,
    )
    return {"trigger_tokens": settings.summary_trigger_tokens, "min": low.as_dict(), "max": high.as_dict()}
