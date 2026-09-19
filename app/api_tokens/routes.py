"""Route layer (API) cho blueprint api_tokens: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("api_tokens", __name__, url_prefix="/api/api-tokens")


@bp.route("", methods=["GET"])
@login_required
def list_tokens(**kwargs):
    """Danh sách API Token của team (không trả token_hash gốc)"""
    # TODO: implement — gọi service.list_tokens(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_tokens(request, **kwargs))

@bp.route("", methods=["POST"])
@login_required
def create_token(**kwargs):
    """Tạo API Token mới, trả token plaintext đúng 1 lần"""
    # TODO: implement — gọi service.create_token(...) khi model/DB đã sẵn sàng
    return jsonify(service.create_token(request, **kwargs))

@bp.route("/<int:token_id>", methods=["PUT"])
@login_required
def update_token(**kwargs):
    """Cập nhật scope/hạn sử dụng của token"""
    # TODO: implement — gọi service.update_token(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_token(request, **kwargs))

@bp.route("/<int:token_id>", methods=["DELETE"])
@login_required
def revoke_token(**kwargs):
    """Thu hồi (xóa) token"""
    # TODO: implement — gọi service.revoke_token(...) khi model/DB đã sẵn sàng
    return jsonify(service.revoke_token(request, **kwargs))
