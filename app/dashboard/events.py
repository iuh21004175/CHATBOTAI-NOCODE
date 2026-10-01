"""Socket.IO: đẩy trạng thái xử lý tài liệu (Bước 2) tới trình duyệt theo thời gian thực.

Luồng: worker (tiến trình riêng) gọi emit_document_status() -> Redis (message queue) -> server web
-> trình duyệt đang mở trang Cơ sở tri thức của đúng bot đó. Mỗi bot 1 room ("bot:<id>"); trình duyệt
chỉ vào được room của bot thuộc team đang đăng nhập nên không nhận được tài liệu của khách hàng khác.
"""
import logging
from urllib.parse import urlparse

from flask import request, session
from flask_login import current_user
from flask_socketio import join_room

from app import permissions
from extensions import socketio

from . import service

logger = logging.getLogger(__name__)


def bot_room(bot_id: int) -> str:
    return f"bot:{bot_id}"


def session_room(socket_key: str) -> str:
    """Room theo PHIÊN TRÌNH DUYỆT (khóa ngẫu nhiên đặt lúc đăng nhập, xem auth.service.login): mọi tab cùng trình duyệt chung khóa, thiết bị
    khác của cùng người dùng thì không. Phiên là cookie ký phía client nên đây là cách duy nhất để đóng ĐÚNG các kết nối của phiên vừa đăng xuất."""
    return f"session:{socket_key}"


def disconnect_session_sockets(socket_key: str | None) -> int:
    """Đăng xuất phải chấm dứt luôn các kết nối Socket.IO đang mở của phiên đó: kết nối chỉ được xác thực lúc connect và đã vào room của team,
    nên nếu để nguyên thì tab khác của trình duyệt vừa đăng xuất vẫn nhận tin nhắn khách cho tới khi tải lại trang. Trả số kết nối đã đóng."""
    server = socketio.server
    if not socket_key or server is None:
        return 0
    sids = [sid for sid, _ in server.manager.get_participants("/", session_room(socket_key))]
    for sid in sids:
        try:
            server.disconnect(sid)
        except Exception:
            # Cookie đăng nhập đã bị xóa nên kết nối mới không thể xác thực; kết nối cũ không đóng được thì ghi log, không làm hỏng việc đăng xuất
            logger.warning("Không đóng được kết nối Socket.IO %s khi đăng xuất", sid, exc_info=True)
    return len(sids)


def emit_document_status(document, status: str, **extra) -> None:
    """Gọi được từ worker (không có request). Lỗi Redis/Socket.IO không được làm hỏng việc xử lý tài liệu."""
    payload = {"id": document.id, "status": status, "filename": document.filename, **extra}
    try:
        socketio.emit("document_status", payload, to=bot_room(document.bot_id))
    except Exception:
        logger.warning("Không đẩy được trạng thái tài liệu %s qua Socket.IO", document.id, exc_info=True)


@socketio.on("connect")
def on_connect():
    if not current_user.is_authenticated:
        return False
    # CORS của Socket.IO đang mở "*": chặn kết nối từ trang web khác dùng cookie đăng nhập của người dùng
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return False
    socket_key = session.get("socket_key")
    if socket_key:
        join_room(session_room(socket_key))
    return None


@socketio.on("join_bot")
def on_join_bot(data):
    """Trình duyệt xin nhận sự kiện của 1 bot. Trả {ok: bool} làm ack."""
    bot_id = data.get("bot_id") if isinstance(data, dict) else None
    if not current_user.is_authenticated or not isinstance(bot_id, int):
        return {"ok": False}
    # Socket.IO không đi qua before_request: xác thực lại tư cách thành viên thay vì tin session["team_id"]
    team_id = permissions.resolve_team_id(current_user.id, session.get("team_id"))
    if not team_id:
        return {"ok": False}
    if service.get_bot_for_team(bot_id, team_id) is None:
        return {"ok": False}
    join_room(bot_room(bot_id))
    return {"ok": True}
