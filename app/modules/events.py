"""Socket.IO: đẩy tiến độ phân tích module tới trình duyệt của chủ bot (khung quản trị, ĐÃ đăng nhập) qua room "bot:<id>" có sẵn.

Đây KHÔNG phải kênh của widget khách (app/widget/events.py, namespace /widget, xác thực bằng public_id + Origin): hai kênh khác mục đích, khác cách xác thực.
Gọi được từ worker (không có request); lỗi Redis/Socket.IO không được làm hỏng việc phân tích.
"""
import logging

from app.dashboard.events import bot_room
from extensions import socketio

logger = logging.getLogger(__name__)


def emit_module_status(module, status: str, **extra) -> None:
    payload = {"id": module.id, "status": status, "done": module.progress_done, "total": module.progress_total, **extra}
    try:
        socketio.emit("module_status", payload, to=bot_room(module.bot_id))
    except Exception:
        logger.warning("Không đẩy được trạng thái module %s qua Socket.IO", module.id, exc_info=True)
