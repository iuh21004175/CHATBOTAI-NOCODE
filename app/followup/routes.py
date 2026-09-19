"""Route layer (API) cho blueprint followup: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("followup", __name__, url_prefix="/api/followups")


@bp.route("", methods=["GET"])
@login_required
def list_followups(**kwargs):
    """Danh sách kịch bản FollowUp tự động"""
    # TODO: implement — gọi service.list_followups(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_followups(request, **kwargs))

@bp.route("", methods=["POST"])
@login_required
def create_followup(**kwargs):
    """Tạo kịch bản FollowUp mới (nội dung, lịch gửi)"""
    # TODO: implement — gọi service.create_followup(...) khi model/DB đã sẵn sàng
    return jsonify(service.create_followup(request, **kwargs))

@bp.route("/<int:followup_id>", methods=["PUT"])
@login_required
def update_followup(**kwargs):
    """Cập nhật kịch bản FollowUp"""
    # TODO: implement — gọi service.update_followup(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_followup(request, **kwargs))

@bp.route("/<int:followup_id>", methods=["DELETE"])
@login_required
def delete_followup(**kwargs):
    """Xóa kịch bản FollowUp"""
    # TODO: implement — gọi service.delete_followup(...) khi model/DB đã sẵn sàng
    return jsonify(service.delete_followup(request, **kwargs))
