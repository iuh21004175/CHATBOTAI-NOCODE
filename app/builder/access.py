"""Phân quyền trong app (AB1): ai được làm gì trên collection nào. NGUỒN DUY NHẤT — mọi API dữ liệu và trang chạy app hỏi ở đây, ở SERVER.

Người dùng cuối của app = thành viên nhóm sở hữu app (dùng lại hệ thống nhóm, xem app/permissions.py):
- Chủ nhóm / Quản trị viên (quyền `manage_apps`): toàn quyền mọi collection.
- Thành viên (Member): quyền theo VAI TRÒ APP được gán (builder_app_members.role_id -> Spec["roles"]); chưa được gán = không có quyền gì (không thấy app).
Vai trò bị xoá khỏi Spec sau khi gán -> không còn quyền (fail-closed), không rơi về quyền mặc định nào.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app import permissions
from app.builder.spec import ACTIONS
from app.models import BuilderApp, BuilderAppMember, TeamMember, User
from extensions import db


@dataclass(frozen=True)
class Access:
    full: bool = False
    role_id: str | None = None
    role_label: str = ""
    grants: dict = field(default_factory=dict)  # collection -> frozenset(hành động)

    @property
    def has_access(self) -> bool:
        return self.full or self.role_id is not None

    def allows(self, collection: str, action: str) -> bool:
        return self.full or action in self.grants.get(collection, ())

    def as_dict(self, spec: dict) -> dict[str, list[str]]:
        """{collection: [hành động]} cho MỌI collection trong Spec (để giao diện ẩn nút) — chỉ phục vụ hiển thị, server vẫn tự kiểm tra từng lần."""
        return {c["name"]: [a for a in ACTIONS if self.allows(c["name"], a)] for c in spec["collections"]}


NO_ACCESS = Access()
FULL_ACCESS = Access(full=True, role_label="Toàn quyền")


def member_row(app_id: int, user_id: int) -> BuilderAppMember | None:
    return BuilderAppMember.query.filter_by(app_id=app_id, user_id=user_id).first()


def resolve(app_row: BuilderApp, spec: dict, user_id: int, team_role: str | None) -> Access:
    if permissions.can(team_role, "manage_apps"):
        return FULL_ACCESS
    row = member_row(app_row.id, user_id)
    if row is None:
        return NO_ACCESS
    role = next((r for r in spec.get("roles", []) if r["id"] == row.role_id), None)
    if role is None:
        return NO_ACCESS
    return Access(role_id=role["id"], role_label=role["label"], grants={c: frozenset(a) for c, a in role["permissions"].items()})


def can_open(app_row: BuilderApp, user_id: int, team_role: str | None) -> bool:
    """Được mở trang quản trị/chi tiết của app: người quản lý, hoặc thành viên đã được gán vai trò."""
    return permissions.can(team_role, "manage_apps") or member_row(app_row.id, user_id) is not None


def visible_app_ids(team_id: int, user_id: int) -> set[int]:
    rows = (db.session.query(BuilderAppMember.app_id).join(BuilderApp, BuilderApp.id == BuilderAppMember.app_id)
            .filter(BuilderApp.team_id == team_id, BuilderAppMember.user_id == user_id).all())
    return {app_id for (app_id,) in rows}


class AssignmentError(ValueError):
    """Gán vai trò không hợp lệ (message tiếng Việt, hiện cho người dùng)."""


def assign_role(app_row: BuilderApp, spec: dict | None, target_user_id: int, role_id: str, assigned_by: int) -> None:
    """Gán (hoặc đổi) vai trò app cho 1 thành viên Member của team sở hữu app. Không commit — người gọi commit."""
    membership = TeamMember.query.filter_by(team_id=app_row.team_id, user_id=target_user_id).first()
    if membership is None:
        raise AssignmentError("Người này không thuộc nhóm.")
    if permissions.can(membership.role, "manage_apps"):
        raise AssignmentError("Chủ nhóm và Quản trị viên luôn có toàn quyền, không cần gán vai trò.")
    if not spec or role_id not in {r["id"] for r in spec.get("roles", [])}:
        raise AssignmentError("Vai trò không có trong Spec của ứng dụng.")
    row = member_row(app_row.id, target_user_id)
    if row is None:
        db.session.add(BuilderAppMember(app_id=app_row.id, user_id=target_user_id, role_id=role_id, assigned_by=assigned_by))
    else:
        row.role_id, row.assigned_by = role_id, assigned_by


def revoke_role(app_row: BuilderApp, target_user_id: int) -> None:
    row = member_row(app_row.id, target_user_id)
    if row is not None:
        db.session.delete(row)


def member_overview(app_row: BuilderApp) -> list[dict]:
    """Mọi thành viên nhóm kèm vai trò app hiện tại (cho trang gán vai trò)."""
    assigned = {m.user_id: m.role_id for m in BuilderAppMember.query.filter_by(app_id=app_row.id).all()}
    rows = (db.session.query(TeamMember, User).join(User, User.id == TeamMember.user_id).filter(TeamMember.team_id == app_row.team_id)
            .order_by(User.full_name.asc(), User.email.asc()).all())
    return [{"user_id": u.id, "name": u.full_name or u.email, "email": u.email, "team_role": m.role, "full": permissions.can(m.role, "manage_apps"),
             "role_id": assigned.get(u.id)} for m, u in rows]
