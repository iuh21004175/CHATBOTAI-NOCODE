"""Phase B — quản lý nhóm (team): phân quyền theo vai trò, thành viên, lời mời qua email, chuyển giữa nhiều nhóm, tạo nhóm.
Phần thuần không cần DB; còn lại đi qua route thật (có đăng nhập + CSRF) và cần DB *_test (xem tests/README.md)."""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from werkzeug.security import generate_password_hash

from app import permissions
from app.models import Bot, BotDomain, Team, TeamInvitation, TeamMember, User
from app.team import service as team_service
from tests.db_case import DbCase


class RoleRules(unittest.TestCase):
    def test_permission_matrix(self):
        expected = {
            "manage_team": {"Owner"},
            "manage_members": {"Owner", "Admin"},
            "manage_bots": {"Owner", "Admin"},
            "publish": {"Owner", "Admin"},
        }
        for permission, roles in expected.items():
            for role in permissions.ROLES:
                self.assertEqual(permissions.can(role, permission), role in roles, (permission, role))

    def test_unknown_role_permission_or_none_is_denied(self):
        self.assertFalse(permissions.can(None, "manage_bots"))
        self.assertFalse(permissions.can("Hacker", "manage_bots"))
        self.assertFalse(permissions.can("Owner", "khong_ton_tai"))

    def test_can_manage_who(self):
        self.assertTrue(all(permissions.can_manage("Owner", target) for target in permissions.ROLES))
        self.assertTrue(permissions.can_manage("Admin", "Member"))
        self.assertFalse(permissions.can_manage("Admin", "Admin"))
        self.assertFalse(permissions.can_manage("Admin", "Owner"))
        self.assertFalse(any(permissions.can_manage("Member", target) for target in permissions.ROLES))
        self.assertFalse(permissions.can_manage(None, "Member"))

    def test_assignable_roles(self):
        self.assertEqual(team_service.assignable_roles("Owner"), ("Owner", "Admin", "Member"))
        self.assertEqual(team_service.assignable_roles("Admin"), ("Admin", "Member"))
        self.assertEqual(team_service.assignable_roles("Member"), ())
        self.assertEqual(team_service.assignable_roles(None), ())

    def test_owner_is_never_invitable(self):
        self.assertNotIn("Owner", team_service.INVITABLE_ROLES)


class TeamCase(DbCase):
    """self.team = "Team A" (Owner mặc định, self.team.user); tiện ích tạo người dùng/thành viên/client đã đăng nhập."""

    PASSWORD = "matkhau-123"

    def setUp(self):
        super().setUp()
        # Bộ giới hạn tần suất dùng chung (Redis) giữa các test/lần chạy -> tắt để test không phụ thuộc thứ tự/lần chạy trước
        from extensions import limiter

        enabled, limiter.enabled = limiter.enabled, False
        self.addCleanup(setattr, limiter, "enabled", enabled)

    def make_user(self, email, name=None):
        user = User(email=email, password_hash=generate_password_hash(self.PASSWORD), full_name=name or email.split("@")[0])
        self.db.session.add(user)
        self.db.session.commit()
        return user

    def join(self, team, user, role="Member"):
        member = TeamMember(team_id=team.id, user_id=user.id, role=role, joined_at=datetime.utcnow())
        self.db.session.add(member)
        self.db.session.commit()
        return member

    def member_of(self, team, email, role="Member"):
        """Tư cách thành viên (TeamMember) của 1 user mới."""
        return self.join(team, self.make_user(email), role)

    def user_in(self, team, email, role="Member"):
        """User mới đã là thành viên của team với vai trò `role`."""
        return self.member_of(team, email, role).user

    def client_for(self, user, team=None):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["team_id"] = (team or self.team).id
            session["csrf_token"] = "tok"
        return client

    def owner_client(self):
        return self.client_for(self.team.user)

    def post(self, client, path, follow=False, **data):
        data.setdefault("csrf_token", "tok")
        return client.post(path, data=data, follow_redirects=follow)

    def roles(self, team=None):
        self.db.session.expire_all()
        return {m.user.email: m.role for m in TeamMember.query.filter_by(team_id=(team or self.team).id)}

    def text(self, response):
        return response.get_data(as_text=True)


class PermissionsOnExistingRoutes(TeamCase):
    def setUp(self):
        super().setUp()
        self.admin = self.user_in(self.team, "admin@example.com", "Admin")
        self.member = self.user_in(self.team, "member@example.com", "Member")

    def test_only_owner_and_admin_can_create_bots(self):
        for user, allowed in ((self.team.user, True), (self.admin, True), (self.member, False)):
            client = self.client_for(user)
            with self.subTest(user=user.email):
                self.assertEqual(client.get("/bots/new").status_code, 200 if allowed else 403)
                response = self.post(client, "/bots/new", name=f"Bot của {user.full_name}")
                self.assertEqual(response.status_code, 302 if allowed else 403)
        self.assertEqual(sorted(b.name for b in Bot.query.filter_by(team_id=self.team.id) if b.name != "Bot A"), ["Bot của Team A", "Bot của admin"])

    def test_only_owner_and_admin_can_change_publish_settings(self):
        base = f"/bots/{self.bot.id}/publish"
        for user, allowed in ((self.team.user, True), (self.admin, True), (self.member, False)):
            client = self.client_for(user)
            with self.subTest(user=user.email):
                add = self.post(client, base + "/domains", widget_domain=f"{user.full_name.lower().replace(' ', '')}.vn")
                self.assertEqual(add.status_code, 302 if allowed else 403)
                self.assertEqual(self.post(client, base + "/appearance", widget_color="#112233").status_code, 302 if allowed else 403)
                self.assertEqual(self.post(client, base + "/icon").status_code, 302 if allowed else 403)
        domains = {d.domain for d in BotDomain.query.filter_by(bot_id=self.bot.id)}
        self.assertEqual(domains, {"teama.vn", "admin.vn"})

    def test_member_cannot_delete_a_domain(self):
        self.db.session.add(BotDomain(bot_id=self.bot.id, domain="giu.vn"))
        self.db.session.commit()
        row = BotDomain.query.one()
        response = self.post(self.client_for(self.member), f"/bots/{self.bot.id}/publish/domains/{row.id}/delete")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(BotDomain.query.count(), 1)

    def test_member_keeps_day_to_day_access_to_bots(self):
        client = self.client_for(self.member)
        for path in ("/dashboard", f"/bots/{self.bot.id}/setup", f"/bots/{self.bot.id}/knowledge", f"/bots/{self.bot.id}/history",
                     f"/bots/{self.bot.id}/publish", "/customers", "/inbox"):
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)
        response = self.post(client, f"/bots/{self.bot.id}/setup", name="Tên mới", low_confidence_reply_mode="ask_clarify")
        self.assertEqual(response.status_code, 302)
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(Bot, self.bot.id).name, "Tên mới")

    def test_ui_hides_what_the_member_cannot_do(self):
        member_html = self.text(self.client_for(self.member).get("/dashboard"))
        owner_html = self.text(self.owner_client().get("/dashboard"))
        self.assertNotIn('href="/bots/new"', member_html)
        self.assertIn('href="/bots/new"', owner_html)
        publish = self.text(self.client_for(self.member).get(f"/bots/{self.bot.id}/publish"))
        self.assertNotIn("Thêm domain", publish)
        self.assertNotIn(">Lưu giao diện</button>", publish)
        self.assertIn("Thêm domain", self.text(self.owner_client().get(f"/bots/{self.bot.id}/publish")))

    def test_member_sees_no_create_button_on_empty_dashboard(self):
        empty_team = self.make_team("Nhóm trống")
        member = self.user_in(empty_team, "mem-trong@example.com", "Member")
        self.assertNotIn("Tạo chatbot đầu tiên", self.text(self.client_for(member, empty_team).get("/dashboard")))
        self.assertIn("Tạo chatbot đầu tiên", self.text(self.client_for(empty_team.user, empty_team).get("/dashboard")))


class SessionRevalidation(TeamCase):
    """session["team_id"] không được tin mù quáng: quyền phải theo tư cách thành viên HIỆN TẠI."""

    def test_removed_member_loses_access_on_the_very_next_request(self):
        member = self.member_of(self.team, "gone@example.com")
        client = self.client_for(member.user)
        self.assertEqual(client.get(f"/bots/{self.bot.id}/setup").status_code, 200)
        self.db.session.delete(member)
        self.db.session.commit()
        for path in (f"/bots/{self.bot.id}/setup", f"/bots/{self.bot.id}/history", f"/bots/{self.bot.id}/knowledge"):
            self.assertIn(client.get(path).status_code, (302, 404), path)
        self.assertNotEqual(client.get(f"/bots/{self.bot.id}/setup").status_code, 200)

    def test_removed_member_with_no_team_is_sent_to_create_one(self):
        member = self.member_of(self.team, "gone@example.com")
        client = self.client_for(member.user)
        self.db.session.delete(member)
        self.db.session.commit()
        response = client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/team/new"))
        self.assertEqual(client.get("/team/new").status_code, 200)
        self.assertEqual(client.get("/customers").status_code, 302)

    def test_removed_member_falls_back_to_their_other_team(self):
        other_team = self.make_team("Team B")
        user = self.make_user("two@example.com")
        member = self.join(self.team, user)
        self.join(other_team, user, "Owner")
        client = self.client_for(user)  # đang làm việc ở Team A
        self.db.session.delete(member)
        self.db.session.commit()
        self.assertEqual(client.get(f"/bots/{self.bot.id}/setup").status_code, 404, "không còn quyền với bot Team A")
        with client.session_transaction() as session:
            self.assertEqual(session["team_id"], other_team.id)

    def test_forged_team_id_in_session_is_corrected(self):
        other_team = self.make_team("Team B")
        other_bot = self.service.create_bot(other_team.id, "Bot B")
        client = self.owner_client()
        with client.session_transaction() as session:
            session["team_id"] = other_team.id  # không phải thành viên
        self.assertEqual(client.get(f"/bots/{other_bot.id}/setup").status_code, 404)
        with client.session_transaction() as session:
            self.assertEqual(session["team_id"], self.team.id)

    def test_role_change_takes_effect_immediately(self):
        admin = self.user_in(self.team, "admin@example.com", "Admin")
        client = self.client_for(admin)
        self.assertEqual(client.get("/bots/new").status_code, 200)
        TeamMember.query.filter_by(user_id=admin.id).one().role = "Member"
        self.db.session.commit()
        self.assertEqual(client.get("/bots/new").status_code, 403)

    def test_socket_team_resolution_revalidates_membership(self):
        member = self.member_of(self.team, "sock@example.com")
        self.assertEqual(permissions.resolve_team_id(member.user.id, self.team.id), self.team.id)
        self.db.session.delete(member)
        self.db.session.commit()
        self.assertIsNone(permissions.resolve_team_id(member.user.id, self.team.id))
        self.assertIsNone(permissions.resolve_team_id(member.user.id, "không phải số"))
        self.assertIsNone(permissions.resolve_team_id(member.user.id, None))

    def test_auth_me_reports_the_active_teams_role(self):
        other_team = self.make_team("Team B")
        user = self.make_user("two@example.com")
        self.join(self.team, user, "Member")
        self.join(other_team, user, "Admin")
        client = self.client_for(user, other_team)
        data = client.get("/auth/me").get_json()
        self.assertEqual((data["team_id"], data["role"]), (other_team.id, "Admin"))


class SwitchTeams(TeamCase):
    def setUp(self):
        super().setUp()
        self.team_b = self.make_team("Team B")
        self.bot_b = self.service.create_bot(self.team_b.id, "Bot B")
        self.user = self.make_user("two@example.com")
        self.join(self.team, self.user, "Member")
        self.join(self.team_b, self.user, "Admin")
        self.client = self.client_for(self.user)

    def active(self):
        with self.client.session_transaction() as session:
            return session["team_id"]

    def test_switching_changes_data_role_and_permissions(self):
        self.assertEqual(self.client.get(f"/bots/{self.bot_b.id}/setup").status_code, 404)
        self.assertEqual(self.client.get("/bots/new").status_code, 403, "Member ở Team A")
        self.assertEqual(self.post(self.client, "/team/switch", team_id=self.team_b.id).status_code, 302)
        self.assertEqual(self.active(), self.team_b.id)
        self.assertEqual(self.client.get(f"/bots/{self.bot_b.id}/setup").status_code, 200)
        self.assertEqual(self.client.get(f"/bots/{self.bot.id}/setup").status_code, 404, "dữ liệu Team A không lẫn sang")
        self.assertEqual(self.client.get("/bots/new").status_code, 200, "Admin ở Team B")

    def test_dashboard_shows_the_active_teams_bots_only(self):
        self.post(self.client, "/team/switch", team_id=self.team_b.id)
        html = self.text(self.client.get("/dashboard"))
        self.assertIn("Bot B", html)
        self.assertNotIn("Bot A", html)

    def test_cannot_switch_to_a_team_the_user_is_not_in(self):
        team_c = self.make_team("Team C")
        response = self.post(self.client, "/team/switch", team_id=team_c.id)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.active(), self.team.id)
        self.assertEqual(self.post(self.client, "/team/switch", team_id=999999).status_code, 403)
        self.assertEqual(self.post(self.client, "/team/switch", team_id="abc").status_code, 403)

    def test_switch_needs_csrf(self):
        self.post(self.client, "/team/switch", csrf_token="sai", team_id=self.team_b.id)
        self.assertEqual(self.active(), self.team.id)

    def test_switch_needs_login(self):
        response = self.post(self.app.test_client(), "/team/switch", team_id=self.team_b.id)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_switcher_lists_every_team_with_role_and_escapes_names(self):
        self.db.session.get(Team, self.team_b.id).name = "<script>alert(1)</script>"
        self.db.session.commit()
        html = self.text(self.client.get("/dashboard"))
        self.assertIn("Team A", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)", html)
        self.assertIn("Quản trị viên", html)


class CreateTeam(TeamCase):
    def test_creator_becomes_owner_and_active_team(self):
        client = self.owner_client()
        response = self.post(client, "/team/new", name="  Nhóm   mới  ")
        self.assertEqual(response.status_code, 302)
        team = Team.query.filter_by(name="Nhóm mới").one()
        member = TeamMember.query.filter_by(team_id=team.id).one()
        self.assertEqual((member.user_id, member.role), (self.team.user.id, "Owner"))
        self.assertIsNotNone(member.joined_at)
        with client.session_transaction() as session:
            self.assertEqual(session["team_id"], team.id)
        self.assertEqual(Bot.query.filter_by(team_id=team.id).count(), 0, "nhóm mới trống, không thấy bot nhóm cũ")
        self.assertEqual(client.get(f"/bots/{self.bot.id}/setup").status_code, 404)

    def test_validation_and_csrf(self):
        client = self.owner_client()
        before = Team.query.count()
        for name in ("", "   ", "x" * 256):
            self.post(client, "/team/new", name=name)
        self.post(client, "/team/new", csrf_token="sai", name="Hợp lệ")
        self.assertEqual(Team.query.count(), before)

    def test_team_count_per_user_is_capped(self):
        client = self.owner_client()
        for i in range(team_service.MAX_TEAMS_PER_USER + 2):
            self.post(client, "/team/new", name=f"Nhóm {i}")
        self.db.session.expire_all()
        self.assertEqual(TeamMember.query.filter_by(user_id=self.team.user.id).count(), team_service.MAX_TEAMS_PER_USER)

    def test_requires_login_and_page_renders(self):
        self.assertEqual(self.owner_client().get("/team/new").status_code, 200)
        response = self.app.test_client().get("/team/new")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class RenameTeam(TeamCase):
    def test_only_owner_can_rename(self):
        admin = self.user_in(self.team, "admin@example.com", "Admin")
        member = self.user_in(self.team, "member@example.com")
        for user in (admin, member):
            self.assertEqual(self.post(self.client_for(user), "/team/rename", name="Bị hack").status_code, 403)
        self.assertEqual(self.post(self.owner_client(), "/team/rename", name="  Tên   mới ").status_code, 302)
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(Team, self.team.id).name, "Tên mới")

    def test_invalid_names_and_csrf_are_rejected(self):
        client = self.owner_client()
        for name in ("", "  ", "x" * 256):
            self.post(client, "/team/rename", name=name)
        self.post(client, "/team/rename", csrf_token="sai", name="Không lưu")
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(Team, self.team.id).name, "Team A")

    def test_rename_only_touches_the_active_team(self):
        team_b = self.make_team("Team B")
        self.post(self.owner_client(), "/team/rename", name="Đổi A")
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(Team, team_b.id).name, "Team B")


class ManageMembers(TeamCase):
    def setUp(self):
        super().setUp()
        self.owner = self.team.user
        self.admin = self.user_in(self.team, "admin@example.com", "Admin")
        self.admin2 = self.user_in(self.team, "admin2@example.com", "Admin")
        self.member = self.user_in(self.team, "member@example.com", "Member")
        self.member2 = self.user_in(self.team, "member2@example.com", "Member")

    def row(self, user):
        return TeamMember.query.filter_by(team_id=self.team.id, user_id=user.id).one()

    def set_role(self, actor, target, role, **kw):
        return self.post(self.client_for(actor), f"/team/members/{self.row(target).id}/role", role=role, **kw)

    def remove(self, actor, target, **kw):
        return self.post(self.client_for(actor), f"/team/members/{self.row(target).id}/remove", **kw)

    # -- đổi vai trò --
    def test_owner_can_set_any_role_on_others(self):
        self.set_role(self.owner, self.member, "Admin")
        self.set_role(self.owner, self.admin, "Member")
        self.set_role(self.owner, self.member2, "Owner")
        self.assertEqual(self.roles()["member@example.com"], "Admin")
        self.assertEqual(self.roles()["admin@example.com"], "Member")
        self.assertEqual(self.roles()["member2@example.com"], "Owner")

    def test_admin_can_only_manage_members(self):
        self.set_role(self.admin, self.member, "Admin")
        self.assertEqual(self.roles()["member@example.com"], "Admin")
        for target in (self.admin2, self.owner):
            self.set_role(self.admin, target, "Member")
        roles = self.roles()
        self.assertEqual((roles["admin2@example.com"], roles["teama@example.com"]), ("Admin", "Owner"))

    def test_admin_cannot_grant_owner(self):
        self.set_role(self.admin, self.member, "Owner")
        self.assertEqual(self.roles()["member@example.com"], "Member")

    def test_admin_cannot_promote_themself_or_touch_the_owner(self):
        self.set_role(self.admin, self.admin, "Owner")
        self.set_role(self.admin, self.owner, "Member")
        roles = self.roles()
        self.assertEqual((roles["admin@example.com"], roles["teama@example.com"]), ("Admin", "Owner"))

    def test_member_cannot_change_roles_at_all(self):
        self.assertEqual(self.set_role(self.member, self.member2, "Admin").status_code, 403)
        self.assertEqual(self.set_role(self.member, self.member, "Owner").status_code, 403)
        self.assertEqual(self.roles()["member@example.com"], "Member")

    def test_invalid_role_values_are_rejected(self):
        for value in ("", "Root", "owner", "Owner; DROP", "admin"):
            self.set_role(self.owner, self.member, value)
        self.assertEqual(self.roles()["member@example.com"], "Member")

    def test_last_owner_cannot_be_demoted_but_can_once_another_owner_exists(self):
        self.set_role(self.owner, self.owner, "Member")
        self.assertEqual(self.roles()["teama@example.com"], "Owner")
        self.set_role(self.owner, self.admin, "Owner")
        self.set_role(self.owner, self.owner, "Member")
        self.assertEqual(self.roles()["teama@example.com"], "Member")
        self.assertEqual(list(self.roles().values()).count("Owner"), 1)

    def test_owner_count_never_reaches_zero_by_any_role_change(self):
        self.set_role(self.owner, self.admin, "Owner")
        self.set_role(self.owner, self.owner, "Admin")  # còn admin làm Owner
        self.set_role(self.admin, self.owner, "Member")  # Owner (admin) đổi Owner cũ
        self.set_role(self.admin, self.admin, "Member")  # người Owner cuối tự hạ -> bị chặn
        self.assertEqual(list(self.roles().values()).count("Owner"), 1)

    def test_target_from_another_team_is_not_found(self):
        other_team = self.make_team("Team B")
        foreign = TeamMember.query.filter_by(team_id=other_team.id).one()
        self.post(self.owner_client(), f"/team/members/{foreign.id}/role", role="Member")
        self.post(self.owner_client(), f"/team/members/{foreign.id}/remove")
        self.assertEqual(self.roles(other_team), {"teamb@example.com": "Owner"})

    def test_csrf_required_for_role_and_remove(self):
        self.set_role(self.owner, self.member, "Admin", csrf_token="sai")
        self.remove(self.owner, self.member, csrf_token="sai")
        self.assertEqual(self.roles()["member@example.com"], "Member")

    # -- xóa thành viên --
    def test_owner_can_remove_anyone_else(self):
        for target in (self.member, self.admin):
            self.remove(self.owner, target)
        self.assertEqual(set(self.roles()), {"teama@example.com", "admin2@example.com", "member2@example.com"})

    def test_admin_can_remove_members_only(self):
        self.remove(self.admin, self.member)
        self.remove(self.admin, self.admin2)
        self.remove(self.admin, self.owner)
        self.assertNotIn("member@example.com", self.roles())
        self.assertIn("admin2@example.com", self.roles())
        self.assertIn("teama@example.com", self.roles())

    def test_member_cannot_remove_anyone(self):
        self.assertEqual(self.remove(self.member, self.member2).status_code, 403)
        self.assertIn("member2@example.com", self.roles())

    def test_owner_cannot_remove_themself_via_remove(self):
        self.remove(self.owner, self.owner)
        self.assertIn("teama@example.com", self.roles())

    def test_removed_user_data_of_the_team_is_untouched(self):
        self.remove(self.owner, self.member)
        self.assertEqual(Bot.query.filter_by(team_id=self.team.id).count(), 1)
        self.assertIsNotNone(User.query.filter_by(email="member@example.com").first(), "tài khoản không bị xóa, chỉ rời nhóm")

    # -- tự rời nhóm --
    def test_member_and_admin_can_leave(self):
        for user, email in ((self.member, "member@example.com"), (self.admin, "admin@example.com")):
            response = self.post(self.client_for(user), "/team/leave")
            self.assertEqual(response.status_code, 302)
            self.assertNotIn(email, self.roles())

    def test_sole_owner_cannot_leave_but_can_after_transfer(self):
        self.post(self.owner_client(), "/team/leave")
        self.assertIn("teama@example.com", self.roles())
        self.set_role(self.owner, self.admin, "Owner")
        self.post(self.owner_client(), "/team/leave")
        self.assertNotIn("teama@example.com", self.roles())
        self.assertEqual(list(self.roles().values()).count("Owner"), 1)

    def test_leave_needs_csrf_and_login(self):
        self.post(self.client_for(self.member), "/team/leave", csrf_token="sai")
        self.assertIn("member@example.com", self.roles())
        self.assertIn("/login", self.post(self.app.test_client(), "/team/leave").headers["Location"])

    # -- trang --
    def test_page_shows_only_the_actions_the_viewer_may_use(self):
        owner_html = self.text(self.owner_client().get("/team"))
        self.assertIn("member@example.com", owner_html)
        self.assertIn("Gửi lời mời", owner_html)
        self.assertIn(f"/team/members/{self.row(self.member).id}/remove", owner_html)
        self.assertNotIn(f"/team/members/{self.row(self.owner).id}/remove", owner_html, "không có nút xóa chính mình / chủ nhóm duy nhất")

        admin_html = self.text(self.client_for(self.admin).get("/team"))
        self.assertIn(f"/team/members/{self.row(self.member).id}/remove", admin_html)
        self.assertNotIn(f"/team/members/{self.row(self.admin2).id}/remove", admin_html)
        self.assertNotIn(f"/team/members/{self.row(self.owner).id}/role", admin_html)
        self.assertNotIn('action="/team/rename"', admin_html)

        member_html = self.text(self.client_for(self.member).get("/team"))
        self.assertNotIn("Gửi lời mời", member_html)
        self.assertNotIn("/remove", member_html)
        self.assertIn("/team/leave", member_html)

    def test_joined_date_is_shown_or_dash_for_legacy_members(self):
        self.row(self.member).joined_at = None
        self.db.session.commit()
        html = self.text(self.owner_client().get("/team"))
        self.assertIn(datetime.utcnow().strftime("%d/%m/%Y"), html)
        self.assertIn("—", html)

    def test_names_are_escaped(self):
        self.member.full_name = "<img src=x onerror=alert(1)>"
        self.db.session.commit()
        html = self.text(self.owner_client().get("/team"))
        self.assertNotIn("<img src=x", html)
        self.assertIn("&lt;img src=x", html)


class Invitations(TeamCase):
    def setUp(self):
        super().setUp()
        self.admin = self.user_in(self.team, "admin@example.com", "Admin")
        self.member = self.user_in(self.team, "member@example.com", "Member")
        patcher = mock.patch("app.team.service.send_invitation_email")
        self.send = patcher.start()
        self.addCleanup(patcher.stop)

    def invite(self, user, email, role="Member", **kw):
        return self.post(self.client_for(user), "/team/invitations", email=email, role=role, **kw)

    def test_owner_and_admin_can_invite_and_email_is_sent(self):
        for inviter, email, role in ((self.team.user, "new1@example.com", "Member"), (self.admin, "new2@example.com", "Admin")):
            self.assertEqual(self.invite(inviter, email, role).status_code, 302)
        invitations = {i.email: i.role for i in TeamInvitation.query.all()}
        self.assertEqual(invitations, {"new1@example.com": "Member", "new2@example.com": "Admin"})
        self.assertEqual(self.send.call_count, 2)
        email, team_name, inviter_name, role, url = self.send.call_args_list[1].args
        self.assertEqual((email, team_name, role), ("new2@example.com", "Team A", "Admin"))
        self.assertIn("/team/invite/", url)

    def test_member_cannot_invite(self):
        self.assertEqual(self.invite(self.member, "x@example.com").status_code, 403)
        self.assertEqual(TeamInvitation.query.count(), 0)
        self.send.assert_not_called()

    def test_invitation_rules(self):
        cases = {
            "email hỏng": ("khong-phai-email", "Member"),
            "email trống": ("", "Member"),
            "vai trò Owner": ("a@example.com", "Owner"),
            "vai trò lạ": ("a@example.com", "Root"),
            "đã là thành viên": ("member@example.com", "Member"),
            "đã là thành viên (khác hoa thường)": ("MEMBER@Example.com", "Member"),
        }
        for label, (email, role) in cases.items():
            with self.subTest(label):
                self.invite(self.team.user, email, role)
        self.assertEqual(TeamInvitation.query.count(), 0)
        self.send.assert_not_called()

    def test_email_is_normalised(self):
        self.invite(self.team.user, "  Nguoi.Moi@Example.COM ", "Member")
        self.assertEqual(TeamInvitation.query.one().email, "nguoi.moi@example.com")

    def test_reinviting_refreshes_the_same_invitation(self):
        self.invite(self.team.user, "new@example.com", "Member")
        first = TeamInvitation.query.one()
        first.expires_at = datetime.utcnow() + timedelta(hours=1)
        self.db.session.commit()
        self.invite(self.team.user, "NEW@example.com", "Admin")
        self.db.session.expire_all()
        invitation = TeamInvitation.query.one()
        self.assertEqual((invitation.id, invitation.role), (first.id, "Admin"))
        self.assertGreater(invitation.expires_at, datetime.utcnow() + timedelta(days=6))
        self.assertEqual(self.send.call_count, 2)

    def test_pending_invitations_are_capped(self):
        for i in range(team_service.MAX_PENDING_INVITATIONS + 3):
            self.invite(self.team.user, f"p{i}@example.com")
        self.assertEqual(TeamInvitation.query.count(), team_service.MAX_PENDING_INVITATIONS)

    def test_csrf_and_login_required(self):
        self.invite(self.team.user, "a@example.com", csrf_token="sai")
        self.assertEqual(TeamInvitation.query.count(), 0)
        response = self.post(self.app.test_client(), "/team/invitations", email="a@example.com", role="Member")
        self.assertIn("/login", response.headers["Location"])

    def test_mail_failure_keeps_the_invitation_and_shows_the_link(self):
        self.send.side_effect = RuntimeError("SMTP down")
        response = self.post(self.owner_client(), "/team/invitations", follow=True, email="a@example.com", role="Member")
        self.assertEqual(TeamInvitation.query.count(), 1)
        self.assertIn("/team/invite/", self.text(response))
        self.assertIn("chưa gửi được email", self.text(response))

    def test_cancel_invitation(self):
        self.invite(self.team.user, "a@example.com")
        invitation = TeamInvitation.query.one()
        token = team_service.invitation_token(invitation)
        self.assertEqual(self.post(self.client_for(self.member), f"/team/invitations/{invitation.id}/cancel").status_code, 403)
        self.assertEqual(TeamInvitation.query.count(), 1)
        self.post(self.client_for(self.admin), f"/team/invitations/{invitation.id}/cancel")
        self.assertEqual(TeamInvitation.query.count(), 0)
        invitation_read, error = team_service.read_invitation(token)
        self.assertIsNone(invitation_read)
        self.assertIn("không còn hiệu lực", error)

    def test_cancel_cannot_reach_another_teams_invitation(self):
        other_team = self.make_team("Team B")
        other = TeamInvitation(team_id=other_team.id, email="b@example.com", role="Member", expires_at=datetime.utcnow() + timedelta(days=1))
        self.db.session.add(other)
        self.db.session.commit()
        self.post(self.owner_client(), f"/team/invitations/{other.id}/cancel")
        self.assertEqual(TeamInvitation.query.count(), 1)

    def test_pending_list_is_shown_to_managers_and_hides_expired_or_accepted(self):
        now = datetime.utcnow()
        for email, expires, accepted in (("live@example.com", now + timedelta(days=1), None), ("old@example.com", now - timedelta(days=1), None),
                                         ("done@example.com", now + timedelta(days=1), now)):
            self.db.session.add(TeamInvitation(team_id=self.team.id, email=email, role="Member", expires_at=expires, accepted_at=accepted))
        self.db.session.commit()
        html = self.text(self.owner_client().get("/team"))
        self.assertIn("live@example.com", html)
        self.assertNotIn("old@example.com", html)
        self.assertNotIn("done@example.com", html)
        self.assertNotIn("live@example.com", self.text(self.client_for(self.member).get("/team")))


class AcceptInvitation(TeamCase):
    def setUp(self):
        super().setUp()
        self.invitee = self.make_user("moi@example.com", "Người Mời")
        self.invitation = TeamInvitation(
            team_id=self.team.id, email="moi@example.com", role="Admin", expires_at=datetime.utcnow() + timedelta(days=7),
            invited_by_user_id=self.team.user.id,
        )
        self.db.session.add(self.invitation)
        self.db.session.commit()
        self.token = team_service.invitation_token(self.invitation)
        self.url = f"/team/invite/{self.token}"

    def logged_in(self, user):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["csrf_token"] = "tok"
        return client

    def membership(self, user=None):
        self.db.session.expire_all()
        return TeamMember.query.filter_by(team_id=self.team.id, user_id=(user or self.invitee).id).first()

    def test_logged_in_invitee_joins_with_the_invited_role_and_switches_team(self):
        own_team = self.make_team("Team riêng")  # người được mời đã có nhóm khác
        self.join(own_team, self.invitee, "Owner")
        client = self.client_for(self.invitee, own_team)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 302)
        member = self.membership()
        self.assertEqual(member.role, "Admin")
        self.assertIsNotNone(member.joined_at)
        with client.session_transaction() as session:
            self.assertEqual(session["team_id"], self.team.id)
        self.assertEqual(client.get(f"/bots/{self.bot.id}/setup").status_code, 200)
        self.assertEqual(client.get("/bots/new").status_code, 200, "vai trò Admin có hiệu lực")

    def test_anonymous_visit_then_password_login_completes_the_invitation(self):
        anonymous = self.app.test_client()
        response = anonymous.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])
        self.assertIsNone(self.membership())
        with anonymous.session_transaction() as session:
            self.assertEqual(session["pending_invite"], self.token)
            session["csrf_token"] = "tok"
        login = anonymous.post("/auth/login", data={"email": "moi@example.com", "password": self.PASSWORD, "csrf_token": "tok"})
        self.assertEqual(login.status_code, 302)
        self.assertEqual(self.membership().role, "Admin")
        with anonymous.session_transaction() as session:
            self.assertEqual(session["team_id"], self.team.id)
            self.assertNotIn("pending_invite", session)
        self.assertEqual(TeamInvitation.query.get(self.invitation.id).accepted_at is not None, True)

    def test_every_login_path_consumes_the_pending_invitation(self):
        from app.auth import service as auth_service

        with self.app.test_request_context("/"):
            from flask import session

            session["pending_invite"] = self.token
            auth_service.login(self.invitee)  # magic link / Google / Facebook / đăng ký đều gọi login()
            self.assertEqual(session["team_id"], self.team.id)
        self.assertEqual(self.membership().role, "Admin")

    def test_wrong_account_cannot_use_the_link(self):
        other = self.make_user("khac@example.com")
        self.join(self.team, other, "Member")  # đã là thành viên nhưng KHÔNG phải người được mời
        outsider_team = self.make_team("Team C")
        outsider = outsider_team.user
        client = self.client_for(outsider, outsider_team)
        response = client.get(self.url, follow_redirects=True)
        self.assertIn("dành cho moi@example.com", self.text(response))
        self.assertIsNone(self.membership(outsider))
        self.db.session.expire_all()
        self.assertIsNone(TeamInvitation.query.get(self.invitation.id).accepted_at)
        self.assertNotIn(outsider.email, self.roles())

    def test_wrong_account_at_login_time_does_not_join_and_reports_it(self):
        anonymous = self.app.test_client()
        anonymous.get(self.url)
        other = self.make_user("khac@example.com")
        with anonymous.session_transaction() as session:
            session["csrf_token"] = "tok"
        anonymous.post("/auth/login", data={"email": "khac@example.com", "password": self.PASSWORD, "csrf_token": "tok"})
        self.assertIsNone(self.membership(other))
        self.assertIsNone(self.membership())

    def test_link_is_single_use(self):
        client = self.client_for(self.invitee, self.make_team("Riêng"))
        client.get(self.url)
        self.assertEqual(TeamMember.query.filter_by(team_id=self.team.id, user_id=self.invitee.id).count(), 1)
        self.db.session.delete(self.membership())
        self.db.session.commit()
        response = client.get(self.url, follow_redirects=True)
        self.assertIn("đã được sử dụng", self.text(response))
        self.assertIsNone(self.membership(), "bị xóa khỏi nhóm thì không dùng lại link cũ để vào lại")

    def test_expired_invitation(self):
        self.invitation.expires_at = datetime.utcnow() - timedelta(minutes=1)
        self.db.session.commit()
        client = self.client_for(self.invitee, self.make_team("Riêng"))
        self.assertIn("hết hạn", self.text(client.get(self.url, follow_redirects=True)))
        self.assertIsNone(self.membership())

    def test_token_older_than_the_ttl_is_rejected_even_if_row_is_fresh(self):
        with mock.patch("app.team.service.INVITE_TTL", timedelta(seconds=-1)):
            invitation, error = team_service.read_invitation(self.token)
        self.assertIsNone(invitation)
        self.assertIn("hết hạn", error)

    def test_tampered_and_garbage_tokens(self):
        for token in (self.token[:-3] + "abc", "rác", "", "a." * 20, self.token + "x"):
            with self.subTest(token=token[:12]):
                invitation, error = team_service.read_invitation(token)
                self.assertIsNone(invitation)
                self.assertTrue(error)
        client = self.client_for(self.invitee, self.make_team("Riêng"))
        self.assertEqual(client.get("/team/invite/rac").status_code, 302)
        self.assertIsNone(self.membership())

    def test_token_bound_to_the_email_and_not_reusable_for_another_invitation(self):
        self.invitation.email = "doi-email@example.com"  # email lời mời bị đổi sau khi phát token
        self.db.session.commit()
        invitation, error = team_service.read_invitation(self.token)
        self.assertIsNone(invitation)
        self.assertTrue(error)

    def test_cancelled_invitation_cannot_be_used(self):
        self.db.session.delete(self.invitation)
        self.db.session.commit()
        client = self.client_for(self.invitee, self.make_team("Riêng"))
        self.assertIn("không còn hiệu lực", self.text(client.get(self.url, follow_redirects=True)))
        self.assertIsNone(self.membership())

    def test_existing_member_does_not_get_a_duplicate_row_or_role_change(self):
        self.join(self.team, self.invitee, "Member")
        client = self.client_for(self.invitee)
        client.get(self.url)
        self.assertEqual(TeamMember.query.filter_by(team_id=self.team.id, user_id=self.invitee.id).count(), 1)
        self.assertEqual(self.membership().role, "Member", "lời mời không tự nâng quyền người đã là thành viên")

    def test_invalid_link_for_anonymous_visitor_does_not_store_anything(self):
        anonymous = self.app.test_client()
        anonymous.get("/team/invite/khong-hop-le")
        with anonymous.session_transaction() as session:
            self.assertNotIn("pending_invite", session)

    def test_invited_user_can_register_with_that_email_and_still_join(self):
        from app.auth import service as auth_service

        brand_new = "chua-co-tk@example.com"
        self.invitation.email = brand_new
        self.db.session.commit()
        token = team_service.invitation_token(self.invitation)
        user = auth_service.register("Người Mới", brand_new, "matkhau-123")
        with self.app.test_request_context("/"):
            from flask import session

            session["pending_invite"] = token
            auth_service.login(user)
            self.assertEqual(session["team_id"], self.team.id)
        self.assertEqual(TeamMember.query.filter_by(team_id=self.team.id, user_id=user.id).one().role, "Admin")
        self.assertEqual(TeamMember.query.filter_by(user_id=user.id).count(), 2, "vẫn có nhóm riêng tạo lúc đăng ký")


class EmailContent(TeamCase):
    def test_email_names_the_role_link_and_escapes_html(self):
        with mock.patch("app.team.service.mail") as mail:
            team_service.send_invitation_email(
                "nguoi@example.com", "<b>Nhóm</b>", "<script>x</script>", "Admin", "https://app.example/team/invite/tok?a=1&b=2"
            )
        (message,), _ = mail.send.call_args
        self.assertEqual(message.recipients, ["nguoi@example.com"])
        self.assertIn("Quản trị viên", message.body)
        self.assertIn("https://app.example/team/invite/tok?a=1&b=2", message.body)
        self.assertNotIn("<script>", message.html)
        self.assertNotIn("<b>Nhóm</b>", message.html)
        self.assertIn("&lt;script&gt;", message.html)
        self.assertIn("a=1&amp;b=2", message.html)


if __name__ == "__main__":
    unittest.main()
