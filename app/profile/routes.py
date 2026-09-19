"""Route layer (API) cho blueprint profile: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("profile", __name__, url_prefix="/api/profile")


@bp.route("", methods=["GET"])
@login_required
def get_profile(**kwargs):
    """Thông tin user hiện tại"""
    # TODO: implement — gọi service.get_profile(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_profile(request, **kwargs))

@bp.route("", methods=["PUT"])
@login_required
def update_profile(**kwargs):
    """Cập nhật hồ sơ user"""
    # TODO: implement — gọi service.update_profile(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_profile(request, **kwargs))

@bp.route("/team", methods=["GET"])
@login_required
def get_team(**kwargs):
    """Thông tin team + gói cước hiện tại"""
    # TODO: implement — gọi service.get_team(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_team(request, **kwargs))

@bp.route("/team", methods=["PUT"])
@login_required
def update_team(**kwargs):
    """Cập nhật thông tin team"""
    # TODO: implement — gọi service.update_team(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_team(request, **kwargs))
