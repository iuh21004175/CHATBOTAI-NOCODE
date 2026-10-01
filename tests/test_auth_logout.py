"""Đăng xuất (Cowork báo 2026-09-25: "không có cách đăng xuất"). Hai nguyên nhân gốc, mỗi cái có test riêng:
  (b) UI chưa từng có nút Đăng xuất  -> form POST + token CSRF trong sidebar của mọi trang có shell;
  (a) POST /auth/logout với CSRF sai/thiếu bị BỎ QUA IM LẶNG rồi redirect /auth/login, nơi người đã đăng nhập bị đẩy ngược về /dashboard nên
      trông như "đăng xuất không làm gì" -> nay báo lỗi rõ và ở lại; lượt đăng xuất thứ 2 không còn bị đẩy tới /auth/login?next=/auth/logout.
Cùng gốc "phiên chưa bị hủy thật": kết nối Socket.IO (Inbox realtime) của phiên vừa đăng xuất phải bị đóng — test ở cuối.
Cần DB *_test (xem tests/README.md)."""
import re

from app.auth import service as auth_service
from app.dashboard import events
from extensions import limiter, socketio
from tests.db_case import DbCase

PASSWORD = "Mat-khau-Rat-Manh-1"


def token_in(html: str) -> str:
    """Token CSRF nằm trong form đăng xuất của trang, đúng thứ trình duyệt sẽ gửi khi bấm nút."""
    match = re.search(r'<form[^>]*action="/auth/logout"[^>]*>\s*<input type="hidden" name="csrf_token" value="([^"]+)"', html)
    assert match, "trang không có form đăng xuất"
    return match.group(1)


class LogoutCase(DbCase):
    def setUp(self):
        super().setUp()
        enabled, limiter.enabled = limiter.enabled, False  # giới hạn tốc độ dùng Redis thật, không liên quan tới nội dung test
        self.addCleanup(setattr, limiter, "enabled", enabled)
        self.user = auth_service.register("Người Dùng", "nguoidung@example.com", PASSWORD, "Team Đăng Xuất")
        self.db.session.commit()

    def browser(self):
        """Trình duyệt mới đăng nhập bằng form thật (đặt cả khóa socket của phiên)."""
        client = self.app.test_client()
        page = client.get("/auth/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = client.post("/auth/login", data={"email": "nguoidung@example.com", "password": PASSWORD, "csrf_token": csrf})
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "/dashboard"))
        return client

    def logged_in(self, client, path="/dashboard") -> bool:
        return client.get(path).status_code == 200

    def press_logout(self, client, page="/dashboard", **extra):
        html = client.get(page).get_data(as_text=True)
        return client.post("/auth/logout", data={"csrf_token": token_in(html)}, **extra)


class Case1OriginalBug(LogoutCase):
    def test_every_page_with_the_shell_has_a_logout_button_with_a_csrf_token(self):
        client = self.browser()
        for path in ("/dashboard", "/team", "/bots/new", "/customers", "/inbox"):
            html = client.get(path).get_data(as_text=True)
            self.assertIn("Đăng xuất", html, path)
            self.assertTrue(token_in(html), path)

    def test_the_button_really_ends_the_session(self):
        client = self.browser()
        self.assertTrue(self.logged_in(client))
        response = self.press_logout(client)
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "/auth/login"))
        for path in ("/dashboard", "/team", "/bots/new", "/customers", "/auth/me"):
            blocked = client.get(path)
            self.assertEqual(blocked.status_code, 302, path)
            self.assertIn("/auth/login", blocked.headers["Location"], path)

    def test_login_page_confirms_it_and_the_session_holds_nothing_of_the_user(self):
        client = self.browser()
        self.press_logout(client)
        page = client.get("/auth/login")
        self.assertEqual(page.status_code, 200, "trang đăng nhập hiển thị (không bị đẩy ngược về dashboard)")
        self.assertIn("Đã đăng xuất.", page.get_data(as_text=True))
        with client.session_transaction() as session:
            for key in ("_user_id", "team_id", "socket_key"):
                self.assertNotIn(key, session, key)

    def test_the_old_token_cannot_be_replayed_after_logout(self):
        client = self.browser()
        html = client.get("/dashboard").get_data(as_text=True)
        old = token_in(html)
        client.post("/auth/logout", data={"csrf_token": old})
        fresh = re.search(r'name="csrf_token" value="([^"]+)"', client.get("/auth/login").get_data(as_text=True)).group(1)
        self.assertNotEqual(fresh, old, "token của phiên cũ đã bị xoay, không dùng lại được")


class Case2SimilarCases(LogoutCase):
    def test_get_is_still_405_logout_is_post_only(self):
        client = self.browser()
        self.assertEqual(client.get("/auth/logout").status_code, 405)
        self.assertTrue(self.logged_in(client), "GET không được đăng xuất (chống CSRF qua thẻ <img>)")

    def test_missing_csrf_is_reported_and_keeps_the_session(self):
        client = self.browser()
        response = client.post("/auth/logout")
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "/dashboard"), "không đi qua /auth/login (nơi bị đẩy ngược)")
        page = client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Phiên làm việc đã hết hạn", page)
        self.assertTrue(self.logged_in(client))

    def test_wrong_and_foreign_csrf_tokens_are_rejected_too(self):
        other = self.browser()
        foreign = token_in(other.get("/dashboard").get_data(as_text=True))
        client = self.browser()
        for token in ("", "sai-token", foreign, "a" * 500):
            response = client.post("/auth/logout", data={"csrf_token": token})
            self.assertEqual(response.headers["Location"], "/dashboard", token[:10])
        self.assertTrue(self.logged_in(client))
        self.assertTrue(self.logged_in(other))

    def test_a_cross_site_form_without_the_token_cannot_log_the_user_out(self):
        client = self.browser()
        client.post("/auth/logout", data={}, headers={"Origin": "https://ke-xau.example", "Referer": "https://ke-xau.example/"})
        self.assertTrue(self.logged_in(client))

    def test_all_tabs_of_one_browser_share_the_ended_session(self):
        client = self.browser()  # các tab cùng trình duyệt dùng chung cookie -> chung 1 test client
        client.get("/dashboard")
        client.get("/team")
        self.press_logout(client, "/team")
        for path in ("/dashboard", "/team", "/bots/new"):
            self.assertEqual(client.get(path).status_code, 302, path)

    def test_another_device_of_the_same_user_keeps_its_own_session(self):
        # phiên là cookie ký phía client: đăng xuất ở thiết bị A không (và không thể) thu hồi cookie của thiết bị B
        laptop, phone = self.browser(), self.browser()
        self.press_logout(laptop)
        self.assertFalse(self.logged_in(laptop))
        self.assertTrue(self.logged_in(phone))


class Case3NormalFlow(LogoutCase):
    def test_login_again_after_logout_works_and_lands_on_the_dashboard(self):
        client = self.browser()
        self.press_logout(client)
        page = client.get("/auth/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = client.post("/auth/login", data={"email": "nguoidung@example.com", "password": PASSWORD, "csrf_token": csrf})
        self.assertEqual((response.status_code, response.headers["Location"]), (302, "/dashboard"))
        self.assertTrue(self.logged_in(client))

    def test_wrong_password_after_logout_still_fails_cleanly(self):
        client = self.browser()
        self.press_logout(client)
        page = client.get("/auth/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        response = client.post("/auth/login", data={"email": "nguoidung@example.com", "password": "sai", "csrf_token": csrf})
        self.assertIn(response.status_code, (200, 400, 401), "báo lỗi đăng nhập bình thường, không phải 302 vào dashboard hay 500")
        self.assertFalse(self.logged_in(client))

    def test_a_different_user_can_use_the_same_browser_after_logout_without_inheriting_the_team(self):
        other = auth_service.register("Người Khác", "nguoikhac@example.com", PASSWORD, "Team Khác")
        self.db.session.commit()
        client = self.browser()
        self.press_logout(client)
        page = client.get("/auth/login").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        client.post("/auth/login", data={"email": "nguoikhac@example.com", "password": PASSWORD, "csrf_token": csrf})
        html = client.get("/team").get_data(as_text=True)
        self.assertIn("Team Khác", html)
        self.assertNotIn("Team Đăng Xuất", html)
        self.assertEqual(other.email, "nguoikhac@example.com")


class Case4Edges(LogoutCase):
    def test_pressing_logout_twice_is_harmless(self):
        client = self.browser()
        html = client.get("/dashboard").get_data(as_text=True)
        token = token_in(html)
        first = client.post("/auth/logout", data={"csrf_token": token})
        second = client.post("/auth/logout", data={"csrf_token": token})
        self.assertEqual(first.headers["Location"], "/auth/login")
        self.assertEqual((second.status_code, second.headers["Location"]), (302, "/auth/login"), "không 500, không next=/auth/logout")
        page = client.get("/auth/login").get_data(as_text=True)
        self.assertNotIn("Vui lòng đăng nhập để tiếp tục", page)

    def test_logout_when_never_logged_in_does_not_fail(self):
        anonymous = self.app.test_client()
        for data in ({}, {"csrf_token": "x"}):
            response = anonymous.post("/auth/logout", data=data)
            self.assertEqual((response.status_code, response.headers["Location"]), (302, "/auth/login"))

    def test_after_a_double_logout_the_next_login_does_not_land_on_a_post_only_route(self):
        client = self.browser()
        token = token_in(client.get("/dashboard").get_data(as_text=True))
        client.post("/auth/logout", data={"csrf_token": token})
        client.post("/auth/logout", data={"csrf_token": token})
        page = client.get("/auth/login").get_data(as_text=True)
        self.assertNotIn("next=", page)
        self.assertNotIn("/auth/logout", re.sub(r'action="/auth/logout"', "", page))

    def test_malformed_bodies_never_cause_a_server_error(self):
        client = self.browser()
        for kwargs in ({"data": "csrf_token"}, {"json": {"csrf_token": 1}}, {"data": {"csrf_token": ["a", "b"]}}, {"data": b"\xff\xfe"}):
            response = client.post("/auth/logout", **kwargs)
            self.assertLess(response.status_code, 500, kwargs)
        self.assertTrue(self.logged_in(client))


class Case5Regression(LogoutCase):
    def test_pages_and_flows_that_need_login_still_work_and_still_require_it(self):
        client = self.browser()
        for path in ("/dashboard", "/bots/new", "/team", "/customers", "/inbox", "/auth/me"):
            self.assertEqual(client.get(path).status_code, 200, path)
        anonymous = self.app.test_client()
        for path in ("/dashboard", "/bots/new", "/team", "/customers", "/inbox", "/auth/me"):
            response = anonymous.get(path)
            self.assertEqual(response.status_code, 302, path)
            self.assertIn("/auth/login", response.headers["Location"], path)

    def test_creating_a_bot_and_a_team_still_works_with_the_new_sidebar_form(self):
        client = self.browser()
        page = client.get("/bots/new").get_data(as_text=True)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
        response = client.post("/bots/new", data={"name": "Bot sau khi sửa", **({"csrf_token": csrf.group(1)} if csrf else {})})
        self.assertIn(response.status_code, (200, 302))
        self.assertTrue(self.logged_in(client, "/team/new"))

    def test_existing_forms_keep_their_own_csrf_token_value(self):
        client = self.browser()
        html = client.get("/team").get_data(as_text=True)
        tokens = set(re.findall(r'name="csrf_token" value="([^"]+)"', html))
        self.assertEqual(len(tokens), 1, "mọi form trong 1 trang dùng cùng token của phiên (sidebar không sinh token khác)")

    def test_register_and_login_pages_render_without_the_shell(self):
        anonymous = self.app.test_client()
        for path in ("/auth/login", "/auth/register"):
            html = anonymous.get(path).get_data(as_text=True)
            self.assertEqual(anonymous.get(path).status_code, 200)
            self.assertNotIn('action="/auth/logout"', html, "trang chưa đăng nhập không có nút đăng xuất")

    def test_login_sets_a_fresh_socket_key_each_time(self):
        first, second = self.browser(), self.browser()
        with first.session_transaction() as a, second.session_transaction() as b:
            self.assertTrue(a["socket_key"] and b["socket_key"])
            self.assertNotEqual(a["socket_key"], b["socket_key"])


class SocketsEndWithTheSession(LogoutCase):
    """Kết nối realtime của Inbox chỉ được xác thực lúc connect: phải đóng khi phiên bị hủy, nếu không tab khác vẫn nghe được tin của team."""

    def setUp(self):
        super().setUp()
        from unittest import mock

        import socketio as python_socketio

        # Test client của Flask-SocketIO không chạy với message queue (Redis) và ghi đè các hàm gửi gói của server dùng chung: chỉ trong test này
        # thay manager bằng Manager cục bộ và trả lại nguyên trạng sau đó.
        server = socketio.server
        if "join_team" not in server.handlers.get("/", {}):
            # Handler join_team được đăng ký muộn (sau init_app) nên chỉ gắn vào server của lần create_app() ĐẦU TIÊN trong tiến trình; mỗi lớp DbCase
            # tạo thêm 1 app -> server mới không có nó. Production chỉ gọi create_app() một lần nên không bị; ở đây nạp lại để đăng ký lên server hiện tại.
            import importlib

            from app.inbox import events as inbox_events

            importlib.reload(inbox_events)
        local = python_socketio.Manager()
        local.set_server(server)
        for target, name, value in (
            (server, "manager", local), (server, "_send_packet", server._send_packet), (server, "_send_eio_packet", server._send_eio_packet),
            (server, "async_handlers", server.async_handlers), (server.eio, "async_handlers", server.eio.async_handlers),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect(self, client):
        socket = socketio.test_client(self.app, flask_test_client=client)
        self.assertTrue(socket.is_connected())
        socket.emit("join_team", {}, callback=True)  # callback=True để test client chờ xử lý xong (không dùng giá trị ack: nhiều client cùng lúc thì ack lẫn)
        return socket

    def team_room_members(self) -> list:
        from app.inbox import service as inbox_service
        from app.models import TeamMember

        team_id = TeamMember.query.filter_by(user_id=self.user.id).one().team_id
        return list(socketio.server.manager.get_participants("/", inbox_service.room(team_id)))

    def team_event_reaches(self, socket) -> bool:
        from app.inbox import service as inbox_service
        from app.models import TeamMember

        team_id = TeamMember.query.filter_by(user_id=self.user.id).one().team_id  # team của NGƯỜI DÙNG đăng nhập (không phải self.team của DbCase)
        socket.get_received()
        socketio.emit("inbox_message", {"x": 1}, to=inbox_service.room(team_id))
        return any(item["name"] == "inbox_message" for item in socket.get_received())

    def test_positive_control_a_connected_tab_does_receive_team_events(self):
        client = self.browser()
        tab = self.connect(client)
        self.assertTrue(self.team_event_reaches(tab), "nếu không nhận được ở đây thì các test bên dưới không chứng minh được gì")
        tab.disconnect()

    def test_logout_closes_every_socket_of_that_browser_session(self):
        client = self.browser()
        tab1, tab2 = self.connect(client), self.connect(client)
        self.assertTrue(tab1.is_connected() and tab2.is_connected())
        self.press_logout(client)
        self.assertFalse(tab1.is_connected())
        self.assertFalse(tab2.is_connected())

    def test_another_device_keeps_its_realtime_connection(self):
        laptop, phone = self.browser(), self.browser()
        laptop_socket, phone_socket = self.connect(laptop), self.connect(phone)
        self.press_logout(laptop)
        self.assertFalse(laptop_socket.is_connected())
        self.assertTrue(phone_socket.is_connected())
        phone_socket.disconnect()

    def test_no_message_of_the_team_reaches_a_logged_out_tab(self):
        client = self.browser()
        tab = self.connect(client)
        self.assertTrue(self.team_event_reaches(tab))
        self.press_logout(client)
        self.assertFalse(tab.is_connected())
        self.assertEqual(self.team_room_members(), [], "room của team không còn kết nối nào của phiên đã đăng xuất")

    def test_a_session_without_a_socket_key_connects_and_logs_out_without_error(self):
        # phiên đăng nhập từ trước bản này (không có socket_key)
        client = self.browser()
        with client.session_transaction() as session:
            session.pop("socket_key")
        tab = self.connect(client)
        response = self.press_logout(client)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(tab.is_connected(), "không có khóa thì không đóng được — chỉ ảnh hưởng phiên cũ, sẽ hết khi tải lại trang")
        tab.disconnect()

    def test_disconnect_helper_is_safe_with_nothing_to_close(self):
        self.assertEqual(events.disconnect_session_sockets(None), 0)
        self.assertEqual(events.disconnect_session_sockets(""), 0)
        self.assertEqual(events.disconnect_session_sockets("khoa-khong-ton-tai"), 0)

    def test_a_socket_that_cannot_be_closed_is_logged_and_logout_still_completes(self):
        from unittest import mock

        client = self.browser()
        tab = self.connect(client)
        with mock.patch.object(socketio.server, "disconnect", side_effect=RuntimeError("hỏng")), self.assertLogs("app.dashboard.events", level="WARNING"):
            response = self.press_logout(client)
        self.assertEqual(response.headers["Location"], "/auth/login")
        self.assertFalse(self.logged_in(client), "phiên HTTP vẫn bị hủy dù không đóng được socket")
        tab.disconnect()
