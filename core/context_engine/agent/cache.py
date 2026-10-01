"""Cache Redis cho KẾT QUẢ TRA CỨU của công cụ agent (không cache câu trả lời cuối — xem ghi chú cuối file).

Khóa = (bot, phiên bản tài liệu của bot, chữ ký cấu hình truy xuất, câu tìm đã chuẩn hóa):
- phiên bản tài liệu của bot tăng mỗi khi tài liệu được huấn luyện/xóa (dashboard.service gọi bump) -> cache cũ tự vô hiệu, không cần xóa từng khóa;
- chữ ký cấu hình (top_k, ngưỡng khoảng cách, ngân sách token, ngôn ngữ...) -> đổi cấu hình bot thì không dùng kết quả cũ;
- bot_id nằm trong khóa nên KHÔNG bao giờ trộn dữ liệu giữa các bot/khách hàng.
TTL ngắn (mặc định 300s) chặn mọi trường hợp lệch còn sót (vd Redis lỗi đúng lúc bump).

Vì sao không cache câu trả lời cuối: câu trả lời phụ thuộc lịch sử hội thoại + cấu hình bot tại lúc hỏi; cache sai ngữ cảnh sẽ trả lời sai cho khách
khác. Chỉ cache bước tra cứu (thuần theo bot + câu tìm) là an toàn."""
from __future__ import annotations

import hashlib
import json
import logging
import re

import redis

logger = logging.getLogger("context_engine.agent.cache")

DEFAULT_TTL_SECONDS = 300
_SPACES = re.compile(r"\s+")


def normalize_query(query: str) -> str:
    """Chữ thường + gộp khoảng trắng. KHÔNG bỏ dấu tiếng Việt: "gia"/"giá" cho vector khác nhau nên không được coi là cùng câu."""
    return _SPACES.sub(" ", (query or "").strip().lower())


def settings_signature(settings) -> str:
    parts = [settings.rag_top_k, settings.rag_rerank_top_n, settings.rag_distance_threshold, settings.rag_max_context_tokens,
             settings.max_candidate_count, settings.language]
    return hashlib.sha1(json.dumps(parts).encode("utf-8")).hexdigest()[:12]


class RagCache:
    def __init__(self, client, prefix: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        self.redis = client
        self.prefix = prefix
        self.ttl = int(ttl_seconds)

    @property
    def enabled(self) -> bool:
        return self.ttl > 0

    def _version_key(self, bot_id: int) -> str:
        return f"{self.prefix}:ragver:{bot_id}"

    def version(self, bot_id: int) -> int:
        value = self.redis.get(self._version_key(bot_id))
        return int(value) if value else 0

    def bump(self, bot_id: int) -> int:
        """Vô hiệu MỌI kết quả đã cache của bot (tài liệu vừa đổi)."""
        return int(self.redis.incr(self._version_key(bot_id)))

    def _key(self, bot_id: int, signature: str, query: str) -> str:
        digest = hashlib.sha1(normalize_query(query).encode("utf-8")).hexdigest()
        return f"{self.prefix}:ragcache:{bot_id}:{self.version(bot_id)}:{signature}:{digest}"

    def get(self, bot_id: int, signature: str, query: str) -> dict | None:
        if not self.enabled or not normalize_query(query):
            return None
        raw = self.redis.get(self._key(bot_id, signature, query))
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def put(self, bot_id: int, signature: str, query: str, payload: dict) -> None:
        if self.enabled and normalize_query(query):
            self.redis.set(self._key(bot_id, signature, query), json.dumps(payload, ensure_ascii=False), ex=self.ttl)


def default_cache() -> RagCache:
    """Cache theo cấu hình hiện hành (app.config trong request, Config ngoài request — vd worker huấn luyện tài liệu)."""
    from flask import current_app, has_app_context

    from config import Config
    from extensions import redis_client

    source = current_app.config if has_app_context() else {name: getattr(Config, name) for name in ("AGENT_REDIS_PREFIX", "AGENT_RAG_CACHE_SECONDS")}
    return RagCache(redis_client, source["AGENT_REDIS_PREFIX"], source["AGENT_RAG_CACHE_SECONDS"])


def bump_for_bot(bot_id: int) -> None:
    """Gọi khi tài liệu của bot đổi. Redis lỗi thì KHÔNG làm hỏng thao tác tài liệu (đã ghi log): cache cũ chỉ còn sống tối đa TTL."""
    try:
        default_cache().bump(bot_id)
    except redis.RedisError:
        logger.warning("không vô hiệu được cache tra cứu của bot %s (Redis lỗi) — kết quả cũ tồn tại tối đa TTL", bot_id, exc_info=True)
