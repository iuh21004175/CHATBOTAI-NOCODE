"""Route layer của Website Action Engine (Phase M): trang khai báo module theo loại, kết quả phân tích, duyệt hành động.
Không chứa logic nghiệp vụ — logic ở service.py. Thao tác ghi cần quyền `manage_modules` (Chủ nhóm/Quản trị viên); xem thì mọi thành viên của team.
"""
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import login_required

from app import permissions
from app.csrf import ensure_csrf_token, verify_csrf_token
from app.dashboard import service as dashboard_service
from config import Config
from core.website_actions import risk

from . import service

bp = Blueprint("modules", __name__)

RISK_LABELS = {"read_only": "Chỉ đọc / điều hướng", "cart": "Giỏ hàng", "payment": "Thanh toán"}
ACTION_TYPE_LABELS = {"navigate": "Điều hướng", "click": "Bấm nút", "fill_form": "Điền biểu mẫu", "add_to_cart": "Thêm vào giỏ", "read_info": "Đọc thông tin"}
STATUS_LABELS = {
    "pending": "Đang chờ phân tích", "analyzing": "Đang phân tích", "awaiting_domain_confirmation": "Chờ xác nhận domain",
    "ready": "Đã phân tích xong", "failed": "Phân tích thất bại",
}
UPLOAD_STATUS_LABELS = {
    "awaiting_upload": "Chưa tải lên", "uploaded": "Đã tải lên, đang chờ xử lý", "extracted": "Đã phân tích, chờ xác nhận domain",
    "domain_confirmed": "Đã xác nhận domain", "failed": "Lỗi xử lý",
}


def _require_bot(bot_id: int):
    """Bot phải thuộc team đang đăng nhập (multi-tenant), ngược lại 404."""
    team_id = session.get("team_id")
    bot = dashboard_service.get_bot_for_team(bot_id, team_id) if team_id else None
    if bot is None:
        abort(404)
    return bot


def _require_module(bot, module_id: int):
    module = service.get_module(bot, module_id)
    if module is None:
        abort(404)
    return module


def _csrf_ok() -> bool:
    return verify_csrf_token(request.form.get("csrf_token", ""))


def _back(bot, module=None):
    if module is not None:
        return redirect(url_for("modules.detail", bot_id=bot.id, module_id=module.id))
    return redirect(url_for("modules.index", bot_id=bot.id))


def _render(template: str, bot, **context):
    settings = dashboard_service.get_or_create_settings(bot)
    return render_template(
        template, bot=bot, settings=settings, csrf_token=ensure_csrf_token(), risk_labels=RISK_LABELS, type_labels=ACTION_TYPE_LABELS,
        status_labels=STATUS_LABELS, upload_status_labels=UPLOAD_STATUS_LABELS, is_document_reader=service.is_document_reader, **context,
    )


@bp.route("/bots/<int:bot_id>/modules", methods=["GET"])
@login_required
def index(bot_id):
    bot = _require_bot(bot_id)
    types = service.active_types()
    return _render(
        "bots/modules.html", bot, modules=service.list_modules(bot), types_json=service.types_for_client(types), max_urls=Config.MODULE_MAX_URLS,
        attachment_limits={"max_mb": Config.ATTACHMENT_MAX_BYTES // (1024 * 1024), "max_files": Config.ATTACHMENT_MAX_PER_MESSAGE},
    )


@bp.route("/bots/<int:bot_id>/modules", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def create(bot_id):
    bot = _require_bot(bot_id)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return _back(bot)
    type_id = request.form.get("module_type_id", type=int)
    # file_<vai_trò>: tên ô chọn file do server sinh từ module_type_url_roles; ở đây chỉ gom lại — validate bắt buộc/tuỳ chọn nằm ở service theo dữ liệu DB
    files = {key[len("file_"):]: value for key, value in request.files.items() if key.startswith("file_")}
    module, error = service.create_module(bot, type_id, request.form.get("name", ""), files)
    if error:
        flash(error, "error")
        return _back(bot)
    if service.is_document_reader(module.module_type):
        flash("Đã cài module \"Đọc tài liệu\". Khung chat trên website của bạn đã có nút đính kèm tệp cho khách.", "success")
        return _back(bot)
    flash("Đã khai báo module. Hệ thống đang phân tích website ở nền, bạn có thể theo dõi tiến độ tại đây.", "success")
    return _back(bot, module)


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>", methods=["GET"])
@login_required
def detail(bot_id, module_id):
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    if service.is_document_reader(module.module_type):  # không có URL/hành động để xem kết quả: quản lý ngay ở danh sách
        return _back(bot)
    settings = dashboard_service.get_or_create_settings(bot)
    rows = []
    for action in module.actions:
        level = service.effective_risk_of(action)
        url = service.url_of(action)
        if not action.verified and (url is None or url.upload_status != "domain_confirmed"):
            block = "Trang của hành động này chưa được xác nhận domain (xem mục \"Các trang đã khai báo\" bên dưới)."
        else:
            block = None if action.verified else risk.approval_check(level, action.confidence, allow_payment=bool(settings.allow_agent_payment_actions))
        rows.append({"action": action, "risk": level, "block_reason": block})
    # Nhãn vai trò URL theo ĐÚNG loại module (ModuleTypeUrlRole.label) — không hard-code chung: 4 role dùng chung enum module_urls.url_role nhưng
    # mỗi loại module đặt nhãn hiển thị riêng (vd role "product_detail" là "URL trang sản phẩm" ở loại bán hàng, "URL trang dịch vụ" ở loại tư vấn
    # dịch vụ). Cùng cách runner.py dựng role_labels lúc phân tích.
    role_labels = {r.url_role: r.label for r in module.module_type.url_roles}
    required_roles = {r.url_role for r in module.module_type.url_roles if r.is_required}
    return _render(
        "bots/module_detail.html", bot, module=module, rows=rows, domain_warning=service.domain_warning(bot, module),
        thresholds=risk.CONFIDENCE_MIN, role_labels=role_labels, required_roles=required_roles,
    )


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>/urls/<int:url_id>/confirm-domain", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def confirm_domain(bot_id, module_id, url_id):
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    module_url = next((u for u in module.urls if u.id == url_id), None) or abort(404)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.confirm_domain(module_url, request.form.get("source_url", ""))
        flash(error or f"Đã xác nhận domain \"{module_url.source_url}\".", "error" if error else "success")
    return _back(bot, module)


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>/status", methods=["GET"])
@login_required
def status(bot_id, module_id):
    """Trạng thái + tiến độ (dự phòng khi Socket.IO không kết nối được: trang tự hỏi định kỳ)."""
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    return jsonify(id=module.id, status=module.status, done=module.progress_done, total=module.progress_total, error=module.error_message)


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>/analyze", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def analyze(bot_id, module_id):
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.request_analysis(module)
        flash(error or "Đã đưa module vào hàng chờ phân tích lại. Các hành động cần được duyệt lại nếu nội dung thay đổi.", "error" if error else "success")
    return _back(bot, module)


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>/delete", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def delete(bot_id, module_id):
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return _back(bot, module)
    error = service.delete_module(module)
    flash(error or "Đã xoá module.", "error" if error else "success")
    return _back(bot, module) if error else _back(bot)


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>/actions/<int:action_id>/approve", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def approve(bot_id, module_id, action_id):
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    action = service.get_action(module, action_id) or abort(404)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.approve_action(bot, action)
        flash(error or f"Đã duyệt hành động \"{action.action_name}\".", "error" if error else "success")
    return _back(bot, module)


@bp.route("/bots/<int:bot_id>/modules/<int:module_id>/actions/<int:action_id>/unapprove", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def unapprove(bot_id, module_id, action_id):
    bot = _require_bot(bot_id)
    module = _require_module(bot, module_id)
    action = service.get_action(module, action_id) or abort(404)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        service.unapprove_action(action)
        flash(f"Đã bỏ duyệt hành động \"{action.action_name}\".", "success")
    return _back(bot, module)


@bp.route("/bots/<int:bot_id>/modules/settings", methods=["POST"])
@login_required
@permissions.requires("manage_modules")
def settings(bot_id):
    """Công tắc cho phép agent thực hiện hành động thanh toán (mặc định tắt)."""
    bot = _require_bot(bot_id)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        enabled = request.form.get("allow_agent_payment_actions") == "1"
        service.set_payment_switch(bot, enabled)
        flash("Đã bật cho phép agent thực hiện hành động thanh toán (mỗi hành động vẫn phải duyệt riêng và khách phải xác nhận)." if enabled
              else "Đã tắt hành động thanh toán; các hành động thanh toán đã duyệt được bỏ duyệt.", "success")
    return _back(bot)
