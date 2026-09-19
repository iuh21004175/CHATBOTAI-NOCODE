"""Route layer (API) cho blueprint inbox: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("inbox", __name__, url_prefix="/api/inbox")


@bp.route("/conversations", methods=["GET"])
@login_required
def list_conversations(**kwargs):
    """Danh sách hội thoại (dùng chung cho Inbox và Lịch sử chat), lọc theo kênh/trạng thái/thời gian"""
    # TODO: implement — gọi service.list_conversations(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_conversations(request, **kwargs))

@bp.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(**kwargs):
    """Chi tiết 1 hội thoại kèm tin nhắn"""
    # TODO: implement — gọi service.get_conversation(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_conversation(request, **kwargs))

@bp.route("/conversations/<int:conversation_id>/messages", methods=["POST"])
@login_required
def reply_message(**kwargs):
    """Nhân viên gửi tin nhắn trả lời thủ công, phát realtime qua Flask-SocketIO"""
    # TODO: implement — gọi service.reply_message(...) khi model/DB đã sẵn sàng
    return jsonify(service.reply_message(request, **kwargs))
