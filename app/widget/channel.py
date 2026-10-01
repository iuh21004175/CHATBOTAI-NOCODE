"""Kênh Socket.IO đẩy lệnh hành động xuống widget của KHÁCH (Phase M3) — phần dùng chung giữa handler kết nối (events.py) và route nội bộ giao lệnh
(app/internal/routes.py). Không phụ thuộc Flask request.

- Namespace riêng "/widget": tách hẳn khỏi namespace mặc định của khung quản trị (app/dashboard/events.py đòi đăng nhập). Cùng 1 instance Flask-SocketIO
  và Redis message queue, không dựng server thứ hai.
- Room theo (public_id, visitor_id): visitor_id do widget sinh ngẫu nhiên từ trước tin nhắn đầu tiên nên biết được ngay cả khi hội thoại chưa tồn tại
  (lượt chat đầu tiên vẫn có thể gọi hành động). visitor_id là bí mật của riêng khách đó — cùng cơ chế mà API widget hiện dùng để đọc hội thoại.
- Presence: tập Redis các kết nối đang mở của (public_id, visitor_id), mỗi phần tử "<sid>|<host của trang nhúng>". Nhờ đó backend biết khách CÓ đang mở widget
  hay không và widget đang ở website nào, mà không cần hỏi vào bộ nhớ của tiến trình Socket.IO (có thể có nhiều tiến trình).
"""
import json
import math
import re

from extensions import redis_client, socketio

NAMESPACE = "/widget"
EVENT = "widget_action"
PRESENCE_TTL_SECONDS = 24 * 3600
VISITOR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def action_room(public_id: str, visitor_id: str) -> str:
    return f"widget_action:{public_id}:{visitor_id}"


def _presence_key(public_id: str, visitor_id: str) -> str:
    return f"widget-conn:{public_id}:{visitor_id}"


def _sid_key(sid: str) -> str:
    return f"widget-sid:{sid}"


def register_connection(public_id: str, visitor_id: str, sid: str, host: str) -> None:
    """Ghi presence + bản đồ ngược sid -> (public_id, visitor_id, host) để dọn khi ngắt kết nối (handler disconnect không nhận lại auth)."""
    key = _presence_key(public_id, visitor_id)
    pipe = redis_client.pipeline()
    pipe.sadd(key, f"{sid}|{host}")
    pipe.expire(key, PRESENCE_TTL_SECONDS)
    pipe.set(_sid_key(sid), json.dumps([public_id, visitor_id, host]), ex=PRESENCE_TTL_SECONDS)
    pipe.execute()


def unregister_connection(sid: str) -> None:
    raw = redis_client.get(_sid_key(sid))
    if not raw:
        return
    try:
        public_id, visitor_id, host = json.loads(raw)
    except (ValueError, TypeError):
        redis_client.delete(_sid_key(sid))
        return
    pipe = redis_client.pipeline()
    pipe.srem(_presence_key(public_id, visitor_id), f"{sid}|{host}")
    pipe.delete(_sid_key(sid))
    pipe.execute()


def connected_hosts(public_id: str, visitor_id: str) -> set[str]:
    """Host của các trang đang mở widget (đang kết nối) của khách này. Rỗng = khách không mở widget (hoặc không hỗ trợ Socket.IO)."""
    return {member.split("|", 1)[1] for member in redis_client.smembers(_presence_key(public_id, visitor_id)) if "|" in member}


def emit_action(public_id: str, visitor_id: str, payload: dict) -> None:
    socketio.emit(EVENT, payload, to=action_room(public_id, visitor_id), namespace=NAMESPACE)


# ---- Kết quả thực thi: widget báo qua HTTP POST (app/widget/routes.py) -> đẩy vào danh sách Redis -> lượt agent đang chờ (app/modules/dispatch.py) nhận ----
RESULT_TTL_SECONDS = 120


def _result_key(pending_id: int) -> str:
    return f"widget-action-result:{pending_id}"


def publish_result(pending_id: int, result: dict) -> None:
    key = _result_key(pending_id)
    pipe = redis_client.pipeline()
    pipe.rpush(key, json.dumps(result, ensure_ascii=False))
    pipe.expire(key, RESULT_TTL_SECONDS)
    pipe.execute()


def wait_result(pending_id: int, timeout_seconds: float) -> dict | None:
    """Chờ tối đa timeout_seconds cho widget báo kết quả. None = quá hạn (KHÔNG có nghĩa là thất bại: kết quả đến muộn vẫn được ghi vào DB)."""
    popped = redis_client.blpop(_result_key(pending_id), timeout=max(1, math.ceil(timeout_seconds)))
    if popped is None:
        return None
    try:
        value = json.loads(popped[1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None
