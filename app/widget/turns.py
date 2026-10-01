"""Trạng thái 1 lượt trả lời BẤT ĐỒNG BỘ (Redis) — dùng chung cho widget khách (app/widget) và khung xem trước ở Bước 3 (app/dashboard).

Vì sao có: lượt trả lời của agent kéo dài nhiều giây. Thay vì bắt request HTTP chờ tới khi xong rồi mới hiện một lần, request nhận tin chỉ lưu tin khách,
giao việc cho tác vụ nền rồi trả ngay mã lượt (turn_id). Tác vụ nền ghi các SỰ KIỆN vào danh sách Redis của lượt:
  {"type": "step",  "code": "analyzing|searching|acting|composing"}   tiến trình THẬT (protocol.PROGRESS_*), khách thấy agent đang làm gì
  {"type": "reply", "reply": "...", "message_id": 123}                 câu trả lời cuối (đã lưu vào DB trước khi phát)
  {"type": "error", "message": "..."}                                  lượt lỗi (không có câu trả lời giả)
Widget hỏi định kỳ GET .../turns/<turn_id>?after=<n> và nhận các sự kiện từ vị trí n (không tiêu thụ: hỏi lại cùng vị trí vẫn ra cùng kết quả nên
request mạng rớt không làm mất sự kiện). Chỉ dùng HTTP + Redis đã có sẵn — không phụ thuộc Socket.IO (có thể bị proxy/tunnel chặn).

Bảo mật: turn_id ngẫu nhiên khó đoán VÀ gắn với "chủ" (widget: public_id + visitor_id; xem trước: bot + người dùng đăng nhập) — sai chủ = như không tồn tại.
"""
import hmac
import json
import logging
import secrets

import redis

from extensions import redis_client

logger = logging.getLogger(__name__)

TTL_SECONDS = 15 * 60
EVENT_STEP, EVENT_REPLY, EVENT_ERROR = "step", "reply", "error"
TERMINAL_EVENTS = (EVENT_REPLY, EVENT_ERROR)


def new_id() -> str:
    return secrets.token_urlsafe(18)


def _events_key(turn_id: str) -> str:
    return f"widget-turn:{turn_id}:events"


def _owner_key(turn_id: str) -> str:
    return f"widget-turn:{turn_id}:owner"


def start(turn_id: str, owner: str) -> None:
    redis_client.set(_owner_key(turn_id), owner, ex=TTL_SECONDS)


def _push(turn_id: str, event: dict) -> None:
    key = _events_key(turn_id)
    pipe = redis_client.pipeline()
    pipe.rpush(key, json.dumps(event, ensure_ascii=False))
    pipe.expire(key, TTL_SECONDS)
    pipe.execute()


def progress(turn_id: str, code: str) -> None:
    """Ghi 1 bước tiến trình. Chỉ là thông tin hiển thị: Redis lỗi thì ghi log và lượt vẫn chạy tiếp (câu trả lời cuối đi qua finish_*, nơi lỗi được báo)."""
    try:
        _push(turn_id, {"type": EVENT_STEP, "code": str(code)})
    except redis.RedisError:
        logger.warning("không ghi được tiến trình %s của lượt %s", code, turn_id, exc_info=True)


def finish_reply(turn_id: str, reply: str, message_id: int | None) -> None:
    _push(turn_id, {"type": EVENT_REPLY, "reply": reply, "message_id": message_id})


def finish_error(turn_id: str, message: str) -> None:
    _push(turn_id, {"type": EVENT_ERROR, "message": message})


def read(turn_id: str, owner: str, after: int) -> dict | None:
    """Các sự kiện của lượt từ vị trí `after`. None nếu lượt không tồn tại/đã hết hạn/không thuộc `owner` (không phân biệt để không lộ lượt của người khác)."""
    stored_owner = redis_client.get(_owner_key(turn_id))
    if stored_owner is None or not hmac.compare_digest(stored_owner, owner):
        return None
    after = after if isinstance(after, int) and after > 0 else 0
    raws = redis_client.lrange(_events_key(turn_id), after, -1)
    events = []
    for raw in raws:
        try:
            event = json.loads(raw)
        except ValueError:
            logger.warning("bỏ sự kiện hỏng của lượt %s", turn_id)
            event = None
        if isinstance(event, dict):
            events.append(event)
    return {"events": events, "next": after + len(raws), "done": any(e.get("type") in TERMINAL_EVENTS for e in events)}
