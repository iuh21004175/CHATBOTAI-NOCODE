"""Phân quyền theo vai trò trong team — NGUỒN DUY NHẤT cho "vai trò nào được làm gì" và cho việc xác định team đang làm việc.

Vì sao có `sync_session_team` chạy trước MỌI request: các route/Socket.IO đọc `session["team_id"]` và tin nó để lọc dữ liệu
theo team (multi-tenant). Nếu không kiểm tra lại, thành viên bị xóa khỏi team (hoặc user đổi team) vẫn giữ nguyên quyền truy cập
dữ liệu team cũ cho tới khi phiên hết hạn. Kiểm tra tại đây (1 truy vấn/request) sửa đúng chỗ đó cho mọi route cùng lúc.
"""
from functools import wraps

from flask import abort, g, redirect, request, session, url_for
from flask_login import current_user

from app.credits import service as credits_service
from app.csrf import ensure_csrf_token
from app.models import Bot, Team, TeamMember
from extensions import db

OWNER, ADMIN, MEMBER = "Owner", "Admin", "Member"
ROLES = (OWNER, ADMIN, MEMBER)
ROLE_LABELS = {OWNER: "Chủ nhóm", ADMIN: "Quản trị viên", MEMBER: "Thành viên"}
ROLE_DESCRIPTIONS = {
    OWNER: "Thực hiện mọi thao tác, kể cả đổi tên nhóm và chỉ định chủ nhóm khác.",
    ADMIN: "Tạo trợ lý, xuất bản (domain, giao diện), mời và quản lý thành viên (không đổi được chủ nhóm).",
    MEMBER: "Xem và chỉnh cấu hình, tri thức, lịch sử chat và tin nhắn của các trợ lý; không mời/xóa thành viên, không tạo trợ lý.",
}

# permission -> các vai trò được phép. Thêm quyền mới = thêm đúng 1 dòng ở đây.
PERMISSIONS: dict[str, frozenset[str]] = {
    "manage_team": frozenset({OWNER}),                # đổi tên nhóm
    "manage_members": frozenset({OWNER, ADMIN}),      # mời / đổi vai trò / xóa thành viên (chi tiết theo vai trò đích: can_manage)
    "manage_bots": frozenset({OWNER, ADMIN}),         # tạo trợ lý
    "publish": frozenset({OWNER, ADMIN}),             # Bước 3: domain được nhúng, giao diện, icon
    "manage_billing": frozenset({OWNER, ADMIN}),      # nạp AI Credit (thanh toán payOS)
    "manage_modules": frozenset({OWNER, ADMIN}),      # Website Action Engine: khai báo module, duyệt hành động agent được làm trên website khách
    "manage_apps": frozenset({OWNER, ADMIN}),         # App Builder: tạo / duyệt Spec / sinh lại ứng dụng (xem + dùng app: mọi thành viên)
}

# Route được phép truy cập khi user chưa thuộc team nào (vd bị xóa khỏi team cuối cùng): chỉ để tạo nhóm/nhận lời mời/đăng xuất
_NO_TEAM_ENDPOINTS = frozenset({"team.new_team", "team.accept_invite", "auth.logout", "auth.me", "static", "healthz"})


def can(role: str | None, permission: str) -> bool:
    return role in PERMISSIONS.get(permission, frozenset())


def can_manage(actor_role: str | None, target_role: str | None) -> bool:
    """Actor có được đổi vai trò / xóa thành viên có vai trò target_role không: Chủ nhóm — mọi người; Quản trị viên — chỉ Thành viên
    (không đụng được chủ nhóm hay quản trị viên khác); Thành viên — không ai."""
    if actor_role == OWNER:
        return True
    if actor_role == ADMIN:
        return target_role == MEMBER
    return False


def membership_for(user_id: int, team_id: int | None) -> TeamMember | None:
    if not team_id:
        return None
    return TeamMember.query.filter_by(user_id=user_id, team_id=team_id).first()


def resolve_team_id(user_id: int, preferred_team_id) -> int | None:
    """Team làm việc hợp lệ của user: `preferred_team_id` nếu user CÒN là thành viên, ngược lại team đầu tiên của user, ngược lại
    None. Dùng cho cả route (qua sync_session_team) lẫn Socket.IO (không có before_request)."""
    if isinstance(preferred_team_id, int) and membership_for(user_id, preferred_team_id):
        return preferred_team_id
    first = TeamMember.query.filter_by(user_id=user_id).order_by(TeamMember.id.asc()).first()
    return first.team_id if first else None


def sync_session_team():
    """before_request: đồng bộ session["team_id"] với tư cách thành viên thật; đặt g.membership. User chưa có team nào bị dẫn tới
    trang tạo nhóm (trừ vài route được phép, xem _NO_TEAM_ENDPOINTS)."""
    g.membership = None
    if not current_user.is_authenticated:
        return None
    team_id = resolve_team_id(current_user.id, session.get("team_id"))
    if team_id != session.get("team_id"):
        session["team_id"] = team_id
    g.membership = membership_for(current_user.id, team_id)
    if team_id is None and request.endpoint and request.endpoint not in _NO_TEAM_ENDPOINTS:
        return redirect(url_for("team.new_team"))
    return None


def current_role() -> str | None:
    membership = getattr(g, "membership", None)
    return membership.role if membership else None


def requires(permission: str):
    """Decorator đặt SAU @login_required: 403 nếu vai trò hiện tại không có quyền."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not can(current_role(), permission):
                abort(403)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def _nav_bot_id(team_id: int | None) -> int | None:
    """Trợ lý mà các mục điều hướng theo trợ lý (Module) trỏ tới: trợ lý đang xem (bot_id trên URL/query) nếu thuộc team đang chọn,
    ngược lại trợ lý đầu tiên của team (cùng thứ tự với Bảng điều khiển). None nếu team chưa có trợ lý -> ẩn mục."""
    if not team_id:
        return None
    bot_ids = [bot_id for (bot_id,) in db.session.query(Bot.id).filter(Bot.team_id == team_id).order_by(Bot.created_at.asc(), Bot.id.asc()).all()]
    if not bot_ids:
        return None
    current = (request.view_args or {}).get("bot_id", request.args.get("bot_id", type=int))
    return current if current in bot_ids else bot_ids[0]


def nav_context() -> dict:
    """Biến cho mọi template: danh sách team của user (bộ chuyển team ở sidebar) và vai trò hiện tại."""
    if not current_user.is_authenticated:
        return {}
    rows = (
        db.session.query(Team, TeamMember.role)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == current_user.id)
        .order_by(Team.name.asc(), Team.id.asc())
        .all()
    )
    role = current_role()
    team_id = session.get("team_id")
    return {
        "nav_bot_id": _nav_bot_id(team_id),
        "nav_balance_vnd": credits_service.display_balance(team_id) if team_id else None,
        "nav_teams": [{"id": team.id, "name": team.name, "role": member_role} for team, member_role in rows],
        "nav_active_team_id": session.get("team_id"),
        "nav_role": role,
        "nav_role_label": ROLE_LABELS.get(role, ""),
        "nav_csrf": ensure_csrf_token(),
        "can_manage_bots": can(role, "manage_bots"),
        "can_publish": can(role, "publish"),
        "can_manage_modules": can(role, "manage_modules"),
        "can_manage_apps": can(role, "manage_apps"),
        "can_manage_members": can(role, "manage_members"),
        "can_manage_billing": can(role, "manage_billing"),
    }


def init_app(app) -> None:
    app.before_request(sync_session_team)
    app.context_processor(nav_context)
