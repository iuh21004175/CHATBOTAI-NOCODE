"""Đọc chữ trong ảnh / PDF dạng bản scan bằng CHÍNH model DeepSeek đã dùng cho toàn hệ thống: deepseek-flash nhận ảnh qua `image_url` theo đúng
format chuẩn OpenAI-compatible (xác minh với tài liệu chính thức https://api-docs.deepseek.com/guides/vision/ — hỗ trợ JPEG/PNG/GIF/WebP, mỗi
ảnh quy đổi tối đa ~1024 token). KHÔNG dùng OCR cục bộ (Tesseract) hay dịch vụ ngoài khác (Azure Document Intelligence...): tận dụng đúng
API/công nghệ đã có trong project, không thêm phần mềm hệ thống mới, không phát sinh bên thứ ba mới (ảnh vốn đã phải gửi ra DeepSeek để phân
tích/trả lời giống mọi lệnh gọi khác của hệ thống).

QUAN TRỌNG — chi phí thật: khác markitdown/text (miễn phí, chạy tại chỗ), MỖI lần gọi ở đây là 1 lệnh gọi DeepSeek THẬT, tốn AI Credit của team.
Người gọi (app/attachments/service.py) PHẢI: (1) kiểm đủ Credit TRƯỚC bằng credits_service.has_credit_for_analysis(team_id, estimate_vision_vnd(...))
trước khi gọi bất kỳ hàm nào ở đây, và (2) sau khi xong, trừ Credit thật bằng credits_service.settle_attachment_vision() dựa trên tracker đã
truyền vào — ĐÚNG khuôn module_analysis_charge của Phase M (app/modules/runner.py).
"""
from __future__ import annotations

import base64
import io
import logging
from decimal import Decimal

from config import Config
from core.context_engine import cost_estimate as ce
from core.context_engine import execution_cost as xc
from core.context_engine.cost import LLMUsageTracker, parse_usage
from core.context_engine.structured import LLMReply
from core.doc_reader.errors import ReadError
from core.llm_client import get_llm

logger = logging.getLogger(__name__)

VISION_TEMPERATURE = 0.0  # tác vụ chép lại chữ nhìn thấy, cần ổn định (không sáng tạo)
PROMPT_OVERHEAD_TOKENS = 200  # system/user prompt văn bản đi kèm ảnh

_PROMPT = (
    "Chép lại NGUYÊN VĂN mọi chữ đọc được trong ảnh này, giữ đúng thứ tự đọc tự nhiên; nếu có bảng thì trình bày lại dạng bảng markdown. "
    "Nếu ảnh hoàn toàn không có chữ nào, chỉ mô tả ngắn gọn nội dung ảnh trong 1 câu. Không thêm lời chào hay bình luận nào khác."
)


def estimate_vision_vnd(call_count: int) -> Decimal:
    """Giá BÁN cao nhất cho `call_count` lần gọi vision (1 ảnh = 1 lần; PDF bản scan = tối đa VISION_MAX_PDF_PAGES lần — xem
    app/attachments/service.py nơi gọi hàm này TRƯỚC khi biết chắc có cần vision hay không, nên phải giả định trường hợp tốn nhất)."""
    input_tokens = Config.VISION_IMAGE_TOKENS + PROMPT_OVERHEAD_TOKENS
    per_call = ce.call_cost_vnd(0, input_tokens, Config.VISION_MAX_TOKENS, ce.PEAK)
    return xc.money(xc.money(per_call * max(1, call_count)) * Config.PLATFORM_MARKUP_MULTIPLIER)


def _call(image) -> LLMReply:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    data_url = f"data:image/png;base64,{base64.b64encode(buf.getvalue()).decode('ascii')}"
    llm = get_llm(VISION_TEMPERATURE, Config.VISION_MAX_TOKENS)
    message = {"role": "user", "content": [
        {"type": "text", "text": _PROMPT},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]}
    response = llm.invoke([message])
    content = response.content if isinstance(response.content, str) else ""
    metadata = getattr(response, "response_metadata", None) or {}
    return LLMReply(content=content, token_usage=metadata.get("token_usage"))


def read_image(raw: bytes, *, tracker: LLMUsageTracker) -> str:
    """Đọc chữ (hoặc mô tả, nếu không có chữ) của 1 ảnh bằng DeepSeek vision. Ném ReadError nếu ảnh hỏng hoặc lệnh gọi lỗi."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ReadError("Không mở được tệp ảnh này (có thể đã hỏng).") from exc
    try:
        reply = _call(image)
    except Exception as exc:
        logger.exception("Gọi DeepSeek vision đọc ảnh lỗi")
        raise ReadError("Không đọc được nội dung ảnh này lúc này. Bạn thử lại sau nhé.") from exc
    tracker.record("attachment_vision", parse_usage(reply.token_usage))
    return (reply.content or "").strip()


def read_scanned_pdf(raw: bytes, *, tracker: LLMUsageTracker) -> str:
    """Đọc từng trang của 1 PDF dạng bản scan (không có text layer thật) bằng DeepSeek vision. Trần số trang (VISION_MAX_PDF_PAGES) để chặn
    chi phí/thời gian của 1 tệp không giới hạn theo số trang."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(raw)
    except Exception as exc:
        raise ReadError("Không mở được tệp PDF này (có thể đã hỏng hoặc đặt mật khẩu).") from exc
    try:
        page_count = min(len(pdf), Config.VISION_MAX_PDF_PAGES)
        pages_text = []
        for index in range(page_count):
            page = pdf[index]
            try:
                image = page.render(scale=2.0).to_pil()
                reply = _call(image)
            except Exception as exc:
                logger.exception("Gọi DeepSeek vision đọc trang %d của PDF bản scan lỗi", index + 1)
                raise ReadError("Không đọc được tệp PDF bản scan này lúc này. Bạn thử lại sau nhé.") from exc
            finally:
                page.close()
            tracker.record("attachment_vision", parse_usage(reply.token_usage))
            pages_text.append((reply.content or "").strip())
        text = "\n\n".join(p for p in pages_text if p)
    finally:
        pdf.close()
    if not text:
        raise ReadError("Không nhận ra được chữ nào trong tệp PDF bản scan này.")
    return text
