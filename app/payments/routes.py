"""Route layer nạp AI Credit qua payOS: tạo đơn (từ trang /profile), trang quay lại/hủy sau khi thanh toán, và webhook payOS gọi về.
Logic nghiệp vụ ở service.py; quyền nạp tiền: permissions "manage_billing"."""
import logging

from flask import Blueprint, abort, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_required

from app import permissions
from app.csrf import verify_csrf_token
from config import Config
from core import payos_client as payos
from extensions import limiter

from . import service

logger = logging.getLogger(__name__)
bp = Blueprint("payments", __name__)

STATUS_MESSAGES = {
    "paid": ("Thanh toán thành công, AI Credit đã được cộng vào tài khoản.", "success"),
    "cancelled": ("Bạn đã hủy thanh toán, chưa bị trừ tiền.", "error"),
    "expired": ("Đơn nạp đã hết hạn, vui lòng tạo đơn mới.", "error"),
    "failed": ("Không tạo được đơn thanh toán, vui lòng thử lại.", "error"),
}


def _public_base() -> str:
    return Config.PUBLIC_BASE_URL or request.url_root.rstrip("/")


@bp.route("/profile/topup", methods=["POST"])
@login_required
@permissions.requires("manage_billing")
@limiter.limit("10 per minute")
def topup():
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    back = redirect(url_for("profile_page.profile_page"))
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return back
    if not service.is_configured():
        flash("Cổng thanh toán chưa được cấu hình, vui lòng liên hệ quản trị viên.", "error")
        return back
    # "custom" = gói 1.000.000+ (khách nhập số tiền); còn lại là giá trị của gói cố định
    raw = request.form.get("custom_amount") if request.form.get("package") == "custom" else request.form.get("package")
    amount = service.parse_amount(raw)
    error = service.validate_amount(amount) if amount is not None else "Số tiền không hợp lệ."
    if error:
        flash(error, "error")
        return back
    base = _public_base()
    try:
        order = service.create_order(
            team_id, current_user.id, amount,
            return_url=f"{base}{url_for('payments.topup_return')}", cancel_url=f"{base}{url_for('payments.topup_return')}",
        )
    except payos.PayOSError as exc:
        logger.error("payos: tạo link thanh toán lỗi: %s", exc)
        flash("Không tạo được liên kết thanh toán, vui lòng thử lại sau.", "error")
        return back
    return redirect(order.checkout_url, code=303)


@bp.route("/profile/topup/return", methods=["GET"])
@login_required
def topup_return():
    """payOS đưa khách về đây (cả khi thanh toán xong lẫn khi hủy). Tham số trên URL KHÔNG được tin: chỉ lấy orderCode rồi hỏi lại payOS."""
    team_id = session.get("team_id")
    back = redirect(url_for("profile_page.profile_page"))
    order_code = request.args.get("orderCode", type=int)
    order = service.order_for_team(team_id, order_code) if team_id and order_code else None
    if order is None:
        return back
    try:
        status = service.sync_order(order)
    except payos.PayOSError as exc:
        logger.error("payos: đồng bộ đơn %s lỗi: %s", order.order_code, exc)
        flash("Chưa xác nhận được kết quả thanh toán, hệ thống sẽ tự cập nhật khi payOS báo về. Vui lòng kiểm tra lại sau ít phút.", "error")
        return back
    if status in STATUS_MESSAGES:
        message, category = STATUS_MESSAGES[status]
        flash(message, category)
    else:
        flash("Đơn đang chờ thanh toán. Khi payOS xác nhận, AI Credit sẽ tự được cộng.", "success")
    return back


@bp.route("/payos/webhook", methods=["POST"])
def webhook():
    """payOS gọi khi có giao dịch. Chữ ký sai -> 400; hợp lệ -> 200 (kể cả đơn không thuộc hệ thống, để payOS không gửi lại vô hạn)."""
    payload = request.get_json(silent=True)
    try:
        result = service.handle_webhook(payload)
    except payos.PayOSSignatureError:
        logger.warning("payos: webhook chữ ký không hợp lệ từ %s", request.remote_addr)
        return jsonify(success=False, error="invalid signature"), 400
    return jsonify(success=True, result=result), 200
