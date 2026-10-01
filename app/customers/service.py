"""Service layer cho blueprint customers: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.

Khách hàng thuộc team (multi-tenant) nên MỌI truy vấn ở đây đều lọc theo team_id truyền vào.
Kênh / trạng thái / lần cuối tương tác không lưu riêng mà suy ra từ hội thoại của khách:
- kênh, trạng thái: của hội thoại mới nhất (id lớn nhất) gắn với khách;
- lần cuối tương tác: tin nhắn mới nhất trong mọi hội thoại của khách (chưa có tin thì lấy ngày tạo khách).
"""
import csv
import io
import math
import re
from datetime import datetime

from sqlalchemy import func

from app.models import Conversation, Customer, Message
from extensions import db

STAGES = {"new": "Mới", "lead": "Tiềm năng", "won": "Đã chốt"}
DEFAULT_STAGE = "new"

# Nhãn hiển thị của Conversation.channel; kênh chưa có trong bảng này hiện nguyên mã kênh
CHANNEL_LABELS = {
    "web_widget": "Website",
    "facebook": "Messenger",
    "zalo": "Zalo OA",
    "whatsapp": "WhatsApp",
    "tiktok": "TikTok",
    "instagram": "Instagram",
}

# Conversation.status: "open" = đang chờ xử lý; "closed" (nhân viên đã xử lý ở Inbox) và mọi giá trị khác = đã xong
STATUS_WAITING = "waiting"
STATUS_DONE = "done"
STATUS_LABELS = {STATUS_WAITING: "Đang chờ", STATUS_DONE: "Hoàn tất"}

PER_PAGE = 20
MAX_NAME_CHARS = 255
MAX_EMAIL_CHARS = 255

AVATAR_COLORS = ["#FBBF24", "#34D399", "#818CF8", "#F472B6", "#FB923C", "#22D3EE"]


# ---- Chuẩn hoá & trích xuất liên hệ ----

_VN_PHONE = re.compile(r"(?:\+?84|0)([2-9]\d{8})")
_INTL_PHONE = re.compile(r"\+\d{8,15}")
# Số VN trong câu chat tự do: 0912345678, 0912 345 678, 091.234.5678, +84 912 345 678, 84912345678
_PHONE_IN_TEXT = re.compile(r"(?<![\w+])(?:\+?84|0)(?:[ .\-]?\d){9}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")


def normalize_phone(raw: str | None) -> str | None:
    """Số VN -> dạng 0xxxxxxxxx (10 số); số quốc tế -> +<8-15 số>; không hợp lệ -> None."""
    compact = re.sub(r"[\s.\-()]", "", raw or "")
    vn = _VN_PHONE.fullmatch(compact)
    if vn:
        return "0" + vn.group(1)
    if _INTL_PHONE.fullmatch(compact):
        return compact
    return None


def format_phone(phone: str | None) -> str:
    """0912345678 -> +84 912 345 678 (giống mẫu thiết kế); số khác giữ nguyên."""
    if phone and re.fullmatch(r"0[2-9]\d{8}", phone):
        return f"+84 {phone[1:4]} {phone[4:7]} {phone[7:]}"
    return phone or ""


def normalize_email(raw: str | None) -> str | None:
    email = (raw or "").strip().lower()
    if not email or len(email) > MAX_EMAIL_CHARS or _EMAIL.fullmatch(email) is None:
        return None
    return email


def extract_phone(text: str) -> str | None:
    for match in _PHONE_IN_TEXT.finditer(text or ""):
        phone = normalize_phone(match.group())
        if phone:
            return phone
    return None


def extract_email(text: str) -> str | None:
    for match in _EMAIL.finditer(text or ""):
        email = normalize_email(match.group())
        if email:
            return email
    return None


def capture_contact(team_id: int, conversation: Conversation, text: str) -> Customer | None:
    """Thu thập thông tin khách từ 1 tin nhắn của khách: có số điện thoại / email thì tạo (hoặc dùng lại)
    Customer trong team và gắn vào hội thoại. Chỉ điền trường còn trống, không ghi đè thông tin đã có.
    Không commit — người gọi commit cùng tin nhắn."""
    phone = extract_phone(text)
    email = extract_email(text)
    if not (phone or email):
        return None
    return _attach_contact(team_id, conversation, phone=phone, email=email)


# Tên slot liên hệ trong intent do chủ bot khai báo (required_slots/optional_slots của BotIntentConfig): khi AI điền được các slot
# này qua cơ chế slot-filling, thông tin được ghi thẳng vào Customer — không cần nhân viên nhập tay.
CONTACT_NAME_SLOT = "contact_name"
CONTACT_PHONE_SLOT = "contact_phone"
CONTACT_EMAIL_SLOT = "contact_email"


def _slot_text(value) -> str | None:
    """Giá trị slot do LLM sinh: chỉ nhận chuỗi/số đơn giản (số điện thoại có thể là số), bỏ list/dict/None/rỗng."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return str(value).strip() or None


def clean_name(raw) -> str | None:
    """Tên khách từ slot: gộp khoảng trắng, không rỗng, không dài quá cột; chuỗi trông như email/số điện thoại thì không phải tên."""
    name = " ".join(raw.split()) if isinstance(raw, str) else ""  # số không phải tên (khác số điện thoại)
    if not name or len(name) > MAX_NAME_CHARS or normalize_email(name) or normalize_phone(name):
        return None
    return name


def capture_contact_from_slots(team_id: int, conversation: Conversation, slots: dict | None) -> Customer | None:
    """Ghi slot contact_name / contact_phone / contact_email (nếu đã điền và hợp lệ) vào Customer của hội thoại — cùng quy tắc
    như capture_contact: tạo hoặc dùng lại Customer trong team, chỉ điền trường còn trống, không ghi đè. Giá trị không hợp lệ
    (số điện thoại/email sai định dạng) bị bỏ qua chứ không lưu dữ liệu rác. Không commit — người gọi commit cùng lượt trả lời."""
    slots = slots or {}
    name = clean_name(slots.get(CONTACT_NAME_SLOT))
    phone = normalize_phone(_slot_text(slots.get(CONTACT_PHONE_SLOT)))
    email = normalize_email(_slot_text(slots.get(CONTACT_EMAIL_SLOT)))
    if not (name or phone or email):
        return None
    return _attach_contact(team_id, conversation, name=name, phone=phone, email=email)


def _attach_contact(
    team_id: int, conversation: Conversation, *, name: str | None = None, phone: str | None = None, email: str | None = None
) -> Customer:
    customer = conversation.customer_ref
    if customer is None:
        conditions = []
        if phone:
            conditions.append(Customer.phone == phone)
        if email:
            conditions.append(Customer.email == email)
        # Chỉ có tên (chưa có phone/email) thì không có khóa nào để nhận ra khách cũ: luôn là khách mới của hội thoại này
        customer = (
            Customer.query.filter(Customer.team_id == team_id, db.or_(*conditions)).order_by(Customer.id.asc()).first()
            if conditions
            else None
        )
        if customer is None:
            customer = Customer(team_id=team_id, stage=DEFAULT_STAGE)
            db.session.add(customer)
        conversation.customer_ref = customer

    if name and not customer.name:
        customer.name = name
    if phone and not customer.phone:
        customer.phone = phone
    if email and not customer.email:
        customer.email = email
    return customer


# ---- Hiển thị ----

def display_name(customer: Customer) -> str:
    return customer.name or format_phone(customer.phone) or customer.email or f"Khách #{customer.id}"


def contact_line(customer: Customer) -> str:
    """Dòng liên hệ dưới tên: thông tin liên hệ chưa được dùng làm tên hiển thị."""
    if customer.name:
        return format_phone(customer.phone) or customer.email or ""
    return customer.email or "" if customer.phone else ""


def initials(customer: Customer) -> str:
    if customer.name:
        words = customer.name.split()
        letters = words[0][0] + words[-1][0] if len(words) > 1 else words[0][:2]
        return letters.upper()
    return re.sub(r"\W", "", display_name(customer))[:2].upper() or "#"


def time_ago(moment: datetime | None, now: datetime | None = None) -> str:
    """Mốc thời gian (UTC, như mọi cột created_at trong DB) -> "5 phút trước", "Hôm qua", ..."""
    if moment is None:
        return "—"
    seconds = ((now or datetime.utcnow()) - moment).total_seconds()
    if seconds < 60:
        return "Vừa xong"
    if seconds < 3600:
        return f"{int(seconds // 60)} phút trước"
    if seconds < 86400:
        return f"{int(seconds // 3600)} giờ trước"
    days = int(seconds // 86400)
    if days == 1:
        return "Hôm qua"
    if days < 30:
        return f"{days} ngày trước"
    return moment.strftime("%d/%m/%Y")


def channel_label(channel: str | None) -> str:
    return CHANNEL_LABELS.get(channel, channel) if channel else "—"


def _status_of(conversation_status: str | None) -> str | None:
    if conversation_status is None:
        return None
    return STATUS_WAITING if conversation_status == "open" else STATUS_DONE


def serialize(customer: Customer, channel: str | None, conv_status: str | None, last_at: datetime | None) -> dict:
    status = _status_of(conv_status)
    moment = last_at or customer.created_at
    return {
        "id": customer.id,
        "name": customer.name,
        "display_name": display_name(customer),
        "contact": contact_line(customer),
        "initials": initials(customer),
        "color": AVATAR_COLORS[customer.id % len(AVATAR_COLORS)],
        "phone": customer.phone,
        "phone_display": format_phone(customer.phone),
        "email": customer.email,
        "channel": channel,
        "channel_label": channel_label(channel),
        "stage": customer.stage,
        "stage_label": STAGES.get(customer.stage, customer.stage),
        "status": status,
        "status_label": STATUS_LABELS.get(status, "—"),
        "last_interaction_at": moment.isoformat() if moment else None,
        "last_interaction_label": time_ago(moment),
        "created_at": customer.created_at.isoformat() if customer.created_at else None,
    }


# ---- Truy vấn ----

def escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _query(team_id: int, search: str = "", channel: str = "", stage: str = "", status: str = ""):
    """Query (Customer, kênh, trạng thái hội thoại, lần cuối có tin nhắn) của team, đã lọc và sắp xếp
    theo tương tác gần nhất."""
    latest_conv = (
        db.session.query(Conversation.customer_id.label("customer_id"), func.max(Conversation.id).label("conv_id"))
        .filter(Conversation.customer_id.isnot(None))
        .group_by(Conversation.customer_id)
        .subquery()
    )
    last_msg = (
        db.session.query(Conversation.customer_id.label("customer_id"), func.max(Message.created_at).label("last_at"))
        .join(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.customer_id.isnot(None))
        .group_by(Conversation.customer_id)
        .subquery()
    )
    query = (
        db.session.query(Customer, Conversation.channel, Conversation.status, last_msg.c.last_at)
        .outerjoin(latest_conv, latest_conv.c.customer_id == Customer.id)
        .outerjoin(Conversation, Conversation.id == latest_conv.c.conv_id)
        .outerjoin(last_msg, last_msg.c.customer_id == Customer.id)
        .filter(Customer.team_id == team_id)
    )

    search = (search or "").strip()
    if search:
        pattern = f"%{escape_like(search)}%"
        query = query.filter(
            db.or_(
                Customer.name.ilike(pattern, escape="\\"),
                Customer.phone.ilike(pattern, escape="\\"),
                Customer.email.ilike(pattern, escape="\\"),
            )
        )
    if channel:
        query = query.filter(Conversation.channel == channel)
    if stage in STAGES:
        query = query.filter(Customer.stage == stage)
    if status == STATUS_WAITING:
        query = query.filter(Conversation.status == "open")
    elif status == STATUS_DONE:
        query = query.filter(Conversation.status.isnot(None), Conversation.status != "open")

    return query.order_by(func.coalesce(last_msg.c.last_at, Customer.created_at).desc(), Customer.id.desc())


def list_customers(team_id: int, search: str = "", channel: str = "", stage: str = "", status: str = "",
                   page: int = 1, per_page: int = PER_PAGE) -> dict:
    """Danh sách khách hàng của team (CRM cơ bản), có tìm kiếm, lọc và phân trang."""
    query = _query(team_id, search, channel, stage, status)
    total = query.order_by(None).count()
    pages = max(1, math.ceil(total / per_page))
    page = max(1, min(page, pages))  # trang ngoài phạm vi (vd: sau khi lọc) thì về trang hợp lệ gần nhất
    rows = query.offset((page - 1) * per_page).limit(per_page).all()
    return {
        "items": [serialize(*row) for row in rows],
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
    }


def channel_options(team_id: int) -> list[tuple[str, str]]:
    """Các kênh thực sự có khách trong team -> [(mã, nhãn)] cho bộ lọc "Nền tảng"."""
    rows = (
        db.session.query(Conversation.channel)
        .join(Customer, Customer.id == Conversation.customer_id)
        .filter(Customer.team_id == team_id, Conversation.channel.isnot(None))
        .distinct()
        .all()
    )
    return sorted(((code, channel_label(code)) for (code,) in rows), key=lambda option: option[1])


def get_customer(team_id: int, customer_id: int) -> Customer | None:
    return Customer.query.filter_by(id=customer_id, team_id=team_id).first()


def customer_detail(customer: Customer) -> dict:
    """Chi tiết khách + lịch sử hội thoại liên quan (mới nhất trước)."""
    conversations = (
        db.session.query(Conversation, func.count(Message.id))
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .filter(Conversation.customer_id == customer.id)
        .group_by(Conversation.id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc())
        .all()
    )
    latest = max(conversations, key=lambda row: row[0].id)[0] if conversations else None
    last_at = (
        db.session.query(func.max(Message.created_at))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .filter(Conversation.customer_id == customer.id)
        .scalar()
    )
    data = serialize(customer, latest.channel if latest else None, latest.status if latest else None, last_at)
    data["conversations"] = [
        {
            "id": conv.id,
            "bot_id": conv.bot_id,
            "channel": conv.channel,
            "channel_label": channel_label(conv.channel),
            "status": conv.status,
            "message_count": message_count,
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
        }
        for conv, message_count in conversations
    ]
    return data


def update_customer(customer: Customer, payload: dict) -> str | None:
    """Cập nhật các trường có trong payload (name, phone, email, stage). Trả về thông báo lỗi nếu payload
    không hợp lệ — khi đó KHÔNG thay đổi gì. Chuỗi rỗng ở name/phone/email nghĩa là xoá giá trị."""
    changes = {}

    if "name" in payload:
        name = payload["name"]
        if name is not None and not isinstance(name, str):
            return "Tên không hợp lệ."
        name = (name or "").strip()
        if len(name) > MAX_NAME_CHARS:
            return f"Tên tối đa {MAX_NAME_CHARS} ký tự."
        changes["name"] = name or None

    if "phone" in payload:
        raw = payload["phone"]
        if raw is not None and not isinstance(raw, str):
            return "Số điện thoại không hợp lệ."
        raw = (raw or "").strip()
        phone = normalize_phone(raw) if raw else None
        if raw and phone is None:
            return "Số điện thoại không hợp lệ."
        changes["phone"] = phone

    if "email" in payload:
        raw = payload["email"]
        if raw is not None and not isinstance(raw, str):
            return "Email không hợp lệ."
        raw = (raw or "").strip()
        email = normalize_email(raw) if raw else None
        if raw and email is None:
            return "Email không hợp lệ."
        changes["email"] = email

    if "stage" in payload:
        if payload["stage"] not in STAGES:
            return "Giai đoạn không hợp lệ."
        changes["stage"] = payload["stage"]

    for field, value in changes.items():
        setattr(customer, field, value)
    db.session.commit()
    return None


# ---- Xuất CSV ----

CSV_HEADER = ["Khách hàng", "Số điện thoại", "Email", "Kênh", "Giai đoạn", "Trạng thái", "Lần cuối tương tác"]


def _csv_text(value: str | None) -> str:
    """Chặn CSV injection: ô bắt đầu bằng = + - @ sẽ bị Excel/Sheets hiểu là công thức."""
    value = value or ""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_csv(team_id: int, search: str = "", channel: str = "", stage: str = "", status: str = "") -> str:
    """CSV (UTF-8 có BOM để Excel đọc đúng tiếng Việt) của mọi khách khớp bộ lọc, không phân trang."""
    buffer = io.StringIO()
    buffer.write("﻿")
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for customer, channel_code, conv_status, last_at in _query(team_id, search, channel, stage, status).all():
        item = serialize(customer, channel_code, conv_status, last_at)
        moment = last_at or customer.created_at
        writer.writerow([
            _csv_text(customer.name),
            customer.phone or "",
            _csv_text(customer.email),
            item["channel_label"],
            item["stage_label"],
            item["status_label"],
            moment.strftime("%d/%m/%Y %H:%M") if moment else "",
        ])
    return buffer.getvalue()
