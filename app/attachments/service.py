"""Service của module "Đọc tài liệu": khách gửi tệp trong widget -> lưu -> đọc nền (md/txt/csv trực tiếp; PDF/Word/Excel/PowerPoint bằng
markitdown; PDF bản scan/ảnh bằng DeepSeek vision — TỐN AI Credit) -> embed vào collection riêng -> đưa đoạn liên quan vào ngữ cảnh của các lượt
trả lời sau (core/context_engine/engine.py). Xem docs/DOCUMENT_READER.md.

Bảo vệ (widget công khai, khách chưa đăng nhập): bot phải cài module; tệp thuộc đúng bot + đúng visitor (mọi kiểu sai trả 404 như nhau); kiểm cỡ, đuôi VÀ chữ ký nội dung;
giới hạn số tệp mỗi tin/mỗi hội thoại. Nội dung tệp là dữ liệu KHÔNG tin cậy — chỉ được đưa vào prompt như ngữ cảnh tham khảo (giống tri thức), không phải chỉ thị.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta

from app.credits import service as credits_service
from app.modules import service as modules_service
from app.models import Bot, Conversation, Message, MessageAttachment
from config import Config
from core import attachment_rag, rag_engine, storage_service
from core.context_engine import execution_cost as xc
from core.context_engine import prompts as ctx_prompts
from core.context_engine.cost import LLMUsageTracker
from core.doc_reader import extract, formats, vision_reader
from extensions import db, socketio

logger = logging.getLogger(__name__)

MAX_VISITOR_ID_CHARS = 64
MAX_FILENAME_CHARS = 120
STATUS_PROCESSING, STATUS_READY, STATUS_FAILED = "processing", "ready", "failed"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f\\/:*?\"<>|]+")

# markitdown/vision nặng (CPU hoặc 1 lệnh gọi DeepSeek thật): giới hạn số lần chạy đồng thời. Khoá phải là khoá của LUỒNG THẬT (chờ diễn ra trong
# luồng của tpool, xem _extract_limited) — khoá "xanh" của eventlet sẽ không chặn được các luồng thật đang chờ.
_original_threading = getattr(rag_engine, "_threading", threading)
_slots = _original_threading.BoundedSemaphore(max(Config.VISION_CONCURRENCY, 1))


class AttachmentError(Exception):
    """Lỗi do khách (tệp/thao tác không hợp lệ): message tiếng Việt hiện thẳng cho khách, status là mã HTTP."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- bật/tắt theo module

def enabled_for(bot_id: int) -> bool:
    return modules_service.document_reader_installed(bot_id)


def widget_settings(bot: Bot) -> dict:
    """Phần cấu hình gửi xuống widget. enabled=False thì widget không hiện nút đính kèm."""
    enabled = enabled_for(bot.id)
    return {
        "enabled": enabled,
        "accept": ",".join(formats.ALL_EXTENSIONS),
        "max_bytes": Config.ATTACHMENT_MAX_BYTES,
        "max_files": Config.ATTACHMENT_MAX_PER_MESSAGE,
    }


# ---------------------------------------------------------------- tải lên

def _display_name(filename: str) -> str:
    name = _CONTROL_RE.sub(" ", os.path.basename((filename or "").replace("\\", "/"))).strip()
    stem, ext = os.path.splitext(name)
    return (stem[: MAX_FILENAME_CHARS - len(ext)] + ext) if len(name) > MAX_FILENAME_CHARS else name


def _clean_visitor(visitor_id) -> str:
    return (visitor_id or "").strip()[:MAX_VISITOR_ID_CHARS] if isinstance(visitor_id, str) else ""


def _owned_conversation(bot: Bot, conversation_id, visitor_id: str) -> Conversation | None:
    if not isinstance(conversation_id, int):
        return None
    return Conversation.query.filter_by(id=conversation_id, bot_id=bot.id, visitor_id=visitor_id).first()


def _usable_count(bot: Bot, visitor_id: str, conversation: Conversation | None) -> int:
    """Số tệp còn dùng được của khách: đã gắn vào hội thoại này + đã tải nhưng chưa gắn vào tin nào (không tính tệp lỗi)."""
    query = MessageAttachment.query.filter(
        MessageAttachment.bot_id == bot.id, MessageAttachment.visitor_id == visitor_id, MessageAttachment.status != STATUS_FAILED,
    )
    if conversation is not None:
        query = query.filter((MessageAttachment.conversation_id == conversation.id) | (MessageAttachment.message_id.is_(None)))
    else:
        query = query.filter(MessageAttachment.message_id.is_(None))
    return query.count()


def upload(app, bot: Bot, visitor_id, conversation_id, file) -> dict:
    """Nhận 1 tệp khách tải lên. Trả dict trạng thái (đang đọc). Ném AttachmentError nếu không hợp lệ."""
    if not enabled_for(bot.id):
        raise AttachmentError("Trợ lý này chưa bật tính năng nhận tệp.", 403)
    visitor_id = _clean_visitor(visitor_id)
    if not visitor_id:
        raise AttachmentError("Thiếu định danh khách.")
    if file is None or not getattr(file, "filename", ""):
        raise AttachmentError("Chưa chọn tệp.")
    name = _display_name(file.filename)
    if not formats.is_supported(name):
        raise AttachmentError("Định dạng tệp chưa được hỗ trợ. Hỗ trợ: PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), ảnh, .md, .txt, .csv.")
    raw = file.stream.read(Config.ATTACHMENT_MAX_BYTES + 1)  # đọc tối đa cỡ cho phép + 1 byte: đủ để biết vượt, không nạp cả tệp khổng lồ vào RAM
    if not raw:
        raise AttachmentError("Tệp trống.")
    if len(raw) > Config.ATTACHMENT_MAX_BYTES:
        raise AttachmentError(f"Tệp quá lớn (tối đa {Config.ATTACHMENT_MAX_BYTES // (1024 * 1024)} MB).", 413)
    if not formats.has_valid_signature(name, raw):
        raise AttachmentError("Nội dung tệp không khớp với định dạng của nó (tệp có thể bị hỏng hoặc bị đổi đuôi).")

    conversation = _owned_conversation(bot, conversation_id, visitor_id)
    if _usable_count(bot, visitor_id, conversation) >= Config.ATTACHMENT_MAX_PER_CONVERSATION:
        raise AttachmentError(f"Mỗi cuộc trò chuyện chỉ đính kèm tối đa {Config.ATTACHMENT_MAX_PER_CONVERSATION} tệp.")

    # Ảnh LUÔN cần DeepSeek vision; PDF CÓ THỂ là bản scan (chỉ biết chắc sau khi markitdown thử đọc) — giả định trường hợp tốn nhất để kiểm
    # Credit TRƯỚC khi nhận tệp, cùng nguyên tắc với Phase M (không giữ chỗ, chỉ chặn sớm nếu rõ ràng không đủ).
    if formats.is_image(name) or formats.extension_of(name) == ".pdf":
        calls = Config.VISION_MAX_PDF_PAGES if formats.extension_of(name) == ".pdf" else 1
        estimate = vision_reader.estimate_vision_vnd(calls)
        if not credits_service.has_credit_for_analysis(bot.team_id, estimate):
            raise AttachmentError(
                f"Không đủ AI Credit để đọc tệp này (cần tối thiểu khoảng {int(estimate):,}đ). Vui lòng nạp thêm Credit.".replace(",", "."), 402,
            )

    row = MessageAttachment(
        bot_id=bot.id, conversation_id=conversation.id if conversation else None, visitor_id=visitor_id, filename=name,
        content_type=(getattr(file, "mimetype", None) or "")[:100] or None, size_bytes=len(raw), storage_path="", status=STATUS_PROCESSING,
    )
    db.session.add(row)
    db.session.flush()
    row.storage_path = storage_service.save_attachment(bot.team_id, bot.id, row.id, formats.extension_of(name), raw)
    db.session.commit()
    socketio.start_background_task(process, app, row.id, raw)
    return to_dict(row)


# ---------------------------------------------------------------- xử lý nền

def _extract_limited(filename: str, raw: bytes, tracker: LLMUsageTracker):
    """Chạy trong luồng thật (rag_engine.run_blocking): markitdown/vision xếp hàng theo VISION_CONCURRENCY; đọc md/txt/csv không cần xếp hàng."""
    if formats.needs_heavy_processing(filename):
        with _slots:
            return extract.extract_text(filename, raw, tracker=tracker)
    return extract.extract_text(filename, raw, tracker=tracker)


def process(app, attachment_id: int, raw: bytes) -> None:
    """Tác vụ nền: trích văn bản -> chunk + embed -> ready. Lỗi của khách (ReadError) ghi message tiếng Việt; lỗi hệ thống ghi log đầy đủ và báo
    chung chung. Nếu bước trích văn bản dùng DeepSeek vision (ảnh/PDF bản scan — xem core/doc_reader/extract.py), trừ AI Credit THEO GIÁ VỐN
    THẬT ngay sau khi xong, giống hệt khuôn module_analysis_charge của Phase M."""
    with app.app_context():
        row = db.session.get(MessageAttachment, attachment_id)
        if row is None:
            return
        tracker = LLMUsageTracker()
        try:
            result = rag_engine.run_blocking(_extract_limited, row.filename, raw, tracker)
            row.chunk_count = attachment_rag.index_text(row.bot_id, row.id, result.text)
            row.char_count, row.extract_method, row.truncated = len(result.text), result.method, result.truncated
            row.status, row.error_message = STATUS_READY, None
        except extract.ReadError as exc:
            db.session.rollback()
            row = db.session.get(MessageAttachment, attachment_id)
            row.status, row.error_message = STATUS_FAILED, str(exc)[:500]
        except Exception:
            logger.exception("attachments: xử lý tệp %s lỗi", attachment_id)
            db.session.rollback()
            row = db.session.get(MessageAttachment, attachment_id)
            row.status, row.error_message = STATUS_FAILED, "Không xử lý được tệp này lúc này. Bạn thử lại sau nhé."
        if tracker.calls:  # đã tốn LLM thật (kể cả khi bước sau đó lỗi) thì vẫn tính phí phần đã dùng — không "miễn phí" vì lỗi ở bước khác
            bot = db.session.get(Bot, row.bot_id)
            llm = xc.llm_cost(tracker.calls, datetime.utcnow())
            price = xc.price_execution(llm, markup=Config.PLATFORM_MARKUP_MULTIPLIER)
            row.vision_cost_vnd = price.total_cost_vnd
            row.vision_charged_vnd = credits_service.settle_attachment_vision(bot.team_id, attachment_id=row.id, price=price)
            if not llm.reported:
                logger.warning("attachments: tệp %s có lệnh gọi DeepSeek vision không đo được usage — phần đó không được tính phí", attachment_id)
        row.processed_at = datetime.utcnow()
        db.session.commit()


def _fail_if_stale(row: MessageAttachment) -> None:
    """Đang xử lý mà quá hạn tối đa (ATTACHMENT_STALE_SECONDS) = tiến trình xử lý đã chết (server khởi động lại giữa chừng): báo lỗi thay vì để
    khách chờ mãi."""
    limit = timedelta(seconds=Config.ATTACHMENT_STALE_SECONDS)
    if row.status == STATUS_PROCESSING and row.created_at and datetime.utcnow() - row.created_at > limit:
        row.status, row.error_message, row.processed_at = STATUS_FAILED, "Việc đọc tệp bị gián đoạn. Bạn vui lòng gửi lại tệp.", datetime.utcnow()
        db.session.commit()


def to_dict(row: MessageAttachment) -> dict:
    return {
        "id": row.id, "filename": row.filename, "status": row.status, "error": row.error_message if row.status == STATUS_FAILED else None,
        "truncated": bool(row.truncated),
    }


def status_of(bot: Bot, attachment_id, visitor_id) -> dict | None:
    """Trạng thái 1 tệp cho đúng chủ (bot + visitor). None = không có (sai chủ/không tồn tại)."""
    visitor_id = _clean_visitor(visitor_id)
    row = MessageAttachment.query.filter_by(id=attachment_id, bot_id=bot.id, visitor_id=visitor_id).first() if isinstance(attachment_id, int) and visitor_id else None
    if row is None:
        return None
    _fail_if_stale(row)
    return to_dict(row)


# ---------------------------------------------------------------- gắn vào tin nhắn + đưa vào ngữ cảnh

def claim(bot: Bot, conversation: Conversation, visitor_id: str, attachment_ids) -> list[MessageAttachment]:
    """Xác nhận các tệp khách đính kèm vào tin sắp gửi. Không commit (cùng giao dịch với việc lưu tin). Ném AttachmentError nếu có tệp không dùng được."""
    if attachment_ids in (None, []):
        return []
    if not isinstance(attachment_ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in attachment_ids):
        raise AttachmentError("Danh sách tệp đính kèm không hợp lệ.")
    ids = list(dict.fromkeys(attachment_ids))
    if len(ids) > Config.ATTACHMENT_MAX_PER_MESSAGE:
        raise AttachmentError(f"Mỗi tin chỉ đính kèm tối đa {Config.ATTACHMENT_MAX_PER_MESSAGE} tệp.")
    if not enabled_for(bot.id):
        raise AttachmentError("Trợ lý này chưa bật tính năng nhận tệp.", 403)
    rows = MessageAttachment.query.filter(MessageAttachment.id.in_(ids), MessageAttachment.bot_id == bot.id, MessageAttachment.visitor_id == visitor_id).all()
    if len(rows) != len(ids):
        raise AttachmentError("Không tìm thấy tệp đính kèm.", 404)
    rows.sort(key=lambda r: r.id)  # cùng thứ tự với question_for() khi dựng lại nhãn
    for row in rows:
        _fail_if_stale(row)
        if row.status == STATUS_PROCESSING:
            raise AttachmentError(f"Tệp \"{row.filename}\" đang được đọc, vui lòng đợi xong rồi gửi.")
        if row.status != STATUS_READY:
            raise AttachmentError(f"Tệp \"{row.filename}\" không đọc được: {row.error_message or 'lỗi không xác định'}")
        if row.message_id is not None or (row.conversation_id is not None and row.conversation_id != conversation.id):
            raise AttachmentError(f"Tệp \"{row.filename}\" đã được gửi trước đó.")
    for row in rows:
        row.conversation_id = conversation.id
    return rows


def bind_to_message(rows: list[MessageAttachment], message_id: int) -> None:
    for row in rows:
        row.message_id = message_id


def label_for(rows: list[MessageAttachment]) -> str:
    return "📎 " + ", ".join(r.filename for r in rows) if rows else ""


def question_for(message: Message, language: str) -> str:
    """Câu hỏi đưa vào engine cho tin khách. Tin CHỈ có tệp (nội dung đúng bằng nhãn "📎 tên tệp") thì thêm chỉ dẫn "tóm tắt tệp rồi hỏi khách cần gì" — nếu không,
    engine chỉ thấy tên tệp làm câu hỏi. Tin có chữ thì giữ nguyên."""
    rows = MessageAttachment.query.filter_by(message_id=message.id).order_by(MessageAttachment.id).all()
    if rows and message.content == label_for(rows):
        return f"{message.content}\n{ctx_prompts.texts(language)['attachment_only_question']}"
    return message.content


def for_conversation(bot: Bot, conversation: Conversation) -> dict[int, str]:
    """{attachment_id: tên tệp} các tệp đã đọc xong của hội thoại, để engine tra cứu trong đó. Module đã gỡ khỏi bot thì không dùng nữa."""
    if not enabled_for(bot.id):
        return {}
    rows = (
        MessageAttachment.query.filter_by(bot_id=bot.id, conversation_id=conversation.id, status=STATUS_READY)
        .order_by(MessageAttachment.id.desc()).limit(Config.ATTACHMENT_MAX_PER_CONVERSATION).all()
    )
    return {r.id: r.filename for r in rows}
