"""Phase 6 — Cost Control: đọc usage của DeepSeek, theo dõi cache-hit, tổng hợp theo team.

Tên field đã xác minh bằng lệnh gọi thật (2026-09-21): `response_metadata["token_usage"]` gồm prompt_tokens,
completion_tokens, total_tokens, prompt_cache_hit_tokens, prompt_cache_miss_tokens (cấp cao nhất) và
prompt_tokens_details.cached_tokens. Cache hit tính theo khối 128 token nên lượt 2 cùng prefix mới thấy hit.

Đọc phòng thủ: thiếu/sai kiểu field usage KHÔNG được làm hỏng luồng trả lời — field không đọc được là None
(= "không biết"), khác với 0 (= "đã đo, bằng 0").
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger("context_engine.cost")


def _as_int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        return None
    return int(value)


def _add(a: int | None, b: int | None) -> int | None:
    return b if a is None else a if b is None else a + b


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None

    @property
    def reported(self) -> bool:
        return self.prompt_tokens is not None or self.completion_tokens is not None

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            _add(self.prompt_tokens, other.prompt_tokens),
            _add(self.completion_tokens, other.completion_tokens),
            _add(self.total_tokens, other.total_tokens),
            _add(self.cache_hit_tokens, other.cache_hit_tokens),
            _add(self.cache_miss_tokens, other.cache_miss_tokens),
        )

    @property
    def cache_hit_ratio(self) -> float | None:
        hit, miss = self.cache_hit_tokens, self.cache_miss_tokens
        if hit is None or miss is None or hit + miss == 0:
            return None
        return hit / (hit + miss)

    def as_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
        }


def parse_usage(token_usage) -> Usage:
    """token_usage: dict lấy từ response_metadata["token_usage"] (hoặc None nếu API không trả)."""
    if not isinstance(token_usage, dict):
        return Usage()
    prompt = _as_int(token_usage.get("prompt_tokens"))
    completion = _as_int(token_usage.get("completion_tokens"))
    total = _as_int(token_usage.get("total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion

    hit = _as_int(token_usage.get("prompt_cache_hit_tokens"))
    miss = _as_int(token_usage.get("prompt_cache_miss_tokens"))
    if hit is None:  # bản API/SDK khác chỉ có chi tiết chuẩn OpenAI
        details = token_usage.get("prompt_tokens_details")
        hit = _as_int(details.get("cached_tokens")) if isinstance(details, dict) else None
    if miss is None and hit is not None and prompt is not None:
        miss = max(prompt - hit, 0)
    return Usage(prompt, completion, total, hit, miss)


@dataclass
class LLMUsageTracker:
    """Ghi từng lệnh gọi LLM của 1 lượt. kind: "main" (lệnh gọi chính), "retry" (gọi lại chính do JSON lỗi),
    "history_lookup" (Bước F, ngoại lệ duy nhất), "summary" (việc nền). Lệnh gọi chính được đếm tách khỏi lệnh gọi
    phụ để thống kê "1 lệnh gọi/lượt" không bị lệch."""

    calls: list[dict] = field(default_factory=list)

    def record(self, kind: str, usage: Usage, **extra) -> None:
        self.calls.append({"kind": kind, **usage.as_dict(), **extra})
        logger.info("llm_call kind=%s usage=%s", kind, usage.as_dict())

    def total(self, kinds: tuple[str, ...]) -> Usage:
        result = Usage()
        for call in self.calls:
            if call["kind"] in kinds:
                result = result + Usage(
                    call["prompt_tokens"], call["completion_tokens"], call["total_tokens"],
                    call["cache_hit_tokens"], call["cache_miss_tokens"],
                )
        return result

    @property
    def main_usage(self) -> Usage:
        """Ghi vào Message.usage_*: lệnh gọi chính (kể cả lần gọi lại do JSON lỗi — vẫn là chi phí của lệnh gọi chính)."""
        return self.total(("main", "retry"))

    @property
    def extra_calls(self) -> list[dict]:
        return [c for c in self.calls if c["kind"] not in ("main", "retry")]

    @property
    def main_call_count(self) -> int:
        return sum(1 for c in self.calls if c["kind"] in ("main", "retry"))


def team_usage_summary(team_id: int, since=None) -> dict:
    """Cộng dồn usage của lệnh gọi chính theo team (tin bot của mọi bot thuộc team). Bảng subscriptions/quota chưa
    tồn tại (Team chỉ có cột `plan` dạng chuỗi) nên đây chỉ là báo cáo đọc — chưa chặn khi vượt hạn mức; việc chặn/
    nâng gói làm khi có màn hình Gói dịch vụ. Tính bằng 1 truy vấn tổng hợp trên messages (đã có usage_*), không bảng phụ."""
    from app.models import Bot, Conversation, Message
    from extensions import db

    query = (
        db.session.query(
            db.func.count(Message.id),
            db.func.coalesce(db.func.sum(Message.usage_prompt_tokens), 0),
            db.func.coalesce(db.func.sum(Message.usage_completion_tokens), 0),
            db.func.coalesce(db.func.sum(Message.usage_cache_hit_tokens), 0),
            db.func.coalesce(db.func.sum(Message.usage_cache_miss_tokens), 0),
        )
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(Bot, Bot.id == Conversation.bot_id)
        .filter(Bot.team_id == team_id, Message.sender == "bot", Message.usage_prompt_tokens.isnot(None))
    )
    if since is not None:
        query = query.filter(Message.created_at >= since)
    replies, prompt, completion, hit, miss = query.one()
    usage = Usage(int(prompt), int(completion), int(prompt) + int(completion), int(hit), int(miss))
    return {"replies": int(replies), **usage.as_dict(), "cache_hit_ratio": usage.cache_hit_ratio}
