"""Route layer (API) cho blueprint bots: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("bots", __name__, url_prefix="/api/bots")


@bp.route("/dashboard", methods=["GET"])
@login_required
def dashboard(**kwargs):
    """Tổng quan Bảng điều khiển: số bot, trạng thái 4 bước, số tài liệu/hội thoại"""
    # TODO: implement — gọi service.dashboard(...) khi model/DB đã sẵn sàng
    return jsonify(service.dashboard(request, **kwargs))

@bp.route("", methods=["GET"])
@login_required
def list_bots(**kwargs):
    """Danh sách bot thuộc team đang đăng nhập"""
    # TODO: implement — gọi service.list_bots(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_bots(request, **kwargs))

@bp.route("", methods=["POST"])
@login_required
def create_bot(**kwargs):
    """Tạo bot mới cho team"""
    # TODO: implement — gọi service.create_bot(...) khi model/DB đã sẵn sàng
    return jsonify(service.create_bot(request, **kwargs))

@bp.route("/<int:bot_id>", methods=["GET"])
@login_required
def get_bot(**kwargs):
    """Chi tiết 1 bot"""
    # TODO: implement — gọi service.get_bot(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_bot(request, **kwargs))

@bp.route("/<int:bot_id>", methods=["PUT"])
@login_required
def update_bot(**kwargs):
    """Cập nhật thông tin bot"""
    # TODO: implement — gọi service.update_bot(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_bot(request, **kwargs))

@bp.route("/<int:bot_id>", methods=["DELETE"])
@login_required
def delete_bot(**kwargs):
    """Xóa bot"""
    # TODO: implement — gọi service.delete_bot(...) khi model/DB đã sẵn sàng
    return jsonify(service.delete_bot(request, **kwargs))

@bp.route("/<int:bot_id>/settings", methods=["GET"])
@login_required
def get_bot_settings(**kwargs):
    """Lấy cấu hình hành vi (Bước 1 — Thiết lập): lời chào, hướng dẫn, ngôn ngữ, temperature"""
    # TODO: implement — gọi service.get_bot_settings(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_bot_settings(request, **kwargs))

@bp.route("/<int:bot_id>/settings", methods=["PUT"])
@login_required
def update_bot_settings(**kwargs):
    """Cập nhật cấu hình hành vi bot"""
    # TODO: implement — gọi service.update_bot_settings(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_bot_settings(request, **kwargs))
