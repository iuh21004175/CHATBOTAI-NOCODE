"""Service layer cho blueprint inbox: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.

Hội thoại thuộc bot, bot thuộc team -> mọi truy vấn đều đi qua Bot.team_id == team_id đang đăng nhập
(nguyên tắc multi-tenant), không tin conversation_id từ client.

Quy ước trạng thái Conversation.status: "open" = đang chờ xử lý, "closed" = nhân viên đã đánh dấu xử lý xong
(khách nhắn lại thì tự mở lại — xem app/widget/service.receive_message).
"""
import logging
from datetime import datetime

from sqlalchemy import func

from app.customers import service as customers_service
from app.models import Bot, Conversation, Customer, Message
from extensions import db, socketio

logger = logging.getLogger(__name__)

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
LIST_LIMIT = 100
PREVIEW_CHARS = 90

# Màu chấm kênh cạnh avatar (theo mẫu thiết kế); kênh chưa khai báo dùng màu mặc định
CHANNEL_COLORS = {"web_widget": "#2563EB", "facebook": "#1877F2", "zalo": "#0068FF"}
DEFAULT_CHANNEL_COLOR = "#64748B"


def room(team_id: int) -> str:
    return f"team:{team_id}"


def emit_message(team_id: int, conversation_id: int, message_id: int) -> None:
    """Báo realtime cho nhân viên của team đang mở Inbox. Gọi được từ request khách (widget). Lỗi Redis/Socket.IO
    chỉ ghi log, không được làm hỏng việc lưu/trả lời tin nhắn."""
    try:
        socketio.emit(
            "inbox_message",
            {"conversation_id": conversation_id, "message_id": message_id},
            to=room(team_id),
        )
    except Exception:
        logger.warning("Không đẩy được sự kiện Inbox (conversation %s) qua Socket.IO", conversation_id, exc_info=True)


def _iso(moment: datetime | None) -> str | None:
    """Cột thời gian lưu UTC không kèm múi giờ -> gắn 'Z' để trình duyệt tự đổi sang giờ máy người dùng."""
    return moment.isoformat() + "Z" if moment else None


def _team_conversations(team_id: int):
    return Conversation.query.join(Bot, Bot.id == Conversation.bot_id).filter(Bot.team_id == team_id)


def get_conversation_for_team(team_id: int, conversation_id: int) -> Conversation | None:
    return _team_conversations(team_id).filter(Conversation.id == conversation_id).first()


def _name(conv: Conversation) -> str:
    if conv.customer_ref:
        return customers_service.display_name(conv.customer_ref)
    if conv.visitor_id:
        return f"Khách #{conv.visitor_id[:6]}"
    return "Khách vãng lai"


def _initials(conv: Conversation) -> str:
    if conv.customer_ref:
        return customers_service.initials(conv.customer_ref)
    return "KH"


def _color(conv: Conversation) -> str:
    return customers_service.AVATAR_COLORS[(conv.customer_id or conv.id) % len(customers_service.AVATAR_COLORS)]


def _channel(conv: Conversation) -> dict:
    return {
        "channel": conv.channel,
        "channel_label": customers_service.channel_label(conv.channel),
        "channel_color": CHANNEL_COLORS.get(conv.channel, DEFAULT_CHANNEL_COLOR),
    }


def _summary(conv: Conversation, last: Message | None) -> dict:
    """Chờ nhân viên = hội thoại đang mở và tin cuối chưa phải của nhân viên (khách hoặc bot là người nói cuối)."""
    return {
        "id": conv.id,
        "name": _name(conv),
        "initials": _initials(conv),
        "color": _color(conv),
        **_channel(conv),
        "status": conv.status or STATUS_OPEN,
        "needs_staff": (conv.status or STATUS_OPEN) == STATUS_OPEN and last is not None and last.sender != "staff",
        "preview": (last.content[:PREVIEW_CHARS] if last else ""),
        "last_sender": last.sender if last else None,
        "at": _iso(last.created_at if last else conv.created_at),
    }


def list_conversations(team_id: int, search: str = "", limit: int = LIST_LIMIT) -> list[dict]:
    """Hội thoại của mọi bot trong team, mới nhất (theo tin nhắn cuối) lên đầu; kèm tin cuối làm bản xem trước."""
    last_id = (
        db.session.query(Message.conversation_id.label("conversation_id"), func.max(Message.id).label("message_id"))
        .group_by(Message.conversation_id)
        .subquery()
    )
    query = (
        db.session.query(Conversation, Message)
        .join(Bot, Bot.id == Conversation.bot_id)
        .outerjoin(last_id, last_id.c.conversation_id == Conversation.id)
        .outerjoin(Message, Message.id == last_id.c.message_id)
        .outerjoin(Customer, Customer.id == Conversation.customer_id)
        .filter(Bot.team_id == team_id)
    )

    search = (search or "").strip()
    if search:
        pattern = f"%{customers_service.escape_like(search)}%"
        query = query.filter(
            db.or_(
                Customer.name.ilike(pattern, escape="\\"),
                Customer.phone.ilike(pattern, escape="\\"),
                Customer.email.ilike(pattern, escape="\\"),
                Conversation.visitor_id.ilike(pattern, escape="\\"),
                Message.content.ilike(pattern, escape="\\"),
            )
        )

    rows = (
        query.order_by(func.coalesce(Message.created_at, Conversation.created_at).desc(), Conversation.id.desc())
        .limit(max(1, min(limit, LIST_LIMIT)))
        .all()
    )
    return [_summary(conv, last) for conv, last in rows]


def _message_dict(message: Message) -> dict:
    return {"id": message.id, "sender": message.sender, "content": message.content, "at": _iso(message.created_at)}


def _customer_panel(conv: Conversation) -> dict:
    """Thông tin cột bên phải: khách đã có hồ sơ thì lấy từ hồ sơ + mọi hội thoại của khách; khách vãng lai chỉ có
    dữ liệu của chính hội thoại này."""
    customer = conv.customer_ref
    if customer is None:
        return {
            "profile": False,
            "name": _name(conv),
            "contact": "",
            "stage": None,
            "stage_label": None,
            "first_at": _iso(conv.created_at),
            "conversation_count": 1,
            "profile_query": None,
        }
    first_at, count = (
        db.session.query(func.min(Conversation.created_at), func.count(Conversation.id))
        .filter(Conversation.customer_id == customer.id)
        .one()
    )
    return {
        "profile": True,
        "name": customers_service.display_name(customer),
        "contact": customers_service.contact_line(customer),
        "stage": customer.stage,
        "stage_label": customers_service.STAGES.get(customer.stage, customer.stage),
        "first_at": _iso(first_at),
        "conversation_count": count,
        # Trang Khách hàng chưa có trang chi tiết: dẫn tới danh sách đã lọc đúng khách này
        "profile_query": customer.phone or customer.email or customer.name,
    }


def conversation_detail(conv: Conversation, after_id: int = 0) -> dict:
    """Tin nhắn của hội thoại (chỉ tin có id > after_id để cập nhật tăng dần) + thông tin khách."""
    messages = (
        Message.query.filter(Message.conversation_id == conv.id, Message.id > after_id).order_by(Message.id.asc()).all()
    )
    last = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id.desc()).first()
    return {
        "conversation": _summary(conv, last),
        "messages": [_message_dict(m) for m in messages],
        "customer": _customer_panel(conv),
    }


def set_status(team_id: int, conv: Conversation, status) -> str | None:
    """Đánh dấu đã xử lý (closed) hoặc mở lại (open). Trả về thông báo lỗi nếu trạng thái không hợp lệ."""
    if status not in (STATUS_OPEN, STATUS_CLOSED):
        return "Trạng thái không hợp lệ."
    conv.status = status
    db.session.commit()
    last = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id.desc()).first()
    emit_message(team_id, conv.id, last.id if last else 0)
    return None
