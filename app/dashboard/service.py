"""Service layer cho Bảng điều khiển: tổng hợp dữ liệu thật từ bots/documents/conversations/
messages theo team đang đăng nhập (nguyên tắc multi-tenant) — không có dữ liệu giả lập.
"""
from datetime import datetime, timedelta

from minio.error import S3Error

from app.models import Bot, BotSettings, Conversation, Customer, Document, Message, Team, TeamMember, User
from app.widget import appearance
from config import Config
from extensions import db

STATS_WINDOW_DAYS = 30
MAX_TOKENS_MIN = 10
MAX_TOKENS_MAX = 3000
DEFAULT_MAX_TOKENS = 500


def get_team(team_id: int) -> Team | None:
    return db.session.get(Team, team_id)


def get_team_bots(team_id: int) -> list[Bot]:
    return Bot.query.filter_by(team_id=team_id).order_by(Bot.created_at.asc()).all()


def get_team_owner_name(team_id: int) -> str:
    membership = TeamMember.query.filter_by(team_id=team_id, role="Owner").first()
    if membership is None:
        return "—"
    user = db.session.get(User, membership.user_id)
    return user.full_name or user.email if user else "—"


def create_bot(team_id: int, name: str) -> Bot:
    bot = Bot(team_id=team_id, name=name.strip())
    db.session.add(bot)
    db.session.flush()
    db.session.add(BotSettings(bot_id=bot.id, language="vi", temperature=0.7))
    db.session.commit()
    return bot


def get_bot_for_team(bot_id: int, team_id: int) -> Bot | None:
    """Luôn lọc theo team_id đang đăng nhập — chặn truy cập bot của team khác qua đổi bot_id
    trên URL (nguyên tắc multi-tenant)."""
    return Bot.query.filter_by(id=bot_id, team_id=team_id).first()


def get_or_create_settings(bot: Bot) -> BotSettings:
    settings = BotSettings.query.filter_by(bot_id=bot.id).first()
    if settings is None:
        settings = BotSettings(bot_id=bot.id, language="vi", temperature=0.7)
        db.session.add(settings)
        db.session.commit()
    return settings


def update_bot_setup(bot: Bot, settings: BotSettings, form: dict) -> None:
    bot.name = (form.get("name") or bot.name).strip()

    settings.greeting = form.get("greeting", "").strip()
    settings.instructions = form.get("instructions", "").strip()
    language = form.get("language", "")
    if language in rag_engine.SUPPORTED_LANGUAGES:  # giá trị lạ (sửa HTML) thì giữ nguyên cấu hình cũ
        settings.language = language
    settings.ai_model = form.get("ai_model", "deepseek-chat")
    try:
        settings.temperature = max(0.0, min(1.0, float(form.get("temperature", 0.7))))
    except (TypeError, ValueError):
        settings.temperature = 0.7
    try:
        settings.max_tokens = max(MAX_TOKENS_MIN, min(MAX_TOKENS_MAX, int(form.get("max_tokens", DEFAULT_MAX_TOKENS))))
    except (TypeError, ValueError):
        settings.max_tokens = DEFAULT_MAX_TOKENS
    settings.forward_to_staff = form.get("forward_to_staff") == "on"
    settings.collect_customer_info = form.get("collect_customer_info") == "on"
    settings.away_message = form.get("away_message", "").strip()

    db.session.commit()


def generate_reply(bot: Bot, question: str, history: list[tuple[str, str]] | None = None) -> str:
    """Trả lời 1 câu hỏi của khách theo đúng cấu hình đã lưu ở Bước 1 (hướng dẫn/tính cách,
    ngôn ngữ, temperature, token tối đa). Dùng cho Web Widget và khung chat thử ở Bước 1.
    history: các lượt trước trong cùng hội thoại [(customer|bot, nội dung)]."""
    settings = get_or_create_settings(bot)
    return rag_engine.answer(
        bot.id,
        question,
        system_prompt=settings.instructions or "",
        temperature=settings.temperature if settings.temperature is not None else 0.7,  # 0 là giá trị hợp lệ
        max_tokens=settings.max_tokens,
        language=settings.language or rag_engine.DEFAULT_LANGUAGE,
        history=history,
    )


# ---- Chat: giới hạn dùng chung cho khung chat thử (Bước 1) và Web Widget ----

MAX_MESSAGE_CHARS = 1000
HISTORY_MESSAGES = 6  # số tin gần nhất đưa vào prompt
HISTORY_CHARS = 600  # cắt mỗi tin để prompt không phình


def clean_history(raw) -> list[tuple[str, str]]:
    """Chuẩn hoá lịch sử do client gửi ([{role, content}]) — không tin dữ liệu từ trình duyệt."""
    if not isinstance(raw, list):
        return []
    history = []
    for item in raw[-HISTORY_MESSAGES:]:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            continue
        sender = "bot" if item.get("role") == "bot" else "customer"
        history.append((sender, item["content"].strip()[:HISTORY_CHARS]))
    return [h for h in history if h[1]]


def default_greeting(language: str) -> str:
    return "Hello! How can I help you?" if language == "en" else "Xin chào! Tôi có thể giúp gì cho bạn?"


# ---- Bước 3: Xuất bản ----

def update_widget_domain(settings: BotSettings, domain: str) -> None:
    settings.widget_domain = domain.strip()
    db.session.commit()


def update_widget_appearance(settings: BotSettings, form: dict) -> str | None:
    """Lưu cấu hình giao diện chatbox (Bước 3). Trả về thông báo lỗi nếu không hợp lệ."""
    values, error = appearance.parse_form(form)
    if error:
        return error
    appearance.apply(settings, values)
    db.session.commit()
    return None


# ---- Bước 4: Lịch sử chat ----

def list_conversations(bot_id: int, search: str = "", channel: str = "") -> list[Conversation]:
    query = Conversation.query.filter_by(bot_id=bot_id)
    if channel:
        query = query.filter(Conversation.channel == channel)
    conversations = query.order_by(Conversation.created_at.desc()).limit(200).all()

    if search:
        needle = search.strip().lower()

        def matches(conv: Conversation) -> bool:
            if conv.customer_ref and needle in (conv.customer_ref.name or "").lower():
                return True
            return needle in (conv.visitor_id or "").lower()

        conversations = [c for c in conversations if matches(c)]

    return conversations


def get_conversation_for_bot(conversation_id: int, bot_id: int) -> Conversation | None:
    return Conversation.query.filter_by(id=conversation_id, bot_id=bot_id).first()


def get_conversation_messages(conversation_id: int) -> list[Message]:
    return Message.query.filter_by(conversation_id=conversation_id).order_by(Message.created_at.asc()).all()


def conversation_display_name(conv: Conversation) -> str:
    if conv.customer_ref and conv.customer_ref.name:
        return conv.customer_ref.name
    if conv.visitor_id:
        return f"Khách #{conv.visitor_id[:6]}"
    return "Khách vãng lai"


def get_bot_detail(bot: Bot) -> dict:
    """Số liệu thật cho 1 bot: tài liệu, hội thoại, tin nhắn 30 ngày gần nhất theo người gửi."""
    document_count = Document.query.filter_by(bot_id=bot.id).count()
    trained_count = Document.query.filter_by(bot_id=bot.id, status="trained").count()
    conversation_count = Conversation.query.filter_by(bot_id=bot.id).count()

    since = datetime.utcnow() - timedelta(days=STATS_WINDOW_DAYS)
    base_q = Message.query.join(Conversation).filter(
        Conversation.bot_id == bot.id, Message.created_at >= since
    )
    total_messages = base_q.count()
    customer_messages = base_q.filter(Message.sender == "customer").count()
    bot_messages = base_q.filter(Message.sender == "bot").count()
    staff_messages = base_q.filter(Message.sender == "staff").count()

    settings = BotSettings.query.filter_by(bot_id=bot.id).first()
    is_configured = bool(settings and (settings.greeting or settings.instructions))

    return {
        "document_count": document_count,
        "conversation_count": conversation_count,
        "is_active": trained_count > 0,
        "is_configured": is_configured,
        "settings": settings,
        "stats": {
            "total": total_messages,
            "customer": customer_messages,
            "bot": bot_messages,
            "staff": staff_messages,
            "followup": 0,  # chưa có pipeline gắn tin nhắn FollowUp vào messages.sender
        },
    }


# ---- Bước 2: Cơ sở tri thức ----

import csv
import io
import os

from core import rag_engine, storage_service

ALLOWED_EXTENSIONS = {".txt", ".md", ".csv"}
MB = 1024 * 1024
MAX_UPLOAD_BYTES = Config.KNOWLEDGE_MAX_FILE_MB * MB  # giới hạn 1 tệp
STORAGE_LIMIT_BYTES = Config.KNOWLEDGE_STORAGE_LIMIT_MB * MB  # quota cơ sở tri thức của MỖI trợ lý
STORAGE_WARN_PERCENT = 80  # từ mức này giao diện cảnh báo sắp đầy


def filesize(value, decimals: int = 1) -> str:
    """1536 -> '1.5 KB'. Thông báo lỗi dùng decimals=2 để '4.99 MB' không bị làm tròn thành '5.0 MB'."""
    value = float(value or 0)
    for unit in ("B", "KB", "MB"):
        if value < 1024 or unit == "MB":
            text = f"{value:.0f}" if unit == "B" else f"{value:.{decimals}f}"
            return f"{text} {unit}"
        value /= 1024


def list_documents(bot_id: int, search: str = "", ext: str = "") -> list[Document]:
    query = Document.query.filter_by(bot_id=bot_id)
    if search:
        query = query.filter(Document.filename.ilike(f"%{search.strip()}%"))
    if ext:
        query = query.filter(Document.filename.ilike(f"%.{ext.lower()}"))
    return query.order_by(Document.created_at.desc()).all()


def get_document_for_bot(document_id: int, bot_id: int) -> Document | None:
    return Document.query.filter_by(id=document_id, bot_id=bot_id).first()


def storage_summary(bot_id: int) -> dict:
    """Dung lượng cơ sở tri thức của 1 trợ lý: đã dùng/quota, số tài liệu và chunk. Tệp thất bại vẫn
    chiếm chỗ (đã nằm trong MinIO) cho tới khi bị xóa."""
    used, documents, chunks = db.session.query(
        db.func.coalesce(db.func.sum(Document.size_bytes), 0),
        db.func.count(Document.id),
        db.func.coalesce(db.func.sum(Document.chunk_count), 0),
    ).filter(Document.bot_id == bot_id).one()
    used, limit = int(used), STORAGE_LIMIT_BYTES
    percent = min(100, round(used * 100 / limit)) if limit else 0
    level = "full" if used >= limit else "warn" if percent >= STORAGE_WARN_PERCENT else "ok"
    return {
        "used": used,
        "limit": limit,
        "remaining": max(limit - used, 0),
        "percent": percent,
        "level": level,
        "documents": int(documents),
        "chunks": int(chunks),
        "max_file": MAX_UPLOAD_BYTES,
    }


def _extract_text(filename: str, raw: bytes) -> str:
    text = raw.decode("utf-8-sig", errors="replace")
    if filename.lower().endswith(".csv"):
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return ""
        header, body = rows[0], rows[1:]
        lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
        lines += ["| " + " | ".join(r) + " |" for r in body]
        return "\n".join(lines)
    return text


def failure_message(error: Exception) -> str:
    """Lý do thất bại ngắn gọn, dễ hiểu để hiện cho người dùng. Chi tiết kỹ thuật (mã lỗi S3, request id...)
    chỉ nằm trong log của worker, không đưa lên giao diện."""
    if isinstance(error, S3Error):
        return "Không đọc được tệp gốc trong kho lưu trữ (MinIO). Hãy xóa và tải lại tệp."
    if type(error).__module__.split(".")[0] in ("chromadb", "httpx") or isinstance(error, (ConnectionError, TimeoutError)):
        return "Không kết nối được ChromaDB để lưu dữ liệu huấn luyện. Vui lòng thử xử lý lại sau."
    if isinstance(error, UnicodeError):
        return "Không đọc được nội dung tệp (sai mã hóa ký tự)."
    return f"Lỗi khi xử lý nội dung tài liệu ({type(error).__name__}): {str(error)[:120]}"


def mark_failed(document: Document, message: str) -> None:
    document.status = "failed"
    document.error_message = (message or "Lỗi không xác định")[:500]
    db.session.commit()


CONFIGURABLE_STATUSES = ("draft", "trained", "failed")  # pending/processing đang do worker giữ, không cấu hình lại được


def chunk_params_for(bot: Bot, document: Document) -> tuple[int, int]:
    """Cấu hình chunk hiệu lực của 1 tài liệu: giá trị riêng của tài liệu, chưa đặt thì dùng mặc định của trợ lý."""
    settings = get_or_create_settings(bot)
    size = settings.chunk_size if document.chunk_size is None else document.chunk_size
    overlap = settings.chunk_overlap if document.chunk_overlap is None else document.chunk_overlap
    return rag_engine.normalize_chunk_params(size, overlap)


def parse_chunk_params(form) -> tuple[tuple[int, int] | None, str | None]:
    """Đọc + kiểm tra chunk_size/chunk_overlap từ dữ liệu người dùng gửi. Trả về ((size, overlap), None)
    hoặc (None, thông báo lỗi). Không tự ép giá trị sai về khoảng hợp lệ để người dùng biết mình nhập sai."""
    try:
        size, overlap = int(form.get("chunk_size", "")), int(form.get("chunk_overlap", ""))
    except (TypeError, ValueError):
        return None, "Kích thước chunk và overlap phải là số nguyên."
    if not rag_engine.CHUNK_SIZE_MIN <= size <= rag_engine.CHUNK_SIZE_MAX:
        return None, f"Kích thước chunk phải từ {rag_engine.CHUNK_SIZE_MIN} đến {rag_engine.CHUNK_SIZE_MAX} token."
    overlap_max = rag_engine.max_chunk_overlap(size)
    if not 0 <= overlap <= overlap_max:
        return None, f"Overlap phải từ 0 đến {overlap_max} token ({rag_engine.OVERLAP_MAX_PERCENT}% kích thước chunk)."
    return (size, overlap), None


def load_markdown(document: Document) -> str:
    """Đọc tệp gốc từ MinIO và chuẩn hóa sang markdown — dùng chung cho xem trước và huấn luyện để
    chunk hiển thị trên giao diện chính là chunk sẽ được huấn luyện."""
    obj = storage_service.get_file(document.storage_path)
    try:
        raw = obj.read()
    finally:
        obj.close()
        obj.release_conn()
    return rag_engine.run_blocking(_extract_text, document.filename, raw)


def preview_chunks(document: Document, chunk_size: int, chunk_overlap: int) -> dict:
    """Cắt chunk thử (chỉ đếm token, không embed) bằng đúng hàm mà worker dùng khi huấn luyện."""
    text = load_markdown(document)
    chunks = rag_engine.run_blocking(rag_engine.chunk_markdown, text, chunk_size, chunk_overlap)
    items = [
        {"index": i + 1, "content": c["content"], "tokens": rag_engine.count_tokens(c["content"])}
        for i, c in enumerate(chunks)
    ]
    total_tokens = sum(item["tokens"] for item in items)
    return {
        "chunks": items,
        "count": len(items),
        "characters": len(text),
        "average_tokens": round(total_tokens / len(items)) if items else 0,
    }


def queue_training(document: Document, chunk_size: int, chunk_overlap: int) -> None:
    """Lưu cấu hình chunk riêng của tài liệu và đưa vào hàng chờ của worker."""
    document.chunk_size = chunk_size
    document.chunk_overlap = chunk_overlap
    document.status = "pending"
    document.error_message = None
    db.session.commit()


def process_document(bot: Bot, document: Document, raw: bytes, on_progress=None) -> int:
    """Chuẩn hóa markdown -> chunk -> embed -> ghi ChromaDB (collection riêng của bot). Chạy trong worker nền
    (workers/process_documents.py). on_progress(đã_xong, tổng_chunk) báo tiến độ để đẩy realtime tới
    trình duyệt. Trả về số chunk; lỗi thì đặt status=failed + error_message rồi ném lại."""
    document.status = "processing"
    document.error_message = None
    db.session.commit()
    try:
        text = rag_engine.run_blocking(_extract_text, document.filename, raw)
        chunk_size, chunk_overlap = chunk_params_for(bot, document)
        chunks = rag_engine.run_blocking(rag_engine.chunk_markdown, text, chunk_size, chunk_overlap)
        count = rag_engine.upsert_chunks(bot.id, document.id, chunks, on_progress)
    except Exception as e:
        db.session.rollback()
        mark_failed(document, failure_message(e))
        raise
    document.status = "trained"
    document.chunk_count = count
    db.session.commit()
    return count


def upload_document(bot: Bot, file_storage) -> tuple[Document | None, str | None]:
    filename = os.path.basename(file_storage.filename or "")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None, "Hiện chỉ hỗ trợ tệp TXT, MD, CSV (PDF/DOCX đang phát triển)."

    raw = file_storage.read(MAX_UPLOAD_BYTES + 1)  # đọc tối đa giới hạn + 1 byte: tệp quá lớn không nạp hết vào RAM
    if not raw:
        return None, "Tệp rỗng."
    if len(raw) > MAX_UPLOAD_BYTES:
        return None, f"Tệp vượt giới hạn {Config.KNOWLEDGE_MAX_FILE_MB} MB/tệp."
    remaining = storage_summary(bot.id)["remaining"]
    if len(raw) > remaining:
        return None, (
            f"Không đủ dung lượng: trợ lý còn {filesize(remaining, 2)}, tệp cần {filesize(len(raw), 2)}. "
            "Hãy xóa bớt tài liệu cũ."
        )

    document = Document(bot_id=bot.id, filename=filename, storage_path="", size_bytes=len(raw), status="draft")
    db.session.add(document)
    db.session.flush()
    document.storage_path = storage_service.save_file(
        bot.team_id, bot.id, document.id, filename, io.BytesIO(raw), len(raw)
    )
    db.session.commit()
    return document, None  # chờ người dùng cấu hình chunk; chỉ khi queue_training() đặt status=pending, worker nền mới xử lý


def status_snapshot(bot_id: int) -> list[dict]:
    return [
        {"id": d.id, "status": d.status, "chunks": d.chunk_count or 0, "error": d.error_message or ""}
        for d in Document.query.filter_by(bot_id=bot_id).all()
    ]


def delete_document(bot: Bot, document: Document) -> None:
    rag_engine.delete_document(bot.id, document.id)
    try:
        storage_service.delete_file(document.storage_path)
    except Exception:
        pass
    db.session.delete(document)
    db.session.commit()
