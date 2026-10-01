"""Service layer cho blueprint team: tạo/đổi tên nhóm, thành viên, lời mời qua email, chuyển nhóm.

Mọi thao tác lấy team từ TƯ CÁCH THÀNH VIÊN của người thực hiện (`actor`, đã được app/permissions.py xác thực) — không nhận team_id
từ client — nên không thể tác động sang team khác. Quy tắc vai trò (ai đổi/xóa được ai) dùng chung app/permissions.can_manage.
Bất biến: team luôn còn ít nhất 1 Chủ nhóm.
"""
from datetime import datetime, timedelta

from flask import current_app
from flask_mail import Message as MailMessage
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import escape

from app import permissions
from app.credits import service as credits_service
from app.models import Team, TeamInvitation, TeamMember, User
from extensions import db, mail

INVITE_SALT = "team-invite"
INVITE_TTL = timedelta(days=7)
MAX_TEAM_NAME_CHARS = 255
MAX_TEAMS_PER_USER = 10
MAX_PENDING_INVITATIONS = 50
INVITABLE_ROLES = (permissions.ADMIN, permissions.MEMBER)  # Chủ nhóm chỉ được chỉ định bởi Chủ nhóm khác (đổi vai trò), không qua lời mời


def _clean_email(raw: str | None) -> str:
    from app.auth.service import EMAIL_RE  # import trễ: app.auth.service gọi ngược vào module này khi đăng nhập

    email = (raw or "").strip().lower()
    return email if email and len(email) <= 255 and EMAIL_RE.match(email) else ""


# ---- Nhóm của user, tạo nhóm, đổi tên ----

def user_team_count(user_id: int) -> int:
    return TeamMember.query.filter_by(user_id=user_id).count()


def create_team(user: User, name: str) -> tuple[Team | None, str | None]:
    """Tạo nhóm mới, người tạo là Chủ nhóm. Trả (team, None) hoặc (None, lỗi)."""
    name = " ".join((name or "").split())
    if not name:
        return None, "Vui lòng nhập tên nhóm."
    if len(name) > MAX_TEAM_NAME_CHARS:
        return None, f"Tên nhóm tối đa {MAX_TEAM_NAME_CHARS} ký tự."
    if user_team_count(user.id) >= MAX_TEAMS_PER_USER:
        return None, f"Mỗi tài khoản chỉ thuộc tối đa {MAX_TEAMS_PER_USER} nhóm."
    team = Team(name=name, plan="free")
    db.session.add(team)
    db.session.flush()
    credits_service.ensure_account(team.id)  # Credit dùng thử: đúng 1 lần cho mỗi team mới (cùng transaction với việc tạo team)
    db.session.add(TeamMember(team_id=team.id, user_id=user.id, role=permissions.OWNER, joined_at=datetime.utcnow()))
    db.session.commit()
    return team, None


def rename_team(team: Team, name: str) -> str | None:
    name = " ".join((name or "").split())
    if not name:
        return "Vui lòng nhập tên nhóm."
    if len(name) > MAX_TEAM_NAME_CHARS:
        return f"Tên nhóm tối đa {MAX_TEAM_NAME_CHARS} ký tự."
    team.name = name
    db.session.commit()
    return None


def switch_team(user_id: int, team_id: int) -> bool:
    """True nếu user là thành viên của team (được phép chuyển sang)."""
    return permissions.membership_for(user_id, team_id) is not None


# ---- Thành viên ----

def _owner_count(team_id: int) -> int:
    return TeamMember.query.filter_by(team_id=team_id, role=permissions.OWNER).count()


def list_members(actor: TeamMember) -> list[dict]:
    """Thành viên của team của actor, kèm việc actor được làm với từng người (để giao diện chỉ hiện nút hợp lệ)."""
    rows = (
        db.session.query(TeamMember, User)
        .join(User, User.id == TeamMember.user_id)
        .filter(TeamMember.team_id == actor.team_id)
        .order_by(TeamMember.id.asc())
        .all()
    )
    last_owner = _owner_count(actor.team_id) <= 1
    result = []
    for member, user in rows:
        is_self = member.user_id == actor.user_id
        manageable = permissions.can_manage(actor.role, member.role) and not (member.role == permissions.OWNER and last_owner)
        result.append({
            "member": member,
            "user": user,
            "is_self": is_self,
            "can_change_role": manageable and not (is_self and actor.role != permissions.OWNER),
            "can_remove": manageable and not is_self,
            "can_leave": is_self and not (member.role == permissions.OWNER and last_owner),
            "assignable_roles": assignable_roles(actor.role),
        })
    return result


def assignable_roles(actor_role: str | None) -> tuple[str, ...]:
    """Vai trò actor được gán khi đổi vai trò: Chủ nhóm — cả 3; Quản trị viên — Quản trị viên/Thành viên; còn lại — không."""
    if actor_role == permissions.OWNER:
        return permissions.ROLES
    if actor_role == permissions.ADMIN:
        return (permissions.ADMIN, permissions.MEMBER)
    return ()


def _target(actor: TeamMember, member_id: int) -> TeamMember | None:
    return TeamMember.query.filter_by(id=member_id, team_id=actor.team_id).first()


def change_role(actor: TeamMember, member_id: int, new_role: str) -> str | None:
    """Đổi vai trò 1 thành viên của team. Trả thông báo lỗi hoặc None."""
    target = _target(actor, member_id)
    if target is None:
        return "Không tìm thấy thành viên."
    if new_role not in assignable_roles(actor.role):
        return "Bạn không có quyền gán vai trò này."
    if not permissions.can_manage(actor.role, target.role):
        return "Bạn không có quyền đổi vai trò của người này."
    if target.role == new_role:
        return None
    if target.role == permissions.OWNER and _owner_count(actor.team_id) <= 1:
        return "Nhóm phải còn ít nhất một chủ nhóm — hãy chỉ định chủ nhóm khác trước."
    target.role = new_role
    db.session.commit()
    return None


def remove_member(actor: TeamMember, member_id: int) -> str | None:
    """Xóa người khác khỏi nhóm (Chủ nhóm: mọi người; Quản trị viên: chỉ Thành viên). Muốn tự rời nhóm dùng leave_team."""
    target = _target(actor, member_id)
    if target is None:
        return "Không tìm thấy thành viên."
    if target.user_id == actor.user_id:
        return "Dùng \"Rời nhóm\" để tự rời khỏi nhóm."
    if not permissions.can_manage(actor.role, target.role):
        return "Bạn không có quyền xóa người này."
    if target.role == permissions.OWNER and _owner_count(actor.team_id) <= 1:
        return "Nhóm phải còn ít nhất một chủ nhóm."
    db.session.delete(target)
    db.session.commit()
    return None


def leave_team(actor: TeamMember) -> str | None:
    """Tự rời nhóm. Chủ nhóm duy nhất không rời được (phải chỉ định chủ nhóm khác trước)."""
    if actor.role == permissions.OWNER and _owner_count(actor.team_id) <= 1:
        return "Bạn là chủ nhóm duy nhất — hãy chỉ định chủ nhóm khác trước khi rời nhóm."
    db.session.delete(actor)
    db.session.commit()
    return None


# ---- Lời mời ----

def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def invitation_token(invitation: TeamInvitation) -> str:
    return _serializer().dumps({"i": invitation.id, "e": invitation.email}, salt=INVITE_SALT)


def list_invitations(team_id: int) -> list[TeamInvitation]:
    """Lời mời đang chờ (chưa nhận, chưa hết hạn)."""
    return (
        TeamInvitation.query.filter(
            TeamInvitation.team_id == team_id, TeamInvitation.accepted_at.is_(None), TeamInvitation.expires_at > datetime.utcnow()
        )
        .order_by(TeamInvitation.id.asc())
        .all()
    )


def invite(actor: TeamMember, email: str, role: str) -> tuple[TeamInvitation | None, str | None]:
    """Tạo (hoặc làm mới) lời mời. Trả (invitation, None) hoặc (None, lỗi). Gọi invitation_token() để lấy link."""
    email = _clean_email(email)
    if not email:
        return None, "Email không hợp lệ."
    if role not in INVITABLE_ROLES:
        return None, "Vai trò không hợp lệ."
    already = (
        db.session.query(TeamMember.id)
        .join(User, User.id == TeamMember.user_id)
        .filter(TeamMember.team_id == actor.team_id, User.email == email)
        .first()
    )
    if already:
        return None, "Người này đã là thành viên của nhóm."
    invitation = TeamInvitation.query.filter_by(team_id=actor.team_id, email=email, accepted_at=None).first()
    if invitation is None:
        if len(list_invitations(actor.team_id)) >= MAX_PENDING_INVITATIONS:
            return None, f"Tối đa {MAX_PENDING_INVITATIONS} lời mời đang chờ."
        invitation = TeamInvitation(team_id=actor.team_id, email=email)
        db.session.add(invitation)
    invitation.role = role
    invitation.invited_by_user_id = actor.user_id
    invitation.expires_at = datetime.utcnow() + INVITE_TTL
    db.session.commit()
    return invitation, None


def cancel_invitation(actor: TeamMember, invitation_id: int) -> bool:
    invitation = TeamInvitation.query.filter_by(id=invitation_id, team_id=actor.team_id, accepted_at=None).first()
    if invitation is None:
        return False
    db.session.delete(invitation)
    db.session.commit()
    return True


def read_invitation(token: str) -> tuple[TeamInvitation | None, str | None]:
    """Token -> lời mời còn hiệu lực, hoặc (None, lỗi). Không cần đăng nhập (chỉ đọc)."""
    try:
        data = _serializer().loads(token or "", salt=INVITE_SALT, max_age=int(INVITE_TTL.total_seconds()))
    except SignatureExpired:
        return None, "Link mời đã hết hạn — hãy nhờ quản trị viên gửi lại lời mời."
    except BadSignature:
        return None, "Link mời không hợp lệ."
    invitation = db.session.get(TeamInvitation, data.get("i")) if isinstance(data, dict) else None
    if invitation is None or invitation.email != data.get("e"):
        return None, "Lời mời không còn hiệu lực (có thể đã bị hủy)."
    if invitation.accepted_at is not None:
        return None, "Lời mời này đã được sử dụng."
    if invitation.expires_at <= datetime.utcnow():
        return None, "Link mời đã hết hạn — hãy nhờ quản trị viên gửi lại lời mời."
    return invitation, None


def accept_invitation(user: User, token: str) -> tuple[Team | None, str | None]:
    """Người ĐANG ĐĂNG NHẬP bằng đúng email được mời nhận lời mời. Trả (team, None) hoặc (None, lỗi)."""
    invitation, error = read_invitation(token)
    if error:
        return None, error
    if (user.email or "").strip().lower() != invitation.email:
        return None, f"Lời mời này dành cho {invitation.email}. Hãy đăng xuất và đăng nhập bằng email đó."
    if permissions.membership_for(user.id, invitation.team_id) is None:
        if user_team_count(user.id) >= MAX_TEAMS_PER_USER:
            return None, f"Mỗi tài khoản chỉ thuộc tối đa {MAX_TEAMS_PER_USER} nhóm."
        db.session.add(TeamMember(team_id=invitation.team_id, user_id=user.id, role=invitation.role, joined_at=datetime.utcnow()))
    invitation.accepted_at = datetime.utcnow()
    db.session.commit()
    return db.session.get(Team, invitation.team_id), None


def send_invitation_email(email: str, team_name: str, inviter_name: str, role: str, url: str) -> None:
    role_label = permissions.ROLE_LABELS.get(role, role)
    subject = f"{inviter_name} mời bạn tham gia nhóm \"{team_name}\" trên Chatbot AI"
    mail.send(MailMessage(
        subject=subject,
        recipients=[email],
        body=(
            f"{inviter_name} mời bạn tham gia nhóm \"{team_name}\" với vai trò {role_label}.\n\n"
            f"Bấm vào link sau và đăng nhập bằng email {email} để tham gia (hiệu lực 7 ngày):\n\n{url}\n\n"
            "Nếu bạn không biết lời mời này, vui lòng bỏ qua email."
        ),
        html=f"""
        <p><b>{_html(inviter_name)}</b> mời bạn tham gia nhóm <b>{_html(team_name)}</b> với vai trò {_html(role_label)}.</p>
        <p><a href="{_html(url)}" style="display:inline-block;padding:12px 20px;background:#1D4ED8;color:#fff;
           border-radius:8px;text-decoration:none;font-weight:600;">Tham gia nhóm</a></p>
        <p>Đăng nhập bằng email <b>{_html(email)}</b> (link có hiệu lực 7 ngày). Hoặc copy link: {_html(url)}</p>
        <p style="color:#5B6B82;font-size:13px;">Nếu bạn không biết lời mời này, vui lòng bỏ qua email.</p>
        """,
    ))


def _html(value: str) -> str:
    return str(escape(value))
