"""Route layer (API) cho blueprint inbox: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.

Trang giao diện "Tin nhắn" (/inbox) nằm cùng các trang khác ở blueprint dashboard.
"""
from flask import Blueprint, abort, jsonify, request, session
from flask_login import login_required

from app.csrf import verify_csrf_token
from extensions import limiter

from . import service

bp = Blueprint("inbox", __name__, url_prefix="/api/inbox")

CSRF_ERROR = "Phiên làm việc đã hết hạn, vui lòng tải lại trang."


def _team_id() -> int:
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    return team_id


def _conversation_or_404(conversation_id: int):
    return service.get_conversation_for_team(_team_id(), conversation_id) or abort(404)


@bp.route("/conversations", methods=["GET"])
@login_required
def list_conversations():
    """Danh sách hội thoại (dùng chung cho Inbox và Lịch sử chat), lọc theo kênh/trạng thái/thời gian"""
    return jsonify(conversations=service.list_conversations(_team_id(), search=request.args.get("q", "")))


@bp.route("/conversations/<int:conversation_id>", methods=["GET"])
@login_required
def get_conversation(conversation_id):
    """Chi tiết 1 hội thoại kèm tin nhắn (after_id: chỉ lấy tin mới hơn id đó)"""
    conv = _conversation_or_404(conversation_id)
    return jsonify(service.conversation_detail(conv, after_id=max(0, request.args.get("after_id", 0, type=int))))


@bp.route("/conversations/<int:conversation_id>", methods=["PUT"])
@login_required
def update_conversation(conversation_id):
    """Đánh dấu đã xử lý (status=closed) hoặc mở lại (status=open)"""
    conv = _conversation_or_404(conversation_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error=CSRF_ERROR), 400
    payload = request.get_json(silent=True)
    error = service.set_status(_team_id(), conv, payload.get("status") if isinstance(payload, dict) else None)
    if error:
        return jsonify(error=error), 400
    return jsonify(service.conversation_detail(conv))


@bp.route("/conversations/<int:conversation_id>/messages", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def reply_message(conversation_id):
    """Nhân viên gửi tin nhắn trả lời thủ công, phát realtime qua Flask-SocketIO"""
    conv = _conversation_or_404(conversation_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error=CSRF_ERROR), 400
    payload = request.get_json(silent=True)
    message, error = service.reply_message(_team_id(), conv, payload.get("content") if isinstance(payload, dict) else None)
    if error:
        return jsonify(error=error), 400
    return jsonify(message=message), 201
