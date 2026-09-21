"""Service layer cho blueprint widget: logic nghiệp vụ của Web Widget công khai (khách chưa đăng
nhập): kiểm tra domain được phép nhúng, lưu hội thoại/tin nhắn, gọi RAG trả lời.
Bot được xác định bằng bot_id trong URL nên mọi query đều lọc theo đúng bot đó (multi-tenant).
"""
import os
import uuid
from urllib.parse import urlparse

from flask import url_for

from app.customers import service as customers_service
from app.dashboard import service as dashboard_service
from app.inbox import service as inbox_service
from app.models import Bot, Conversation, Message
from app.widget import appearance, icons
from extensions import db

_EMBED_JS_PATH = os.path.join(os.path.dirname(__file__), "embed.js")
MAX_VISITOR_ID_CHARS = 64

UI_TEXTS = {
    "vi": {"placeholder": "Bạn muốn hỏi gì?", "send": "Gửi", "staff": "Nhân viên", "error": "Xin lỗi, hiện không thể trả lời. Vui lòng thử lại sau."},
    "en": {"placeholder": "Type your message...", "send": "Send", "staff": "Staff", "error": "Sorry, I can't answer right now. Please try again later."},
}


def embed_version() -> int:
    """Đổi khi embed.js đổi — dùng làm ?v= cho trang xem trước để không dính cache trình duyệt."""
    return int(os.path.getmtime(_EMBED_JS_PATH))


def embed_script_source() -> str:
    with open(_EMBED_JS_PATH, encoding="utf-8") as f:
        return f.read()


def normalize_domain(domain: str | None) -> str:
    """'https://www.Shop.vn/abc' / 'shop.vn:443' -> 'shop.vn'"""
    value = (domain or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    value = value.split("/")[0].split(":")[0]
    return value.removeprefix("www.")


def origin_of(url: str) -> str:
    """'https://shop.vn/trang?x=1' -> 'https://shop.vn' ('' nếu không phải URL hợp lệ)"""
    parsed = urlparse(url or "")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme in ("http", "https") and parsed.netloc else ""


def origin_allowed(origin: str | None, widget_domain: str | None) -> bool:
    """Origin của trang đang nhúng widget phải trùng domain đã khai báo (hoặc là subdomain của nó).
    Trình duyệt luôn gửi Origin với request cross-origin nên chặn được nhúng trái phép ở website khác."""
    allowed = normalize_domain(widget_domain)
    host = normalize_domain(origin) if origin and origin != "null" else ""
    if not allowed or not host:
        return False
    return host == allowed or host.endswith("." + allowed)


def get_bot(bot_id: int) -> Bot | None:
    return db.session.get(Bot, bot_id)


def icon_url(bot: Bot, settings) -> str | None:
    """URL công khai của icon tự tải lên (None nếu đang dùng icon dựng sẵn). Phải là URL TUYỆT ĐỐI
    (_external=True): widget chạy trên site của khách (origin khác), URL tương đối sẽ trỏ nhầm sang
    site đó. ?v= = tên file đã lưu (có mốc thời gian) -> tự đổi mỗi lần tải icon mới, tránh dính cache."""
    if settings.widget_icon != icons.CUSTOM_ICON_KEY or not settings.widget_icon_path:
        return None
    version = os.path.basename(settings.widget_icon_path)
    return url_for("widget.widget_icon", bot_id=bot.id, v=version, _external=True)


def get_config(bot: Bot) -> dict:
    """Thông tin hiển thị của widget: tên, lời chào và chữ giao diện theo ngôn ngữ đã chọn ở Bước 1."""
    settings = dashboard_service.get_or_create_settings(bot)
    language = settings.language if settings.language in UI_TEXTS else "vi"
    return {
        "name": bot.name,
        "greeting": settings.greeting or dashboard_service.default_greeting(language),
        "language": language,
        **appearance.to_widget(appearance.current(settings), icon_url=icon_url(bot, settings)),
        **UI_TEXTS[language],
    }


ICON_CONTENT_TYPES = dashboard_service.ICON_CONTENT_TYPES


def icon_bytes(bot: Bot) -> tuple[bytes, str] | None:
    """Nội dung + content-type của icon tự tải lên, hoặc None nếu bot không dùng icon tuỳ chỉnh."""
    settings = dashboard_service.get_or_create_settings(bot)
    if settings.widget_icon != icons.CUSTOM_ICON_KEY or not settings.widget_icon_path:
        return None
    from core import storage_service

    ext = os.path.splitext(settings.widget_icon_path)[1].lower()
    obj = storage_service.get_file(settings.widget_icon_path)
    try:
        raw = obj.read()
    finally:
        obj.close()
        obj.release_conn()
    return raw, ICON_CONTENT_TYPES.get(ext, "application/octet-stream")


def widget_domain(bot: Bot) -> str | None:
    return dashboard_service.get_or_create_settings(bot).widget_domain


def _staff_has_taken_over(conversation: Conversation) -> bool:
    """Nhân viên đang tiếp quản = hội thoại còn mở và người trả lời gần nhất (bỏ qua tin của khách) là nhân viên.
    Khi đó bot không trả lời chen ngang. Nhân viên bấm "đã xử lý" (closed) thì lần khách nhắn tới bot trả lời lại,
    và tin bot mới nhất đó kết thúc phiên tiếp quản."""
    if conversation.status not in (None, inbox_service.STATUS_OPEN):
        return False
    last_reply = (
        Message.query.filter(Message.conversation_id == conversation.id, Message.sender != "customer")
        .order_by(Message.id.desc())
        .first()
    )
    return last_reply is not None and last_reply.sender == "staff"


def receive_message(bot: Bot, message: str, visitor_id: str | None, conversation_id) -> dict:
    """Lưu tin khách -> gọi RAG (có lịch sử hội thoại) -> lưu tin bot. Nhân viên đang tiếp quản thì chỉ lưu tin khách
    (reply=None, nhân viên trả lời qua Inbox). Ném ValueError nếu tin nhắn không hợp lệ; lỗi LLM/ChromaDB được ném
    nguyên để route trả 502 (tin của khách vẫn được giữ)."""
    message = (message or "").strip()
    if not message:
        raise ValueError("empty")
    if len(message) > dashboard_service.MAX_MESSAGE_CHARS:
        raise ValueError("too_long")

    visitor_id = (visitor_id or "").strip()[:MAX_VISITOR_ID_CHARS] or uuid.uuid4().hex

    conversation = None
    if isinstance(conversation_id, int):
        # Phải đúng bot và đúng visitor — không đoán id để đọc/ghi hội thoại của khách khác
        conversation = Conversation.query.filter_by(id=conversation_id, bot_id=bot.id, visitor_id=visitor_id).first()
    if conversation is None:
        conversation = Conversation(bot_id=bot.id, channel="web_widget", visitor_id=visitor_id)
        db.session.add(conversation)
        db.session.flush()

    paused = _staff_has_taken_over(conversation)
    recent = (
        Message.query.filter_by(conversation_id=conversation.id)
        .order_by(Message.id.desc())
        .limit(dashboard_service.HISTORY_MESSAGES)
        .all()
    )
    history = [(m.sender, m.content[: dashboard_service.HISTORY_CHARS]) for m in reversed(recent) if m.sender != "staff"]

    customer_message = Message(conversation_id=conversation.id, sender="customer", content=message)
    db.session.add(customer_message)
    conversation.status = inbox_service.STATUS_OPEN  # khách nhắn lại thì hội thoại đã đóng được mở lại
    if dashboard_service.get_or_create_settings(bot).collect_customer_info:
        customers_service.capture_contact(bot.team_id, conversation, message)
    db.session.commit()
    inbox_service.emit_message(bot.team_id, conversation.id, customer_message.id)

    if paused:
        return {
            "reply": None,
            "conversation_id": conversation.id,
            "visitor_id": visitor_id,
            "last_message_id": customer_message.id,
        }

    reply = dashboard_service.generate_reply(bot, message, history)

    bot_message = Message(conversation_id=conversation.id, sender="bot", content=reply)
    db.session.add(bot_message)
    db.session.commit()
    inbox_service.emit_message(bot.team_id, conversation.id, bot_message.id)
    return {
        "reply": reply,
        "conversation_id": conversation.id,
        "visitor_id": visitor_id,
        "last_message_id": bot_message.id,
    }


def staff_messages_since(bot: Bot, conversation_id, visitor_id: str | None, after_id) -> dict:
    """Tin của nhân viên gửi từ Inbox mà widget chưa nhận (id > after_id). Cùng quy tắc như receive_message:
    phải đúng bot + đúng visitor mới đọc được hội thoại. Trả last_id để widget lần sau hỏi tiếp từ đó."""
    after_id = after_id if isinstance(after_id, int) and after_id > 0 else 0
    conversation = None
    if isinstance(conversation_id, int):
        conversation = Conversation.query.filter_by(
            id=conversation_id, bot_id=bot.id, visitor_id=(visitor_id or "").strip()[:MAX_VISITOR_ID_CHARS]
        ).first()
    if conversation is None:
        return {"messages": [], "last_id": after_id}
    rows = (
        Message.query.filter(Message.conversation_id == conversation.id, Message.id > after_id)
        .order_by(Message.id.asc())
        .all()
    )
    return {
        "messages": [{"id": m.id, "content": m.content} for m in rows if m.sender == "staff"],
        "last_id": rows[-1].id if rows else after_id,
    }
