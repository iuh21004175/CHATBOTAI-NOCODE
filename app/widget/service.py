"""Service layer cho blueprint widget: logic nghiệp vụ của Web Widget công khai (khách chưa đăng
nhập): kiểm tra domain được phép nhúng, lưu hội thoại/tin nhắn, gọi RAG trả lời.
Bot được xác định bằng public_id (định danh công khai ngẫu nhiên) trong URL nên mọi query đều lọc theo đúng bot đó (multi-tenant).
"""
import hmac
import logging
import os
import uuid
from datetime import datetime
from urllib.parse import urlparse

from flask import current_app, url_for

from app.attachments import service as attachments_service
from app.customers import service as customers_service
from app.dashboard import service as dashboard_service
from app.inbox import service as inbox_service
from app.models import Bot, BotDomain, Conversation, Message, ModuleAction, PendingWidgetAction
from app.modules import service as modules_service
from app.widget import appearance, channel, icons, turns
from app.widget.domains import origin_allowed  # noqa: F401 — routes gọi service.origin_allowed
from config import Config
from extensions import db, redis_client, socketio

logger = logging.getLogger(__name__)

_EMBED_JS_PATH = os.path.join(os.path.dirname(__file__), "embed.js")
MAX_VISITOR_ID_CHARS = 64
MAX_PUBLIC_ID_CHARS = 64  # = độ dài cột bots.public_id
STEP_ANALYZING = "analyzing"  # bước đầu của mọi lượt (các bước sau do agent báo: core/context_engine/agent/protocol.PROGRESS_*)
SLOW_REPLY_SECONDS = 10       # quá chừng này chưa có câu trả lời: widget báo "đã nhận yêu cầu, xong sẽ gửi" và cho khách làm việc khác (trả lời bất đồng bộ)
TURN_MARGIN_SECONDS = 30      # dư cho khởi động tiến trình agent + xếp hàng khi tính thời gian tối đa của lượt

UI_TEXTS = {
    "vi": {"placeholder": "Bạn muốn hỏi gì?", "send": "Gửi", "staff": "Nhân viên", "error": "Xin lỗi, hiện không thể trả lời. Vui lòng thử lại sau.",
           "confirm_prompt": "Bạn xác nhận cho trợ lý thực hiện thao tác sau trên website?", "confirm_yes": "Đồng ý", "confirm_no": "Huỷ",
           "confirm_cancelled": "Đã huỷ thao tác.", "action_failed": "Không thực hiện được thao tác trên trang này.",
           # Tốc độ cảm nhận: câu đệm hiện ngay khi khách gửi; dòng tiến trình THẬT của agent; báo nhận yêu cầu khi lâu; báo có kết quả muộn
           "fillers": ["Câu hỏi rất hay, đợi mình xem lại tài liệu một chút nhé...", "Mình đang kiểm tra lại dữ liệu, bạn chờ vài giây nha...",
                       "Để mình xem cái này nhé..."],
           "steps": {"analyzing": "🔍 Đang phân tích yêu cầu...", "searching": "📂 Đang tra cứu tài liệu...", "acting": "⚙️ Đang thực hiện thao tác trên website...",
                     "composing": "✍️ Đang tổng hợp câu trả lời..."},
           "slow": "Mình đã nhận được yêu cầu. Việc này hơi tốn thời gian, mình sẽ gửi kết quả ngay khi xong. Bạn cứ xem trang hoặc hỏi câu khác nhé.",
           "late_reply": "✅ Kết quả bạn yêu cầu lúc nãy đã xong:",
           "attach": {"button": "Đính kèm tệp", "reading": "Đang đọc tệp...", "remove": "Bỏ tệp", "too_big": "Tệp quá lớn (tối đa {mb} MB).",
                      "unsupported": "Định dạng tệp chưa được hỗ trợ.", "too_many": "Mỗi tin chỉ đính kèm tối đa {n} tệp.", "failed": "Không tải được tệp.",
                      "truncated": "Tệp dài nên chỉ đọc phần đầu.", "only_file": "Hãy đọc tệp này giúp mình."}},
    "en": {"placeholder": "Type your message...", "send": "Send", "staff": "Staff", "error": "Sorry, I can't answer right now. Please try again later.",
           "confirm_prompt": "Do you confirm that the assistant may perform this action on the website?", "confirm_yes": "Confirm", "confirm_no": "Cancel",
           "confirm_cancelled": "Action cancelled.", "action_failed": "The action could not be performed on this page.",
           "fillers": ["Great question, let me check the documents for a moment...", "I'm checking the data, please give me a few seconds...",
                       "Let me take a look at that..."],
           "steps": {"analyzing": "🔍 Analyzing your request...", "searching": "📂 Searching the documents...", "acting": "⚙️ Performing the action on the website...",
                     "composing": "✍️ Putting the answer together..."},
           "slow": "I've received your request. This takes a little longer, I'll send the result as soon as it's ready. Feel free to keep browsing or ask something else.",
           "late_reply": "✅ The result you asked for earlier is ready:",
           "attach": {"button": "Attach a file", "reading": "Reading the file...", "remove": "Remove file", "too_big": "File is too large (max {mb} MB).",
                      "unsupported": "This file type is not supported.", "too_many": "You can attach up to {n} files per message.", "failed": "Could not upload the file.",
                      "truncated": "The file is long, only the first part was read.", "only_file": "Please read this file for me."}},
}


def embed_version() -> int:
    """Đổi khi embed.js đổi — dùng làm ?v= cho trang xem trước để không dính cache trình duyệt."""
    return int(os.path.getmtime(_EMBED_JS_PATH))


def embed_script_source() -> str:
    with open(_EMBED_JS_PATH, encoding="utf-8") as f:
        return f.read()


def origin_of(url: str) -> str:
    """'https://shop.vn/trang?x=1' -> 'https://shop.vn' ('' nếu không phải URL hợp lệ)"""
    parsed = urlparse(url or "")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme in ("http", "https") and parsed.netloc else ""


def get_bot(public_id: str) -> Bot | None:
    """Tra bot theo định danh CÔNG KHAI (bots.public_id). id số tuần tự nội bộ không bao giờ khớp -> None (404)."""
    public_id = (public_id or "").strip()
    if not public_id or len(public_id) > MAX_PUBLIC_ID_CHARS:
        return None
    return Bot.query.filter_by(public_id=public_id).first()


def icon_url(bot: Bot, settings) -> str | None:
    """URL công khai của icon tự tải lên (None nếu đang dùng icon dựng sẵn). Phải là URL TUYỆT ĐỐI
    (_external=True): widget chạy trên site của khách (origin khác), URL tương đối sẽ trỏ nhầm sang
    site đó. ?v= = tên file đã lưu (có mốc thời gian) -> tự đổi mỗi lần tải icon mới, tránh dính cache."""
    if settings.widget_icon != icons.CUSTOM_ICON_KEY or not settings.widget_icon_path:
        return None
    version = os.path.basename(settings.widget_icon_path)
    return url_for("widget.widget_icon", public_id=bot.public_id, v=version, _external=True)


# Khoá bước tiến trình -> cột câu tuỳ chỉnh trên BotSettings (trống = câu mặc định theo ngôn ngữ, xem UI_TEXTS[lang]["steps"]). Đúng theo mã bước
# thật mà agent/Flask phát ra (core/context_engine/agent/protocol.py:PROGRESS_* + STEP_ANALYZING) — 3 nơi này PHẢI khớp nhau.
PROGRESS_TEXT_COLUMNS = {
    STEP_ANALYZING: "speed_progress_text_analyzing", "searching": "speed_progress_text_searching",
    "acting": "speed_progress_text_acting", "composing": "speed_progress_text_composing",
}


def _perceived_speed_texts(settings, language: str) -> dict:
    """3 kỹ thuật tối ưu tốc độ cảm nhận (Bước 5), MẶC ĐỊNH BẬT, tắt được riêng từng cái ở Bước 1: tắt kỹ thuật nào thì trả rỗng cho đúng phần
    widget đọc (embed.js coi giá trị rỗng là "không có gì để hiện") — không cần widget biết công tắc, chỉ cần đọc đúng dữ liệu server đưa xuống."""
    steps = dict(UI_TEXTS[language]["steps"])
    if settings.speed_progress_enabled:
        for code, column in PROGRESS_TEXT_COLUMNS.items():
            custom = (getattr(settings, column, None) or "").strip()
            if custom:
                steps[code] = custom
    else:
        steps = {}
    return {
        "steps": steps,
        "fillers": UI_TEXTS[language]["fillers"] if settings.speed_fillers_enabled else [],
        "slow": UI_TEXTS[language]["slow"] if settings.speed_async_enabled else "",
        "async_enabled": bool(settings.speed_async_enabled),
    }


def get_config(bot: Bot) -> dict:
    """Thông tin hiển thị của widget: tên, lời chào, chữ giao diện theo ngôn ngữ đã chọn ở Bước 1, và cấu hình tốc độ cảm nhận (Bước 1)."""
    settings = dashboard_service.get_or_create_settings(bot)
    language = settings.language if settings.language in UI_TEXTS else "vi"
    return {
        "name": bot.name,
        "greeting": settings.greeting or dashboard_service.default_greeting(language),
        "language": language,
        **appearance.to_widget(appearance.current(settings), icon_url=icon_url(bot, settings)),
        **UI_TEXTS[language],
        **_perceived_speed_texts(settings, language),
        "slow_after_seconds": SLOW_REPLY_SECONDS,
        "wait_seconds": turn_timeout_seconds(),
        "attachments": attachments_service.widget_settings(bot),
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


def allowed_domains(bot: Bot) -> list[str]:
    """Các domain được phép nhúng widget của bot (bảng bot_domains)."""
    return [row.domain for row in BotDomain.query.filter_by(bot_id=bot.id).all()]


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


def _accept_message(bot: Bot, message: str, visitor_id: str | None, conversation_id, attachment_ids=None) -> tuple[Conversation, Message, str, bool]:
    """Phần chung của mọi đường nhận tin (đồng bộ và bất đồng bộ): kiểm tra tin, tìm/tạo hội thoại đúng bot + đúng visitor, LƯU + commit tin khách.
    Trả (hội thoại, tin khách, visitor_id, paused). paused = nhân viên đang tiếp quản (bot không trả lời). Ném ValueError nếu tin không hợp lệ,
    attachments_service.AttachmentError nếu tệp đính kèm (attachment_ids, module "Đọc tài liệu") không dùng được — khi đó chưa ghi gì.
    Tin chỉ có tệp (không chữ) hợp lệ: nội dung lưu là nhãn "📎 tên tệp"."""
    message = (message or "").strip()
    if not message and not attachment_ids:
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

    try:
        attachments = attachments_service.claim(bot, conversation, visitor_id, attachment_ids)
    except attachments_service.AttachmentError:
        db.session.rollback()  # bỏ hội thoại vừa tạo dở (nếu có): tin không hợp lệ thì không để lại dấu vết
        raise
    label = attachments_service.label_for(attachments)
    content = f"{message}\n\n{label}" if message and label else (message or label)

    customer_message = Message(conversation_id=conversation.id, sender="customer", content=content)
    db.session.add(customer_message)
    db.session.flush()
    attachments_service.bind_to_message(attachments, customer_message.id)
    conversation.status = inbox_service.STATUS_OPEN  # khách nhắn lại thì hội thoại đã đóng được mở lại
    if dashboard_service.get_or_create_settings(bot).collect_customer_info:
        customers_service.capture_contact(bot.team_id, conversation, message)
    db.session.commit()
    inbox_service.emit_message(bot.team_id, conversation.id, customer_message.id)
    return conversation, customer_message, visitor_id, paused


def receive_message(bot: Bot, message: str, visitor_id: str | None, conversation_id, attachment_ids=None) -> dict:
    """Lưu tin khách -> gọi RAG (có lịch sử hội thoại) -> lưu tin bot. Nhân viên đang tiếp quản thì chỉ lưu tin khách
    (reply=None, nhân viên trả lời qua Inbox). Ném ValueError nếu tin nhắn không hợp lệ; lỗi LLM/ChromaDB được ném
    nguyên để route trả 502 (tin của khách vẫn được giữ). Đường ĐỒNG BỘ (chờ tới khi có câu trả lời) — widget mới dùng start_message."""
    conversation, customer_message, visitor_id, paused = _accept_message(bot, message, visitor_id, conversation_id, attachment_ids)
    if paused:
        return {
            "reply": None,
            "conversation_id": conversation.id,
            "visitor_id": visitor_id,
            "last_message_id": customer_message.id,
        }

    # Lịch sử, tóm tắt, bộ nhớ, RAG và cây quyết định nằm trong core/context_engine; tin bot (kèm decision_trace + usage) đã
    # được lưu + commit bên trong.
    bot_message = dashboard_service.reply_to_customer(bot, conversation, customer_message)
    inbox_service.emit_message(bot.team_id, conversation.id, bot_message.id)
    return {
        "reply": bot_message.content,
        "conversation_id": conversation.id,
        "visitor_id": visitor_id,
        "last_message_id": bot_message.id,
    }


# ---------------------------------------------------------------- trả lời BẤT ĐỒNG BỘ (tốc độ cảm nhận): trả ngay mã lượt, tác vụ nền chạy agent

def turn_timeout_seconds() -> float:
    """Thời gian tối đa hợp lý cho 1 lượt (chạy agent + các lần chờ hành động + xếp hàng): widget hết chờ sau chừng này, khóa lượt tự nhả sau chừng này."""
    return Config.AGENT_MAX_RUNTIME_SECONDS + Config.AGENT_MAX_ACTION_CALLS * (Config.AGENT_ACTION_WAIT_SECONDS + 1) + TURN_MARGIN_SECONDS


def turn_owner(public_id: str, visitor_id: str) -> str:
    return f"{public_id}:{visitor_id}"


def start_message(bot: Bot, message: str, visitor_id: str | None, conversation_id, attachment_ids=None) -> dict:
    """Như receive_message nhưng KHÔNG chờ câu trả lời: lưu tin khách rồi giao lượt cho tác vụ nền và trả ngay {status: "processing", turn_id, ...}.
    Widget hỏi tiến trình + câu trả lời qua turns.read (turn_status). Nhân viên đang tiếp quản thì trả reply=None như receive_message.
    Ném ValueError nếu tin không hợp lệ; lỗi Redis khi giao lượt được ném nguyên (tin khách vẫn được giữ)."""
    conversation, customer_message, visitor_id, paused = _accept_message(bot, message, visitor_id, conversation_id, attachment_ids)
    base = {"conversation_id": conversation.id, "visitor_id": visitor_id, "last_message_id": customer_message.id}
    if paused:
        return {"reply": None, **base}
    turn_id = turns.new_id()
    turns.start(turn_id, turn_owner(bot.public_id, visitor_id))
    turns.progress(turn_id, STEP_ANALYZING)  # bước đầu tiên có ngay, không phải chờ worker
    socketio.start_background_task(run_turn, current_app._get_current_object(), bot.id, conversation.id, customer_message.id, turn_id)
    return {"status": "processing", "turn_id": turn_id, **base}


def run_turn(app, bot_id: int, conversation_id: int, customer_message_id: int, turn_id: str) -> None:
    """Tác vụ nền của 1 lượt: chạy đúng luồng trả lời như receive_message rồi ghi kết quả cho widget qua turns. Các lượt CÙNG hội thoại chạy lần lượt
    (khóa Redis) vì trạng thái hội thoại/bộ nhớ không an toàn khi 2 lượt ghi đồng thời — khách hỏi tiếp trong lúc chờ thì lượt sau xếp hàng."""
    with app.app_context():
        error_text = UI_TEXTS["vi"]["error"]
        try:
            bot = db.session.get(Bot, bot_id)
            conversation = db.session.get(Conversation, conversation_id)
            customer_message = db.session.get(Message, customer_message_id)
            language = dashboard_service.get_or_create_settings(bot).language
            error_text = UI_TEXTS[language if language in UI_TEXTS else "vi"]["error"]  # không qua get_config: nó dựng URL tuyệt đối, cần request mà tác vụ nền không có
            timeout = turn_timeout_seconds()
            with redis_client.lock(f"widget-turn-lock:{conversation_id}", timeout=timeout, blocking_timeout=timeout):
                bot_message = dashboard_service.reply_to_customer(
                    bot, conversation, customer_message, on_progress=lambda code: turns.progress(turn_id, code),
                )
            inbox_service.emit_message(bot.team_id, conversation.id, bot_message.id)
            turns.finish_reply(turn_id, bot_message.content, bot_message.id)
        except Exception:
            # Ranh giới của tác vụ nền: không còn request nào để trả 502, nên ghi log đầy đủ rồi báo lỗi cho widget (không tạo câu trả lời giả)
            logger.exception("widget: lượt bất đồng bộ lỗi (turn=%s bot_id=%s)", turn_id, bot_id)
            db.session.rollback()
            turns.finish_error(turn_id, error_text)


def turn_status(bot: Bot, turn_id: str, visitor_id: str | None, after) -> dict | None:
    """Tiến trình + kết quả của lượt cho widget đúng chủ (public_id + visitor_id). None = không có lượt (sai chủ/hết hạn)."""
    visitor_id = (visitor_id or "").strip()[:MAX_VISITOR_ID_CHARS]
    return turns.read(turn_id, turn_owner(bot.public_id, visitor_id), after)


def staff_messages_since(bot: Bot, conversation_id, visitor_id: str | None, after_id, include_bot: bool = False) -> dict:
    """Tin của nhân viên gửi từ Inbox mà widget chưa nhận (id > after_id). Cùng quy tắc như receive_message:
    phải đúng bot + đúng visitor mới đọc được hội thoại. Trả last_id để widget lần sau hỏi tiếp từ đó.
    include_bot: trả cả tin của bot (kèm "sender") — đường dự phòng để widget nhận câu trả lời của lượt bất đồng bộ khi kênh hỏi tiến trình
    bị lỡ (tải lại trang giữa chừng, mạng rớt); widget khử trùng theo id."""
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
        "messages": [
            {"id": m.id, "content": m.content, **({"sender": m.sender} if include_bot else {})}
            for m in rows if m.sender == "staff" or (include_bot and m.sender == "bot")
        ],
        "last_id": rows[-1].id if rows else after_id,
    }


# ---------------------------------------------------------------- kết quả thực thi hành động (Phase M3)

ACTION_RESULT_STATUSES = ("done", "failed")
# Lý do thất bại widget được phép báo (giá trị lạ -> "error"): dùng để phân biệt selector lỗi thời (element_not_found -> bỏ duyệt action) với các lỗi khác
ACTION_FAIL_REASONS = ("element_not_found", "domain_mismatch", "invalid_spec", "missing_param", "cancelled_by_customer", "error")
MAX_RESULT_DATA_CHARS = 500


def submit_action_result(bot: Bot, pending_id, visitor_id, token, status, reason, data) -> tuple[dict, int]:
    """Widget báo kết quả 1 lệnh. Mọi kiểu sai (không có lệnh, sai bot, sai visitor, sai token) đều trả 404 giống nhau — không tiết lộ lệnh có tồn tại hay không.
    Chỉ ghi được 1 lần (pending -> done/failed); lần thứ hai 409."""
    row = PendingWidgetAction.query.filter_by(id=pending_id, bot_id=bot.id).first() if isinstance(pending_id, int) else None
    conversation = db.session.get(Conversation, row.conversation_id) if row is not None else None
    if (row is None or conversation is None or not isinstance(token, str) or not isinstance(visitor_id, str)
            or not hmac.compare_digest(row.token, token) or conversation.visitor_id != visitor_id.strip()[:MAX_VISITOR_ID_CHARS]):
        return {"error": "Không tìm thấy lệnh."}, 404
    if status not in ACTION_RESULT_STATUSES:
        return {"error": "Trạng thái không hợp lệ."}, 400
    if row.status != "pending":
        return {"error": "Lệnh đã có kết quả."}, 409

    reason = reason if status == "failed" and reason in ACTION_FAIL_REASONS else ("error" if status == "failed" else None)
    data = data.strip()[:MAX_RESULT_DATA_CHARS] if isinstance(data, str) and data.strip() else None
    result = {"status": status, "reason": reason, "data": data}
    row.status, row.result, row.finished_at = status, result, datetime.utcnow()
    if reason == "element_not_found" and row.action_id is not None:
        action = db.session.get(ModuleAction, row.action_id)
        if action is not None:
            modules_service.mark_action_failed(action, reason)  # KHÔNG tự đoán selector mới / tự crawl lại: chỉ bỏ duyệt + cảnh báo trên dashboard
    db.session.commit()
    channel.publish_result(row.id, result)
    return {"ok": True}, 200
