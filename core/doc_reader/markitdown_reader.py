"""Đọc PDF/Word/Excel/PowerPoint (.pdf/.docx/.pptx/.xlsx) bằng markitdown (Microsoft, MIT) -> markdown.

markitdown KHÔNG cần môi trường riêng (khác MinerU trước đây): nó không dùng model ngôn ngữ nào — chỉ parse cấu trúc tệp bằng
pdfminer/pdfplumber/python-pptx/openpyxl... — nên không ép phiên bản huggingface_hub/openai xung đột với môi trường chính, cài thẳng vào môi
trường chính được (xem docs/DOCUMENT_READER.md về cách cài từ mã nguồn).

markitdown CHỈ trích chữ CÓ SẴN trong tệp (text layer của PDF, text run của Word/Excel/PowerPoint) — nó KHÔNG tự OCR. PDF dạng BẢN SCAN (ảnh
chụp/quét, không có text layer thật) sẽ cho ra rất ít hoặc không có chữ; trường hợp đó được phát hiện ở đây (ngưỡng _MIN_PDF_TEXT_LAYER_CHARS)
và chuyển sang đọc bằng DeepSeek vision (core/doc_reader/vision_reader.py — TỐN AI Credit, khác nhánh markitdown thuần miễn phí ở trên).
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from markitdown import MarkItDown

from core.context_engine.cost import LLMUsageTracker
from core.doc_reader import vision_reader
from core.doc_reader.errors import ReadError
from core.doc_reader.formats import extension_of

logger = logging.getLogger(__name__)


@dataclass
class DocumentResult:
    text: str
    used_vision: bool  # True nếu PDF bản scan -> phải rơi sang vision_reader (TỐN AI Credit); để extract.py gắn đúng extract_method

# Dưới ngưỡng này (sau khi rút gọn khoảng trắng) coi như PDF không có text layer thật -> rất có thể là bản scan -> chuyển sang vision. Cố tình để
# RẤT THẤP: PDF scan thật sự (ảnh chụp/quét, không có đối tượng chữ nào trong content stream) cho ra chuỗi RỖNG, trong khi 1 PDF chữ hợp lệ dù
# ngắn (vd biên nhận 1 dòng) vẫn vượt xa ngưỡng này — đã kiểm chứng thực tế (xem test_doc_reader.py). Để ngưỡng cao sẽ đẩy nhầm văn bản ngắn hợp
# lệ sang vision (tốn Credit không cần thiết, kém chính xác hơn trích text layer thật).
_MIN_PDF_TEXT_LAYER_CHARS = 10

_converter = MarkItDown(enable_plugins=False)  # không bật plugin bên thứ ba (bề mặt tin cậy không cần thiết cho nhu cầu đọc tệp đơn giản)


def read_document(filename: str, raw: bytes, *, tracker: LLMUsageTracker) -> DocumentResult:
    """.pdf/.docx/.pptx/.xlsx -> markdown. Ném ReadError nếu không đọc được / tệp hỏng / (PDF bản scan mà) vision cũng không đọc được.
    tracker: ghi nhận usage NẾU phải rơi vào nhánh vision (PDF bản scan) — người gọi dùng để tính + trừ AI Credit."""
    ext = extension_of(filename)
    try:
        result = _converter.convert_stream(io.BytesIO(raw), file_extension=ext)
        text = (result.text_content or "").strip()
    except Exception as exc:
        logger.exception("markitdown đọc %s lỗi", filename)
        raise ReadError("Không đọc được nội dung tệp này (tệp có thể bị hỏng hoặc đặt mật khẩu).") from exc

    if ext == ".pdf" and len(text) < _MIN_PDF_TEXT_LAYER_CHARS:
        # không có text layer thật -> rất có thể là bản scan
        return DocumentResult(text=vision_reader.read_scanned_pdf(raw, tracker=tracker), used_vision=True)
    if not text:
        raise ReadError("Không tìm thấy chữ nào trong tệp này.")
    return DocumentResult(text=text, used_vision=False)
