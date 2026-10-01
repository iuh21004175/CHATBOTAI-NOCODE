"""Route layer cho blueprint team: trang Cài đặt nhóm (đổi tên, thành viên, lời mời), tạo nhóm, chuyển nhóm, nhận lời mời.
Không chứa logic nghiệp vụ — logic ở service.py; quyền theo vai trò ở app/permissions.py."""
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app import permissions
from app.csrf import ensure_csrf_token, verify_csrf_token
from app.models import Team
from extensions import db, limiter

from . import service

bp = Blueprint("team", __name__, url_prefix="/team")


def _csrf_ok() -> bool:
    if verify_csrf_token(request.form.get("csrf_token", "")):
        return True
    flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    return False


def _back():
    return redirect(url_for("team.index"))


@bp.route("", methods=["GET"])
@login_required
def index():
    actor = g.membership
    if actor is None:
        return redirect(url_for("team.new_team"))
    manage = permissions.can(actor.role, "manage_members")
    return render_template(
        "team/index.html",
        team=db.session.get(Team, actor.team_id),
        role=actor.role,
        members=service.list_members(actor),
        invitations=service.list_invitations(actor.team_id) if manage else [],
        can_rename=permissions.can(actor.role, "manage_team"),
        can_invite=manage,
        invitable_roles=service.INVITABLE_ROLES,
        role_labels=permissions.ROLE_LABELS,
        role_descriptions=permissions.ROLE_DESCRIPTIONS,
        csrf_token=ensure_csrf_token(),
        active_nav="team",
        page_title="Cài đặt nhóm",
    )


@bp.route("/rename", methods=["POST"])
@login_required
@permissions.requires("manage_team")
def rename():
    if _csrf_ok():
        error = service.rename_team(db.session.get(Team, g.membership.team_id), request.form.get("name", ""))
        flash(error or "Đã lưu tên nhóm.", "error" if error else "success")
    return _back()


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new_team():
    if request.method == "POST":
        if _csrf_ok():
            team, error = service.create_team(current_user, request.form.get("name", ""))
            if error:
                flash(error, "error")
            else:
                session["team_id"] = team.id
                flash(f'Đã tạo nhóm "{team.name}".', "success")
                return redirect(url_for("dashboard.index"))
        return redirect(url_for("team.new_team"))
    return render_template("team/new.html", csrf_token=ensure_csrf_token(), active_nav="team", page_title="Tạo nhóm")


@bp.route("/switch", methods=["POST"])
@login_required
def switch():
    """Chuyển nhóm đang làm việc (chỉ sang nhóm user là thành viên)."""
    team_id = request.form.get("team_id", type=int)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    elif team_id is None or not service.switch_team(current_user.id, team_id):
        abort(403)
    else:
        session["team_id"] = team_id
    return redirect(url_for("dashboard.index"))


# ---- Thành viên ----

@bp.route("/members/<int:member_id>/role", methods=["POST"])
@login_required
@permissions.requires("manage_members")
def change_role(member_id):
    if _csrf_ok():
        error = service.change_role(g.membership, member_id, request.form.get("role", ""))
        flash(error or "Đã cập nhật vai trò.", "error" if error else "success")
    return _back()


@bp.route("/members/<int:member_id>/remove", methods=["POST"])
@login_required
@permissions.requires("manage_members")
def remove_member(member_id):
    if _csrf_ok():
        error = service.remove_member(g.membership, member_id)
        flash(error or "Đã xóa thành viên khỏi nhóm.", "error" if error else "success")
    return _back()


@bp.route("/leave", methods=["POST"])
@login_required
def leave():
    """Tự rời nhóm đang làm việc (mọi vai trò)."""
    if not _csrf_ok():
        return _back()
    actor = g.membership
    if actor is None:
        abort(404)
    error = service.leave_team(actor)
    if error:
        flash(error, "error")
        return _back()
    flash("Bạn đã rời nhóm.", "success")
    return redirect(url_for("dashboard.index"))  # before_request chọn nhóm khác (hoặc dẫn tới trang tạo nhóm)


# ---- Lời mời ----

@bp.route("/invitations", methods=["POST"])
@login_required
@permissions.requires("manage_members")
@limiter.limit("30 per hour", methods=["POST"])
def invite():
    if not _csrf_ok():
        return _back()
    actor = g.membership
    invitation, error = service.invite(actor, request.form.get("email", ""), request.form.get("role", ""))
    if error:
        flash(error, "error")
        return _back()
    link = url_for("team.accept_invite", token=service.invitation_token(invitation), _external=True)
    team = db.session.get(Team, actor.team_id)
    try:
        service.send_invitation_email(invitation.email, team.name, current_user.full_name or current_user.email, invitation.role, link)
        flash(f"Đã gửi lời mời tới {invitation.email}. Nếu họ chưa nhận được email, gửi họ link này: {link}", "success")
    except Exception:
        current_app.logger.exception("Không gửi được email mời vào nhóm")
        flash(f"Đã tạo lời mời nhưng chưa gửi được email. Hãy gửi cho {invitation.email} link này: {link}", "info")
    return _back()


@bp.route("/invitations/<int:invitation_id>/cancel", methods=["POST"])
@login_required
@permissions.requires("manage_members")
def cancel_invitation(invitation_id):
    if _csrf_ok():
        ok = service.cancel_invitation(g.membership, invitation_id)
        flash("Đã hủy lời mời." if ok else "Không tìm thấy lời mời.", "success" if ok else "error")
    return _back()


@bp.route("/invite/<token>", methods=["GET"])
@limiter.limit("30 per minute")
def accept_invite(token):
    """Link trong email mời. Chưa đăng nhập: nhớ token trong session rồi dẫn tới đăng nhập — sau MỌI kiểu đăng nhập
    (mật khẩu/Google/Facebook/magic link/đăng ký) auth.service.login tự nhận lời mời. Đã đăng nhập: nhận ngay."""
    if not current_user.is_authenticated:
        invitation, error = service.read_invitation(token)
        if error:
            flash(error, "error")
        else:
            session["pending_invite"] = token
            flash(f"Đăng nhập hoặc đăng ký bằng email {invitation.email} để tham gia nhóm.", "info")
        return redirect(url_for("auth.login"))
    team, error = service.accept_invitation(current_user, token)
    if error:
        flash(error, "error")
        return redirect(url_for("dashboard.index"))
    session["team_id"] = team.id
    flash(f'Bạn đã tham gia nhóm "{team.name}".', "success")
    return redirect(url_for("dashboard.index"))
