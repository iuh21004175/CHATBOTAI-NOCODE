"""Wrap ChatDeepSeek (langchain-deepseek) — điểm gọi DeepSeek API duy nhất trong app,
để đổi model/tham số không phải sửa rải rác ở nhiều blueprint.
"""
from functools import lru_cache

from langchain_deepseek import ChatDeepSeek

from config import Config


@lru_cache(maxsize=32)  # cache theo từng bộ tham số (temperature, max_tokens) — mỗi bot có thể khác nhau
def get_llm(temperature: float = 0.7, max_tokens: int | None = None) -> ChatDeepSeek:
    return ChatDeepSeek(
        model=Config.DEEPSEEK_MODEL,
        api_key=Config.DEEPSEEK_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
    )
