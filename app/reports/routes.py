"""Route layer (API) cho blueprint reports: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("reports", __name__, url_prefix="/api/reports")


@bp.route("/overview", methods=["GET"])
@login_required
def overview(**kwargs):
    """Số liệu thống kê tổng hợp (hội thoại, khách hàng, tài liệu theo thời gian)"""
    # TODO: implement — gọi service.overview(...) khi model/DB đã sẵn sàng
    return jsonify(service.overview(request, **kwargs))

@bp.route("/export", methods=["GET"])
@login_required
def export(**kwargs):
    """Xuất báo cáo (chạy nền nếu dữ liệu lớn)"""
    # TODO: implement — gọi service.export(...) khi model/DB đã sẵn sàng
    return jsonify(service.export(request, **kwargs))
