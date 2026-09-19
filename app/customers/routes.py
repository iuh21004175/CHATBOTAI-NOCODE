"""Route layer (API) cho blueprint customers: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("customers", __name__, url_prefix="/api/customers")


@bp.route("", methods=["GET"])
@login_required
def list_customers(**kwargs):
    """Danh sách khách hàng thu thập được từ hội thoại (CRM cơ bản)"""
    # TODO: implement — gọi service.list_customers(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_customers(request, **kwargs))

@bp.route("/<int:customer_id>", methods=["GET"])
@login_required
def get_customer(**kwargs):
    """Chi tiết khách hàng + lịch sử hội thoại liên quan"""
    # TODO: implement — gọi service.get_customer(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_customer(request, **kwargs))

@bp.route("/<int:customer_id>", methods=["PUT"])
@login_required
def update_customer(**kwargs):
    """Cập nhật thông tin khách hàng"""
    # TODO: implement — gọi service.update_customer(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_customer(request, **kwargs))
