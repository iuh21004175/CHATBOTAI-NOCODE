"""Phase D — giá vốn và ước tính chi phí của 1 lượt chạy agent (1 Execution). KHÔNG đụng DB (ghi sổ nằm ở app/credits/service.py).

Tái sử dụng bảng giá và công thức của cost_estimate.py (docs/BANG_GIA_API_AI.md: cache-hit / cache-miss / output tính riêng, giá cao điểm và
ngoài cao điểm) và cách đọc usage phòng thủ của cost.py: tokens không đọc được là None ("không biết"), khác 0 ("đã đo, bằng 0").

- Giá vốn thực = chi phí LLM (từ usage THẬT của mọi lệnh gọi trong lượt, kể cả lệnh gọi tóm tắt do công cụ kích hoạt) + chi phí công cụ
  ngoài (hiện 0) + chi phí hạ tầng (hằng số cấu hình, hiện 0 — không tự bịa công thức phân bổ).
- Ước tính CAO NHẤT dùng để giữ chỗ (reserve): agent có thể gọi LLM tối đa `max_iterations` lần, mỗi lần đầu vào lớn dần vì kết quả tra cứu
  của các lần trước nằm trong ngữ cảnh; dùng giá cao điểm, không trúng cache — cận trên, nên chi phí thật gần như luôn thấp hơn.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

from core.context_engine import cost_estimate as ce
from core.context_engine.settings import EngineSettings
from core.context_engine.state import IntentSpec

VN_OFFSET = timedelta(hours=7)
PEAK_WINDOWS = ((8, 11), (13, 17))  # giờ Việt Nam, thứ Hai–Thứ Sáu (ce.PEAK_HOURS_TEXT)
MONEY_STEP = Decimal("0.0001")


def money(value) -> Decimal:
    """Số tiền VND làm tròn 4 chữ số thập phân (khớp cột DECIMAL(14,4))."""
    return Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def is_peak_hours(moment_utc: datetime) -> bool:
    """moment_utc: thời điểm UTC (naive được coi là UTC, đúng như datetime.utcnow() của project)."""
    if moment_utc.tzinfo is not None:
        moment_utc = moment_utc.astimezone(timezone.utc).replace(tzinfo=None)
    local = moment_utc + VN_OFFSET
    return local.weekday() < 5 and any(start <= local.hour < end for start, end in PEAK_WINDOWS)


def price_at(moment_utc: datetime) -> ce.Price:
    return ce.PEAK if is_peak_hours(moment_utc) else ce.OFF_PEAK


@dataclass(frozen=True)
class LlmCost:
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int
    cache_miss_tokens: int
    cost_vnd: Decimal
    reported: bool  # False: có lệnh gọi LLM không đọc được usage -> phần đó KHÔNG được tính (đếm là 0), lưu cờ để đối soát


def llm_cost(calls: Iterable[dict], moment_utc: datetime) -> LlmCost:
    """calls: các dict như LLMUsageTracker.calls (prompt_tokens, completion_tokens, cache_hit_tokens, cache_miss_tokens; có thể None).
    Chỉ có tổng prompt mà không tách hit/miss -> coi toàn bộ là cache-miss (giá cao hơn: không tính thiếu cho khách)."""
    hit = miss = out = 0
    reported = True
    for call in calls:
        prompt, completion = call.get("prompt_tokens"), call.get("completion_tokens")
        if prompt is None and completion is None:
            reported = False
            continue
        call_hit, call_miss = call.get("cache_hit_tokens"), call.get("cache_miss_tokens")
        if call_hit is None or call_miss is None:
            call_hit, call_miss = 0, prompt or 0
        hit += call_hit
        miss += call_miss
        out += completion or 0
    cost = money(ce.call_cost_vnd(hit, miss, out, price_at(moment_utc)))
    return LlmCost(input_tokens=hit + miss, output_tokens=out, cache_hit_tokens=hit, cache_miss_tokens=miss, cost_vnd=cost, reported=reported)


@dataclass(frozen=True)
class ExecutionPrice:
    llm_cost_vnd: Decimal
    tool_cost_vnd: Decimal
    infra_cost_vnd: Decimal
    total_cost_vnd: Decimal       # GIÁ VỐN
    markup_multiplier: Decimal
    billed_vnd: Decimal           # GIÁ BÁN = giá vốn × hệ số — số Credit phải trừ


def price_execution(llm: LlmCost, *, markup: Decimal, tool_cost: Decimal = Decimal(0), infra_cost: Decimal = Decimal(0)) -> ExecutionPrice:
    total = money(llm.cost_vnd + tool_cost + infra_cost)
    return ExecutionPrice(llm.cost_vnd, money(tool_cost), money(infra_cost), total, markup, money(total * markup))


def estimate_reserve_vnd(
    settings: EngineSettings, *, chunk_size: int, intents: Sequence[IntentSpec], max_question_tokens: int, max_iterations: int,
    markup: Decimal, infra_cost: Decimal = Decimal(0),
) -> Decimal:
    """Giá BÁN cao nhất có thể của 1 lượt chạy agent (số Credit giữ chỗ). Dùng cost_estimate.estimate_turn (cùng công thức ô "Chi phí ước
    tính" ở Bước 1): lệnh gọi thứ i (i = 0..max_iterations-1) có đầu vào = cận trên của lượt + i × ngân sách tra cứu (mỗi lần agent tra cứu
    thêm, kết quả nằm lại trong ngữ cảnh), đầu ra chạm trần, không trúng cache, giá cao điểm; cộng 1 lệnh gọi tóm tắt (nếu bật)."""
    turn = ce.estimate_turn(settings, chunk_size=chunk_size, intents=intents, max_question_tokens=max_question_tokens)
    high = turn["question"]["max"]
    rag_max = next((c["max"] for c in turn["components"] if c["key"] == "rag"), 0)
    total = 0.0
    for i in range(max(1, int(max_iterations))):
        total += ce.call_cost_vnd(0, high["input"] + i * rag_max, high["output"], ce.PEAK)
    summary = turn.get("summary_job")
    if summary:
        total += summary["max"]["vnd"]["peak"]
    return money((money(total) + infra_cost) * markup)
