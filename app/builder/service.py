"""Nghiệp vụ App Builder (AB0): tạo app -> LLM sinh Spec (nền) -> người dùng duyệt -> cấp database riêng + dựng schema + LLM sinh code từng màn hình (nền).

Việc nền chạy bằng socketio.start_background_task như các tính năng khác (xem app/widget/service.py) và ghi trạng thái vào builder_apps.status để giao diện
hỏi lại (polling). Ranh giới tiến trình nền là nơi DUY NHẤT bắt Exception rộng: ghi log đầy đủ + lưu lý do vào error_message để người dùng thấy — không nuốt lỗi.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from app import permissions
from app.builder import access, codegen, spec as spec_mod, tenant_db
from app.models import BuilderApp, BuilderAppAudit, BuilderAppVersion
from config import Config
from core.context_engine import execution_cost as xc
from core.context_engine.cost import LLMUsageTracker
from extensions import db, socketio

logger = logging.getLogger("builder.service")

BUSY_STATUSES = ("spec_generating", "generating")


class BuilderError(ValueError):
    """Lỗi nghiệp vụ hiện được cho người dùng (message tiếng Việt)."""


def get_app_for_team(app_id: int, team_id: int) -> BuilderApp | None:
    return BuilderApp.query.filter_by(id=app_id, team_id=team_id).first()


def get_app_by_public_id(public_id: str, team_id: int) -> BuilderApp | None:
    return BuilderApp.query.filter_by(public_id=public_id, team_id=team_id).first()


def list_apps(team_id: int, user_id: int, team_role: str | None) -> list[BuilderApp]:
    """Người quản lý (Chủ nhóm/Quản trị viên) thấy mọi app của team; thành viên chỉ thấy app đã được gán vai trò."""
    query = BuilderApp.query.filter_by(team_id=team_id)
    if not permissions.can(team_role, "manage_apps"):
        ids = access.visible_app_ids(team_id, user_id)
        if not ids:
            return []
        query = query.filter(BuilderApp.id.in_(ids))
    return query.order_by(BuilderApp.created_at.desc(), BuilderApp.id.desc()).all()


def record_audit(app_row: BuilderApp, user_id: int, action: str, collection: str, record_id: int, old: dict | None, new: dict | None, source: str = "ui") -> None:
    """Ghi nhật ký 1 thao tác ghi dữ liệu app. Thao tác đã xảy ra ở database RIÊNG của app (khác database chính) nên không thể chung 1 transaction: nếu ghi nhật ký
    lỗi thì ghi log mức ERROR kèm đủ nội dung để khôi phục bằng tay — KHÔNG làm hỏng/đảo ngược thao tác của người dùng và không giả vờ như đã ghi."""
    try:
        db.session.add(BuilderAppAudit(app_id=app_row.id, user_id=user_id, action=action, collection=collection, record_id=record_id, old_values=old, new_values=new, source=source))
        db.session.commit()
    except Exception:  # noqa: BLE001 — nhật ký không được phá thao tác đã hoàn tất; lỗi đã ghi log đầy đủ bên dưới
        db.session.rollback()
        logger.exception("MẤT NHẬT KÝ app=%s user=%s %s %s#%s old=%s new=%s", app_row.id, user_id, action, collection, record_id, old, new)


def recent_audit(app_id: int, limit: int = 50) -> list[BuilderAppAudit]:
    return BuilderAppAudit.query.filter_by(app_id=app_id).order_by(BuilderAppAudit.id.desc()).limit(limit).all()


def set_member_role(app_id: int, team_id: int, actor_id: int, target_user_id: int, role_id: str) -> None:
    """role_id rỗng = thu hồi vai trò (thành viên không còn thấy app)."""
    row = get_app_for_team(app_id, team_id)
    if row is None:
        raise BuilderError("Không tìm thấy ứng dụng.")
    try:
        if role_id:
            access.assign_role(row, row.spec, target_user_id, role_id, actor_id)
        else:
            access.revoke_role(row, target_user_id)
    except access.AssignmentError as exc:
        db.session.rollback()
        raise BuilderError(str(exc)) from exc
    db.session.commit()


def effective_status(app: BuilderApp) -> tuple[str, str | None]:
    """(status, lỗi): 'đang sinh' quá BUILDER_STALE_SECONDS không cập nhật = tiến trình nền đã chết (server khởi động lại giữa chừng) -> coi là failed để người
    dùng thử lại được thay vì kẹt vĩnh viễn."""
    if app.status in BUSY_STATUSES and app.updated_at and datetime.utcnow() - app.updated_at > timedelta(seconds=Config.BUILDER_STALE_SECONDS):
        return "failed", "Tiến trình sinh ứng dụng bị gián đoạn (quá thời gian không phản hồi). Hãy thử lại."
    return app.status, app.error_message


def current_version(app: BuilderApp) -> BuilderAppVersion | None:
    return db.session.get(BuilderAppVersion, app.current_version_id) if app.current_version_id else None


def create_app(team_id: int, user_id: int, prompt: str) -> BuilderApp:
    prompt = (prompt or "").strip()
    if len(prompt) < 10:
        raise BuilderError("Hãy mô tả ứng dụng cần tạo (ít nhất 10 ký tự).")
    if len(prompt) > Config.BUILDER_PROMPT_MAX_CHARS:
        raise BuilderError(f"Mô tả tối đa {Config.BUILDER_PROMPT_MAX_CHARS} ký tự.")
    if BuilderApp.query.filter_by(team_id=team_id).count() >= Config.BUILDER_MAX_APPS_PER_TEAM:
        raise BuilderError(f"Mỗi nhóm tạo tối đa {Config.BUILDER_MAX_APPS_PER_TEAM} ứng dụng.")
    row = BuilderApp(team_id=team_id, created_by=user_id, prompt=prompt, name=prompt[:60], status="spec_generating", storage_quota_mb=Config.TENANT_DB_QUOTA_MB)
    db.session.add(row)
    db.session.commit()
    _start(_spec_task, row.id)
    return row


def regenerate_spec(app_id: int, team_id: int) -> None:
    """Sinh lại Spec từ mô tả gốc (khi Spec chưa ổn hoặc lần sinh trước lỗi). Chỉ khi app CHƯA có database/code — Spec đã dựng schema thì sửa Spec là việc của AB2."""
    row = _locked(app_id, team_id)
    status, _ = effective_status(row)
    if row.db_name:
        db.session.rollback()
        raise BuilderError("Ứng dụng đã dựng cơ sở dữ liệu nên chưa thể sinh lại Spec từ đầu.")
    if status not in ("spec_ready", "failed"):
        db.session.rollback()
        raise BuilderError("Ứng dụng đang được xử lý.")
    row.status, row.error_message, row.spec = "spec_generating", None, None
    db.session.commit()
    _start(_spec_task, row.id)


def approve(app_id: int, team_id: int) -> None:
    """Người dùng duyệt Spec: cấp database riêng + dựng schema (đồng bộ, vài giây) rồi sinh code (nền). Chạy lại được (idempotent) để sinh lại code / thử lại khi lỗi."""
    row = _locked(app_id, team_id)
    status, _ = effective_status(row)
    if row.spec is None or status in BUSY_STATUSES:
        db.session.rollback()
        raise BuilderError("Ứng dụng chưa có Spec hoặc đang được xử lý." if row.spec is None else "Ứng dụng đang được xử lý.")
    spec = row.spec
    if not row.db_name:
        row.db_name = tenant_db.new_db_name()
    # Chuyển sang "generating" cùng lúc lưu tên database và nhả khoá: yêu cầu duyệt thứ 2 (bấm đúp/2 tab) thấy trạng thái bận và bị từ chối, nên không
    # có 2 lượt cấp phát/sinh code song song; tên database đã lưu nên lần thử lại dùng đúng database đó, không để lại database mồ côi.
    row.status, row.error_message = "generating", None
    db.session.commit()
    try:
        tenant_db.provision(row.db_name)
        changes = tenant_db.apply_spec(row.db_name, spec)
    except tenant_db.TenantDbError as exc:
        logger.error("Cấp phát database cho app %s lỗi: %s", app_id, exc)
        row.status, row.error_message = "failed", str(exc)
        db.session.commit()
        raise BuilderError(str(exc)) from exc
    logger.info("app %s: database %s: %s", app_id, row.db_name, ", ".join(changes) or "schema đã đủ")
    _start(_codegen_task, row.id)


def retry(app_id: int, team_id: int) -> None:
    row = get_app_for_team(app_id, team_id)
    if row is None:
        raise BuilderError("Không tìm thấy ứng dụng.")
    if row.spec is None:
        regenerate_spec(app_id, team_id)
    else:
        approve(app_id, team_id)


def storage_usage_bytes(app: BuilderApp) -> int | None:
    """Dung lượng database app đang dùng; None nếu chưa có database hoặc không đo được (log lỗi — không làm hỏng trang quản trị)."""
    if not app.db_name:
        return None
    try:
        return tenant_db.usage_bytes(app.db_name)
    except Exception:  # noqa: BLE001 — trang quản trị vẫn phải mở được khi MySQL của app lỗi; lỗi đã ghi log
        logger.exception("Không đo được dung lượng database %s", app.db_name)
        return None


# --------------------------------------------------------------------------------------------------------------------- nội bộ
def _locked(app_id: int, team_id: int) -> BuilderApp:
    row = BuilderApp.query.filter_by(id=app_id, team_id=team_id).with_for_update().first()
    if row is None:
        raise BuilderError("Không tìm thấy ứng dụng.")
    return row


def _start(task, app_id: int) -> None:
    from flask import current_app

    socketio.start_background_task(task, current_app._get_current_object(), app_id)


def _fail(row: BuilderApp, message: str) -> None:
    row.status, row.error_message = "failed", message[:2000]
    db.session.commit()


def _spec_task(flask_app, app_id: int) -> None:
    with flask_app.app_context():
        row = db.session.get(BuilderApp, app_id)
        try:
            tracker = LLMUsageTracker()
            spec = codegen.generate_spec(row.prompt, codegen.plain_call(0.3, Config.BUILDER_SPEC_MAX_TOKENS), tracker)
            row.spec, row.name, row.status, row.error_message = spec, spec["name"][:255], "spec_ready", None
            db.session.commit()
            logger.info("app %s: Spec xong, chi phí LLM ~%s VND", app_id, xc.llm_cost(tracker.calls, datetime.utcnow()).cost_vnd)
        except codegen.CodegenError as exc:
            _fail(row, str(exc))
        except Exception as exc:  # noqa: BLE001 — ranh giới tiến trình nền: ghi log + báo người dùng, không để app kẹt ở "đang sinh"
            logger.exception("Sinh Spec cho app %s lỗi", app_id)
            db.session.rollback()
            _fail(db.session.get(BuilderApp, app_id), f"Lỗi hệ thống khi sinh Spec ({type(exc).__name__}). Hãy thử lại.")


def fallback_js(screen: dict) -> str:
    """Mã mặc định do NỀN TẢNG viết (không phải LLM) khi code AI sinh không qua được quét tĩnh sau mọi vòng sửa: không bao giờ xuất bản code bị từ chối."""
    if screen["type"] == "table":
        return "$(function () {\n  UI.crudTable($('#screen'), {collection: %s});\n});\n" % json.dumps(screen["collection"])
    return "$(function () {\n  UI.stats($('#screen'), %s);\n});\n" % json.dumps(screen.get("stats", []), ensure_ascii=False)


def _codegen_task(flask_app, app_id: int) -> None:
    with flask_app.app_context():
        row = db.session.get(BuilderApp, app_id)
        try:
            spec = spec_mod.validate(row.spec)  # kiểm lại: Spec trong DB là đầu vào đáng tin nhất nhưng sinh code chỉ dùng bản đã chuẩn hoá
            tracker = LLMUsageTracker()
            call = codegen.plain_call(0.2, Config.BUILDER_SCREEN_MAX_TOKENS)
            code, warnings = {}, []
            for screen in spec["screens"]:
                result = codegen.generate_screen(spec, screen, call, tracker)
                if result.problems:
                    code[screen["id"]] = fallback_js(screen)
                    warnings.append(f'Màn "{screen["title"]}": code AI sinh không đạt kiểm tra an toàn nên dùng mẫu mặc định. ' + "; ".join(result.problems[:3]))
                else:
                    code[screen["id"]] = result.code
                row.updated_at = datetime.utcnow()  # còn đang chạy: giữ khỏi bị coi là "gián đoạn"
                db.session.commit()
            cost = xc.llm_cost(tracker.calls, datetime.utcnow())
            number = (db.session.query(db.func.max(BuilderAppVersion.number)).filter_by(app_id=app_id).scalar() or 0) + 1
            version = BuilderAppVersion(
                app_id=app_id, number=number, spec=spec, files=codegen.build_files(spec, row.public_id, code), llm_calls=tracker.calls,
                llm_cost_vnd=cost.cost_vnd, cost_reported=cost.reported, scan_warnings=warnings,
            )
            db.session.add(version)
            db.session.flush()
            row.current_version_id, row.status, row.error_message = version.id, "ready", None
            db.session.commit()
            logger.info("app %s: sinh code xong (phiên bản %s), %d lệnh gọi LLM, chi phí %s VND", app_id, number, len(tracker.calls), cost.cost_vnd)
        except Exception as exc:  # noqa: BLE001 — ranh giới tiến trình nền (xem docstring module)
            logger.exception("Sinh code cho app %s lỗi", app_id)
            db.session.rollback()
            _fail(db.session.get(BuilderApp, app_id), f"Lỗi khi sinh code ({type(exc).__name__}). Hãy thử lại.")
