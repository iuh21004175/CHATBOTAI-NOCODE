"""Socket.IO cho Inbox: nhân viên đang mở trang Tin nhắn nhận sự kiện "inbox_message" của team mình.

Room chỉ được lấy từ team_id trong session đăng nhập (không nhận id từ client) nên không thể nghe lén
hội thoại của team khác. Kết nối/xác thực dùng chung handler "connect" ở app/dashboard/events.py.
"""
from flask import session
from flask_login import current_user
from flask_socketio import join_room

from app import permissions
from extensions import socketio

from . import service


@socketio.on("join_team")
def on_join_team(_data=None):
    """Trình duyệt xin nhận tin nhắn mới của team đang đăng nhập. Trả {ok: bool} làm ack."""
    if not current_user.is_authenticated:
        return {"ok": False}
    # Socket.IO không đi qua before_request: xác thực lại tư cách thành viên thay vì tin session["team_id"]
    team_id = permissions.resolve_team_id(current_user.id, session.get("team_id"))
    if not team_id:
        return {"ok": False}
    join_room(service.room(team_id))
    return {"ok": True}
