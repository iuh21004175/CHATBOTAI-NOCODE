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

from extensions import socketio

from . import service

logger = logging.getLogger(__name__)


def bot_room(bot_id: int) -> str:
    return f"bot:{bot_id}"


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
    return None


@socketio.on("join_bot")
def on_join_bot(data):
    """Trình duyệt xin nhận sự kiện của 1 bot. Trả {ok: bool} làm ack."""
    bot_id = data.get("bot_id") if isinstance(data, dict) else None
    team_id = session.get("team_id")
    if not current_user.is_authenticated or not isinstance(bot_id, int) or not team_id:
        return {"ok": False}
    if service.get_bot_for_team(bot_id, team_id) is None:
        return {"ok": False}
    join_room(bot_room(bot_id))
    return {"ok": True}
