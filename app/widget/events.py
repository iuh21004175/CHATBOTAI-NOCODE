"""Socket.IO của WIDGET KHÁCH (Phase M3): nhận lệnh hành động từ server để thực thi trên DOM của website khách.

KHÔNG dùng lại handler của khung quản trị (app/dashboard/events.py): handler đó `return False` nếu chưa đăng nhập nền tảng, mà khách xem widget không đăng nhập.
Ở đây xác thực bằng: (1) public_id của bot (không phải id số tuần tự), (2) header Origin phải thuộc bot_domains của ĐÚNG bot đó — cùng hàm
origin_allowed mà API HTTP của widget dùng (Phase A4). Không đọc current_user/session đăng nhập ở bất kỳ đâu trong file này.
Server chỉ đẩy lệnh xuống (widget_action); kết quả widget báo về qua HTTP POST (app/widget/routes.py), không qua socket.
"""
import logging

from flask import request
from flask_socketio import join_room

from app.widget import channel, domains, service
from extensions import socketio

logger = logging.getLogger(__name__)


def _origin() -> str:
    # WebSocket luôn gửi Origin; XHR-polling cùng origin có thể không gửi -> Referer như route HTTP của widget (routes._authorize)
    return request.headers.get("Origin") or service.origin_of(request.headers.get("Referer", ""))


@socketio.on("connect", namespace=channel.NAMESPACE)
def on_connect(auth=None):
    """Từ chối (False) nếu thiếu/không đúng public_id + visitor_id, hoặc Origin không thuộc domain đã khai báo cho bot."""
    if not isinstance(auth, dict):
        logger.info("widget socket refused: thiếu auth")
        return False
    public_id, visitor_id = auth.get("public_id"), auth.get("visitor_id")
    if not isinstance(public_id, str) or not isinstance(visitor_id, str) or not channel.VISITOR_ID_RE.match(visitor_id):
        logger.info("widget socket refused: public_id/visitor_id không hợp lệ")
        return False
    bot = service.get_bot(public_id)
    origin = _origin()
    if bot is None or not service.origin_allowed(origin, service.allowed_domains(bot)):
        logger.info("widget socket refused: bot=%s origin=%r", bot.id if bot else None, origin)  # không log public_id/visitor_id (bí mật)
        return False
    host = domains.normalize_domain(origin)
    join_room(channel.action_room(public_id, visitor_id))
    channel.register_connection(public_id, visitor_id, request.sid, host)
    return None


@socketio.on("disconnect", namespace=channel.NAMESPACE)
def on_disconnect(*_args):
    try:
        channel.unregister_connection(request.sid)
    except Exception:
        # Redis chập chờn khi dọn presence: phần tử có TTL nên tự hết hạn; chỉ ghi log
        logger.warning("Không dọn được presence widget khi ngắt kết nối", exc_info=True)
