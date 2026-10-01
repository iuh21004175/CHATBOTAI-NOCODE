"""Route layer (API) cho blueprint profile: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, abort, g, jsonify, render_template, request, session
from flask_login import current_user, login_required

from app import permissions
from app.csrf import ensure_csrf_token

from . import service

bp = Blueprint("profile", __name__, url_prefix="/api/profile")
# Trang hồ sơ dạng HTML (Phase D: hiển thị AI Credit). Tách blueprint vì bp ở trên có url_prefix "/api/profile" cho các API JSON.
page_bp = Blueprint("profile_page", __name__)


@page_bp.route("/profile", methods=["GET"])
@login_required
def profile_page():
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    overview = service.credit_overview(team_id)
    if overview is None:
        abort(404)
    membership = g.membership
    return render_template(
        "profile/index.html", user=current_user, active_nav="profile", page_title="Hồ sơ & AI Credit",
        can_topup=permissions.can(membership.role if membership else None, "manage_billing"), csrf_token=ensure_csrf_token(), **overview,
    )


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


@page_bp.app_template_filter("vnd")
def format_vnd(value, signed: bool = False) -> str:
    """Decimal VND -> "9.996,75đ" (dấu chấm ngăn nghìn, dấu phẩy thập phân, bỏ số 0 thừa; tối đa 2 chữ số thập phân)."""
    number = round(float(value or 0), 2)
    text = f"{abs(number):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    sign = "-" if number < 0 else "+" if signed and number > 0 else ""
    return f"{sign}{text}đ"
