"""Route layer của App Builder (AB0). Hai nhóm tách bạch:

1. Quản trị app  (/builder/...): tạo, duyệt Spec, sinh lại, xem trạng thái/chi phí/dung lượng. Ghi cần quyền `manage_apps` (Chủ nhóm/Quản trị viên).
2. Chạy app      (/apps/<public_id>/...): phục vụ file tĩnh đã sinh + API dữ liệu `_api` mà platform-sdk gọi. Đây là BACKEND CỐ ĐỊNH: mọi kiểm tra (đăng nhập,
   thuộc team, CSRF, kiểu dữ liệu, hạn mức) ở server — code giao diện do LLM sinh không thể bỏ qua.

Người dùng cuối của app = thành viên nhóm (AB1): Chủ nhóm/Quản trị viên toàn quyền, thành viên theo VAI TRÒ APP được gán (app/builder/access.py), chưa gán = không vào được.
Mọi thao tác ghi được ghi nhật ký (builder_app_audit). Trang app chạy cùng origin với
nền tảng (domain con riêng + CSP chặt hơn ở AB4); bù lại CSP dưới đây chỉ cho script/style/ảnh/kết nối về chính origin và quét tĩnh chặn code tự gọi mạng.
"""
from __future__ import annotations

import html
import json
import logging

from flask import Blueprint, abort, flash, jsonify, make_response, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import permissions
from app.builder import access, codegen, service, tenant_db
from app.csrf import ensure_csrf_token, verify_csrf_token
from config import Config
from extensions import limiter

logger = logging.getLogger("builder.routes")
bp = Blueprint("builder", __name__)

STATUS_LABELS = {
    "spec_generating": "Đang thiết kế Spec", "spec_ready": "Chờ bạn duyệt Spec", "generating": "Đang sinh ứng dụng", "ready": "Đã sẵn sàng", "failed": "Có lỗi",
}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8"}
APP_CSP = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
FIELD_TYPE_LABELS = {
    "string": "Chuỗi", "text": "Văn bản dài", "integer": "Số nguyên", "decimal": "Số thập phân", "boolean": "Có/Không", "date": "Ngày", "datetime": "Ngày giờ", "ref": "Tham chiếu",
}


def _team_id() -> int:
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    return team_id


def _require_app(app_id: int):
    """App của team đang chọn mà người dùng được phép mở (người quản lý, hoặc thành viên đã được gán vai trò); ngược lại 404 (không lộ sự tồn tại)."""
    row = service.get_app_for_team(app_id, _team_id())
    if row is None or not access.can_open(row, current_user.id, permissions.current_role()):
        abort(404)
    return row


def _csrf_ok() -> bool:
    return verify_csrf_token(request.form.get("csrf_token", ""))


# ------------------------------------------------------------------------------------------------------------------- quản trị app
@bp.route("/builder", methods=["GET"])
@login_required
def index():
    apps = service.list_apps(_team_id(), current_user.id, permissions.current_role())
    return render_template(
        "builder/index.html", apps=apps, status_of=lambda a: service.effective_status(a)[0], status_labels=STATUS_LABELS, csrf_token=ensure_csrf_token(),
        prompt_max=Config.BUILDER_PROMPT_MAX_CHARS, max_apps=Config.BUILDER_MAX_APPS_PER_TEAM,
    )


@bp.route("/builder/apps", methods=["POST"])
@login_required
@permissions.requires("manage_apps")
@limiter.limit("10 per hour")
def create():
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("builder.index"))
    try:
        row = service.create_app(_team_id(), current_user.id, request.form.get("prompt", ""))
    except service.BuilderError as exc:
        flash(str(exc), "error")
        return redirect(url_for("builder.index"))
    return redirect(url_for("builder.detail", app_id=row.id))


@bp.route("/builder/apps/<int:app_id>", methods=["GET"])
@login_required
def detail(app_id):
    row = _require_app(app_id)
    can_manage = permissions.can(permissions.current_role(), "manage_apps")
    status, error = service.effective_status(row)
    version = service.current_version(row)
    usage = service.storage_usage_bytes(row)
    counts = None
    if row.db_name and row.spec:
        try:
            counts = tenant_db.record_counts(row.db_name, row.spec)
        except tenant_db.TenantDbError:
            logger.exception("Không đếm được bản ghi database %s", row.db_name)
    return render_template(
        "builder/detail.html", app=row, status=status, error=error, status_labels=STATUS_LABELS, version=version, csrf_token=ensure_csrf_token(),
        usage_bytes=usage, quota_bytes=row.storage_quota_mb * 1024 * 1024, counts=counts, field_type_labels=FIELD_TYPE_LABELS,
        collection_labels={c["name"]: c["label"] for c in (row.spec or {}).get("collections", [])},
        members=access.member_overview(row) if can_manage else [], audit=service.recent_audit(row.id) if can_manage else [],
        user_names={m["user_id"]: m["name"] for m in access.member_overview(row)} if can_manage else {},
        my_access=access.resolve(row, row.spec or {"roles": []}, current_user.id, permissions.current_role()),
        preview_url=url_for("builder.run_page", public_id=row.public_id, path="") if version else None,
    )


@bp.route("/builder/apps/<int:app_id>/status", methods=["GET"])
@login_required
def status(app_id):
    row = _require_app(app_id)
    state, error = service.effective_status(row)
    return jsonify({"status": state, "label": STATUS_LABELS[state], "error": error})


def _action(app_id: int, fn, success: str):
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("builder.detail", app_id=app_id))
    try:
        fn(app_id, _team_id())
    except service.BuilderError as exc:
        flash(str(exc), "error")
    else:
        flash(success, "success")
    return redirect(url_for("builder.detail", app_id=app_id))


@bp.route("/builder/apps/<int:app_id>/approve", methods=["POST"])
@login_required
@permissions.requires("manage_apps")
def approve(app_id):
    _require_app(app_id)
    return _action(app_id, service.approve, "Đã duyệt Spec. Hệ thống đang sinh ứng dụng.")


@bp.route("/builder/apps/<int:app_id>/retry", methods=["POST"])
@login_required
@permissions.requires("manage_apps")
def retry(app_id):
    _require_app(app_id)
    return _action(app_id, service.retry, "Đã bắt đầu thử lại.")


@bp.route("/builder/apps/<int:app_id>/members", methods=["POST"])
@login_required
@permissions.requires("manage_apps")
def set_member(app_id):
    """Gán / đổi / thu hồi vai trò app của 1 thành viên (role_id rỗng = thu hồi)."""
    _require_app(app_id)
    if not _csrf_ok():
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("builder.detail", app_id=app_id))
    try:
        service.set_member_role(app_id, _team_id(), current_user.id, request.form.get("user_id", type=int) or 0, request.form.get("role_id", "").strip())
    except service.BuilderError as exc:
        flash(str(exc), "error")
    else:
        flash("Đã cập nhật vai trò.", "success")
    return redirect(url_for("builder.detail", app_id=app_id))


@bp.route("/builder/apps/<int:app_id>/regenerate-spec", methods=["POST"])
@login_required
@permissions.requires("manage_apps")
def regenerate_spec(app_id):
    _require_app(app_id)
    return _action(app_id, service.regenerate_spec, "Đang thiết kế lại Spec.")


# ----------------------------------------------------------------------------------------------------------------------- chạy app
def _run_app(public_id: str):
    """App của team đang đăng nhập + phiên bản đang chạy; 404 nếu không phải (không lộ sự tồn tại của app team khác)."""
    row = service.get_app_by_public_id(public_id, _team_id())
    version = service.current_version(row) if row else None
    if row is None or version is None or row.status == "spec_generating":
        abort(404)
    return row, version


@bp.route("/apps/<public_id>/", defaults={"path": ""}, methods=["GET"])
@bp.route("/apps/<public_id>/<path:path>", methods=["GET"])
@login_required
def run_page(public_id, path):
    row, version = _run_app(public_id)
    grants = _access(row, version)
    if not grants.has_access:
        abort(403)  # thành viên chưa được gán vai trò: biết app tồn tại (cùng team) nhưng không được dùng
    name = path or "index.html"
    content = version.files.get(name)
    if content is None:
        abort(404)
    if name.endswith(".html"):
        perms_json = html.escape(json.dumps(grants.as_dict(version.spec)), quote=True)
        content = content.replace(codegen.CSRF_PLACEHOLDER, ensure_csrf_token()).replace(codegen.PERMS_PLACEHOLDER, perms_json)
    response = make_response(content)
    response.headers["Content-Type"] = CONTENT_TYPES.get("." + name.rsplit(".", 1)[-1], "text/plain; charset=utf-8")
    response.headers["Content-Security-Policy"] = APP_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


def _access(row, version) -> access.Access:
    return access.resolve(row, version.spec, current_user.id, permissions.current_role())


def _need(grants: access.Access, spec: dict, collection: str, action: str) -> None:
    """404 nếu collection không có trong Spec, 403 nếu vai trò hiện tại không có hành động đó — kiểm ở SERVER cho mọi API, bất kể giao diện ẩn nút hay không."""
    if not any(c["name"] == collection for c in spec["collections"]):
        raise tenant_db.RecordError(f'Collection "{collection}" không tồn tại', status=404)
    if not grants.allows(collection, action):
        raise tenant_db.RecordError("Bạn không có quyền thực hiện thao tác này.", status=403)


def _api_error(message: str, status: int, errors: dict | None = None):
    return jsonify({"message": message, "errors": errors or {}}), status


def _api(view):
    """Cổng chung của mọi API dữ liệu: đăng nhập (401 JSON, không redirect), CSRF cho thao tác ghi, app thuộc team (404), và đổi lỗi nghiệp vụ -> JSON có mã đúng."""
    from functools import wraps

    @wraps(view)
    def wrapper(public_id, *args, **kwargs):
        if not current_user.is_authenticated:
            return _api_error("Bạn cần đăng nhập.", 401)
        if request.method != "GET" and not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
            return _api_error("Phiên làm việc đã hết hạn, hãy tải lại trang.", 403)
        row, version = _run_app(public_id)
        if not row.db_name:
            return _api_error("Ứng dụng chưa có cơ sở dữ liệu.", 409)
        grants = _access(row, version)
        if not grants.has_access:
            return _api_error("Bạn chưa được gán vai trò trong ứng dụng này.", 403)
        try:
            return view(row, version.spec, grants, *args, **kwargs)
        except tenant_db.RecordError as exc:
            return _api_error(str(exc), exc.status, exc.errors)
        except tenant_db.QuotaExceeded as exc:
            return _api_error(str(exc), 413)
        except tenant_db.TenantDbError as exc:
            logger.error("Database app %s lỗi: %s", row.id, exc)
            return _api_error("Không kết nối được cơ sở dữ liệu của ứng dụng.", 503)

    return wrapper


def _json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise tenant_db.RecordError("Nội dung gửi lên phải là JSON dạng đối tượng")
    return data


def _filters():
    raw = request.args.get("filters")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise tenant_db.RecordError("filters phải là JSON hợp lệ") from exc
    if not isinstance(value, dict):
        raise tenant_db.RecordError("filters phải là đối tượng JSON")
    return value


@bp.route("/apps/<public_id>/_api/_schema", methods=["GET"])
@_api
def api_schema(row, spec, grants):
    """Spec rút gọn cho giao diện: chỉ collection mà người xem có quyền view (và màn hình tương ứng); không lộ ma trận vai trò."""
    visible = {c["name"] for c in spec["collections"] if grants.allows(c["name"], "view")}
    return jsonify({
        "name": spec["name"], "collections": [c for c in spec["collections"] if c["name"] in visible],
        "screens": [s for s in spec["screens"] if s["type"] != "table" or s["collection"] in visible],
    })


@bp.route("/apps/<public_id>/_api/_me", methods=["GET"])
@_api
def api_me(row, spec, grants):
    return jsonify({"id": current_user.id, "email": current_user.email, "full_name": current_user.full_name, "role": grants.role_label, "permissions": grants.as_dict(spec)})


@bp.route("/apps/<public_id>/_api/<collection>", methods=["GET"])
@_api
def api_list(row, spec, grants, collection):
    _need(grants, spec, collection, "view")
    return jsonify(tenant_db.list_records(
        row.db_name, spec, collection, page=request.args.get("page", 1, type=int), per_page=request.args.get("per_page", 20, type=int), q=request.args.get("q", ""),
        sort=request.args.get("sort", "id"), order="asc" if request.args.get("order") == "asc" else "desc", filters=_filters(),
    ))


@bp.route("/apps/<public_id>/_api/<collection>", methods=["POST"])
@_api
def api_create(row, spec, grants, collection):
    _need(grants, spec, collection, "create")
    record = tenant_db.create_record(row.db_name, spec, collection, _json_body(), row.storage_quota_mb)
    service.record_audit(row, current_user.id, "create", collection, record["id"], None, record)
    return jsonify(record), 201


@bp.route("/apps/<public_id>/_api/<collection>/_aggregate", methods=["GET"])
@_api
def api_aggregate(row, spec, grants, collection):
    _need(grants, spec, collection, "view")
    return jsonify(tenant_db.aggregate(row.db_name, spec, collection, request.args.get("agg", ""), request.args.get("field") or None, _filters()))


@bp.route("/apps/<public_id>/_api/<collection>/<int:record_id>", methods=["GET"])
@_api
def api_get(row, spec, grants, collection, record_id):
    _need(grants, spec, collection, "view")
    return jsonify(tenant_db.get_record(row.db_name, spec, collection, record_id))


@bp.route("/apps/<public_id>/_api/<collection>/<int:record_id>", methods=["PUT"])
@_api
def api_update(row, spec, grants, collection, record_id):
    _need(grants, spec, collection, "update")
    old = tenant_db.get_record(row.db_name, spec, collection, record_id)
    record = tenant_db.update_record(row.db_name, spec, collection, record_id, _json_body(), row.storage_quota_mb)
    if {k: v for k, v in record.items() if k != "updated_at"} != {k: v for k, v in old.items() if k != "updated_at"}:  # lưu không đổi gì thì không có gì để ghi nhật ký
        service.record_audit(row, current_user.id, "update", collection, record_id, old, record)
    return jsonify(record)


@bp.route("/apps/<public_id>/_api/<collection>/<int:record_id>", methods=["DELETE"])
@_api
def api_delete(row, spec, grants, collection, record_id):
    _need(grants, spec, collection, "delete")
    old = tenant_db.get_record(row.db_name, spec, collection, record_id)
    tenant_db.delete_record(row.db_name, spec, collection, record_id)
    service.record_audit(row, current_user.id, "delete", collection, record_id, old, None)
    return jsonify({"deleted": record_id})
