"""Đầu vào duy nhất của module "Đọc tài liệu": (tên tệp, nội dung) -> văn bản markdown để cắt chunk + embed.

Chạy CHẶN (markitdown nhanh; DeepSeek vision dự phòng cho PDF bản scan/ảnh là 1 lệnh gọi mạng, có thể mất vài giây) nên nơi gọi phải chạy trong
tác vụ nền / luồng thật (xem app/attachments/service.py).

`tracker` (tuỳ chọn): truyền vào để ghi nhận usage NẾU bước đọc rơi vào nhánh DeepSeek vision (ảnh, hoặc PDF bản scan) — nhánh này TỐN AI Credit
thật, khác markitdown/text (miễn phí, chạy tại chỗ). Người gọi dùng tracker để tính + trừ Credit (xem app/credits/service.py:
settle_attachment_vision); không cần tracker riêng thì bỏ qua, extract_text tự tạo 1 cái dùng tạm (usage không đi đâu — chỉ hợp lý khi gọi thử/
test, KHÔNG dùng trong luồng thật vì sẽ không trừ Credit cho lệnh gọi vision đã tốn tiền)."""
from __future__ import annotations

from dataclasses import dataclass

from config import Config
from core.context_engine.cost import LLMUsageTracker
from core.doc_reader import formats, markitdown_reader, text, vision_reader
from core.doc_reader.errors import ReadError

__all__ = ["ExtractResult", "ReadError", "extract_text"]


@dataclass
class ExtractResult:
    text: str
    method: str          # "text" | "markitdown" | "vision"
    truncated: bool = False


def extract_text(filename: str, raw: bytes, *, tracker: LLMUsageTracker | None = None) -> ExtractResult:
    tracker = tracker or LLMUsageTracker()
    if not formats.is_supported(filename):
        raise ReadError("Định dạng tệp này chưa được hỗ trợ.")
    if not formats.has_valid_signature(filename, raw):
        raise ReadError("Nội dung tệp không khớp với định dạng của nó (tệp có thể bị hỏng hoặc bị đổi đuôi).")
    if formats.is_document(filename):
        doc = markitdown_reader.read_document(filename, raw, tracker=tracker)
        # PDF bản scan: read_document tự chuyển sang vision_reader bên trong -> gắn nhãn đúng "vision", không phải "markitdown", để
        # extract_method phản ánh đúng bước nào THỰC SỰ tạo ra nội dung (và tốn Credit).
        method, content = ("vision" if doc.used_vision else "markitdown"), doc.text
    elif formats.is_image(filename):
        method, content = "vision", vision_reader.read_image(raw, tracker=tracker)
    else:
        method, content = "text", text.read_text(filename, raw)
    content = content.strip()
    if not content:
        raise ReadError("Không tìm thấy chữ nào trong tệp này (tệp trống, hoặc ảnh không có chữ đọc được).")
    truncated = len(content) > Config.ATTACHMENT_MAX_TEXT_CHARS
    return ExtractResult(text=content[:Config.ATTACHMENT_MAX_TEXT_CHARS], method=method, truncated=truncated)
