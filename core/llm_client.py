"""Wrap ChatDeepSeek (langchain-deepseek) — điểm gọi DeepSeek API duy nhất trong app,
để đổi model/tham số không phải sửa rải rác ở nhiều blueprint.
"""
import ssl
from functools import lru_cache

import certifi
from langchain_deepseek import ChatDeepSeek
from openai import DefaultHttpxClient

from config import Config

# Model cố định cho mọi trợ lý (người dùng không chọn). Đổi model = sửa đúng 1 chỗ này.
DEEPSEEK_MODEL = "deepseek-flash"

# deepseek-flash mặc định chạy "thinking mode": token suy luận ẩn tính vào max_tokens nên khi max_tokens nhỏ toàn bộ
# hạn mức bị dùng hết cho suy luận và câu trả lời (content) rỗng — đã đo: max_tokens=40 -> content=''. Tác vụ tư vấn
# ngắn không cần chain-of-thought nên tắt hẳn, để max_tokens là độ dài câu trả lời thật như giao diện Bước 1 mô tả.
_NO_THINKING = {"thinking": {"type": "disabled"}}


@lru_cache(maxsize=1)
def _http_client() -> DefaultHttpxClient:
    """Client HTTP dùng chung cho mọi lệnh gọi LLM, với kho chứng chỉ chỉ định rõ (certifi).

    Vì sao không dùng mặc định: openai 3.x dùng httpx2, và httpx2 mặc định dựng SSL bằng truststore.SSLContext.
    truststore bắt lớp ssl.SSLContext ngay lúc import; khi tiến trình đã eventlet.monkey_patch() (bắt buộc với run.py,
    xem README) lớp đó là lớp con của eventlet nên setter verify_mode của Python gọi lại chính nó vô hạn ->
    RecursionError, mọi lệnh gọi DeepSeek trong server đều lỗi (chạy riêng lẻ ngoài server thì không). Chỉ định
    ssl.create_default_context(cafile=...) đi qua SSLContext của eventlet nên không dính lỗi và vẫn không chặn server."""
    return DefaultHttpxClient(verify=ssl.create_default_context(cafile=certifi.where()))


@lru_cache(maxsize=32)  # cache theo từng bộ tham số (temperature, max_tokens) — mỗi bot có thể khác nhau
def get_llm(temperature: float = 0.7, max_tokens: int | None = None) -> ChatDeepSeek:
    return ChatDeepSeek(
        model=DEEPSEEK_MODEL,
        api_key=Config.DEEPSEEK_API_KEY,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=_NO_THINKING,
        http_client=_http_client(),
    )
