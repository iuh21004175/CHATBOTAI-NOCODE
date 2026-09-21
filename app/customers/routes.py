"""Route layer (API) cho blueprint customers: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.

Trang giao diện "Khách hàng" nằm cùng các trang khác ở blueprint dashboard (app/dashboard/routes.py).
"""
from flask import Blueprint, abort, jsonify, request, session
from flask_login import login_required

from app.csrf import verify_csrf_token

from . import service

bp = Blueprint("customers", __name__, url_prefix="/api/customers")


def _team_id() -> int:
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    return team_id


def _customer_or_404(customer_id: int):
    return service.get_customer(_team_id(), customer_id) or abort(404)


@bp.route("", methods=["GET"])
@login_required
def list_customers():
    """Danh sách khách hàng thu thập được từ hội thoại (CRM cơ bản)"""
    return jsonify(
        service.list_customers(
            _team_id(),
            search=request.args.get("q", ""),
            channel=request.args.get("channel", ""),
            stage=request.args.get("stage", ""),
            status=request.args.get("status", ""),
            page=request.args.get("page", 1, type=int),
        )
    )


@bp.route("/<int:customer_id>", methods=["GET"])
@login_required
def get_customer(customer_id):
    """Chi tiết khách hàng + lịch sử hội thoại liên quan"""
    return jsonify(service.customer_detail(_customer_or_404(customer_id)))


@bp.route("/<int:customer_id>", methods=["PUT"])
@login_required
def update_customer(customer_id):
    """Cập nhật thông tin khách hàng"""
    customer = _customer_or_404(customer_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error="Phiên làm việc đã hết hạn, vui lòng tải lại trang."), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Dữ liệu gửi lên không hợp lệ."), 400
    error = service.update_customer(customer, payload)
    if error:
        return jsonify(error=error), 400
    return jsonify(service.customer_detail(customer))
