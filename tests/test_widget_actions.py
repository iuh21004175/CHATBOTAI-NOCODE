"""Website Action Engine (Phase M3/M4) phía SERVER-WIDGET: kênh Socket.IO của widget khách (xác thực bằng public_id + Origin, KHÔNG đăng nhập), giao lệnh hành động
cho widget và nhận kết quả (route nội bộ /internal/actions/dispatch + POST kết quả), bảo mật (Origin/domain/visitor/token), ngưỡng thanh toán + xác nhận của khách.
Cần DB *_test và Redis. Widget được mô phỏng bằng test (không có JS ở đây — phần DOM của embed.js kiểm thử thủ công, xem tests/README.md).
Đây là kiểm tra hồi quy ở mức mã — KHÔNG thay cho kiểm thử chức năng thực tế."""
import json
import unittest
import uuid
from unittest import mock

from app.models import BotDomain, Conversation, ModuleAction, PendingWidgetAction
from app.modules import dispatch, service
from app.widget import channel
from core.context_engine.agent import protocol as p
from extensions import redis_client, socketio
from tests.test_modules import ModulesCase

VISITOR = "visitor-abc12345"
SHOP = "https://shop.vn"


class WidgetCase(ModulesCase):
    """Bot có domain nhúng shop.vn, 1 hội thoại widget, 1 module đã phân tích và nền Redis riêng cho mỗi test."""

    def setUp(self):
        super().setUp()
        self.prefix = f"test-agent-{uuid.uuid4().hex[:10]}"
        self.old = {k: self.app.config[k] for k in ("AGENT_REDIS_PREFIX", "AGENT_ACTION_WAIT_SECONDS", "AGENT_MAX_ACTION_CALLS")}
        self.app.config.update(AGENT_REDIS_PREFIX=self.prefix, AGENT_ACTION_WAIT_SECONDS=1, AGENT_MAX_ACTION_CALLS=2)
        from extensions import limiter

        enabled, limiter.enabled = limiter.enabled, False  # giới hạn tốc độ dùng Redis thật, không liên quan tới nội dung các test này (có test riêng bên dưới)
        self.addCleanup(setattr, limiter, "enabled", enabled)
        self.addCleanup(self.restore)
        self.db.session.add(BotDomain(bot_id=self.bot.id, domain="shop.vn"))
        self.db.session.commit()
        self.db.session.refresh(self.bot)
        self.public_id = self.bot.public_id
        self.conversation_row = Conversation(bot_id=self.bot.id, channel="web_widget", visitor_id=VISITOR)
        self.db.session.add(self.conversation_row)
        self.db.session.commit()
        self.module = self.make_module(status="ready")
        self.client = self.app.test_client()
        self.emitted = []
        self.widget_behaviour = None
        patcher = mock.patch("app.widget.channel.emit_action", self.emit)
        patcher.start()
        self.addCleanup(patcher.stop)

    def restore(self):
        self.app.config.update(self.old)
        for key in list(redis_client.scan_iter(f"{self.prefix}:*")) + list(redis_client.scan_iter(f"widget-conn:{self.public_id}:*")) + list(redis_client.scan_iter("widget-action-result:*")):
            redis_client.delete(key)

    # ---- mô phỏng widget ----
    def emit(self, public_id, visitor_id, payload):
        self.emitted.append((public_id, visitor_id, payload))
        if self.widget_behaviour:
            self.widget_behaviour(payload)

    def report(self, payload, status="done", reason=None, data=None, origin=SHOP, **overrides):
        body = {"visitor_id": VISITOR, "token": payload["token"], "status": status, "reason": reason, "data": data}
        body.update(overrides)
        return self.client.post(f"/widget/api/{self.public_id}/actions/{payload['id']}/result", json=body, headers={"Origin": origin} if origin else {})

    def widget_replies(self, **kw):
        self.widget_behaviour = lambda payload: self.report(payload, **kw)

    def connect_widget(self, host="shop.vn", visitor=VISITOR):
        channel.register_connection(self.public_id, visitor, "sid-" + uuid.uuid4().hex[:6], host)

    # ---- gọi route nội bộ như MCP ----
    def context(self, run_id, conversation=True, bot=None):
        redis_client.set(p.Keys(self.prefix).run_context(run_id), json.dumps({
            "bot_id": (bot or self.bot).id, "conversation_id": self.conversation_row.id if conversation else None, "summarize_allowed": False}), ex=60)

    def dispatch(self, tool="doc_gia", params=None, run_id=None, context=True, token=None, remote="127.0.0.1", bot=None):
        bot = bot or self.bot
        run_id = run_id or "run-" + uuid.uuid4().hex[:8]
        if context:
            self.context(run_id, bot=bot)
        body = {"bot_id": bot.id, "run_id": run_id, "token": token or p.sign_run_token(self.app.config["SECRET_KEY"], bot.id, run_id), "tool": tool, "params": params or {}}
        return self.client.post("/internal/actions/dispatch", json=body, environ_overrides={"REMOTE_ADDR": remote})

    def approved(self, name="doc_gia", **kw):
        kw.setdefault("verified", True)
        return self.make_action(self.module, name=name, **kw)


class SocketChannel(WidgetCase):
    def setUp(self):
        super().setUp()
        import importlib

        import socketio as python_socketio

        # Test client của Flask-SocketIO không chạy với message queue (Redis) và ghi đè các hàm gửi gói của server dùng chung: chỉ trong test này thay manager
        # bằng Manager cục bộ và trả lại nguyên trạng. Handler đăng ký SAU init_app chỉ gắn vào server của lần create_app() đầu tiên trong tiến trình (mỗi lớp
        # DbCase tạo thêm 1 app) — production chỉ gọi create_app() một lần; ở đây nạp lại để gắn vào server hiện tại (cùng cách tests/test_auth_logout.py).
        from app.dashboard import events as dashboard_events
        from app.widget import events as widget_events

        importlib.reload(dashboard_events)
        importlib.reload(widget_events)
        server = socketio.server
        local = python_socketio.Manager()
        local.set_server(server)
        for target, name, value in (
            (server, "manager", local), (server, "_send_packet", server._send_packet), (server, "_send_eio_packet", server._send_eio_packet),
            (server, "async_handlers", server.async_handlers), (server.eio, "async_handlers", server.eio.async_handlers),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def connect(self, auth=None, origin=SHOP, namespace=channel.NAMESPACE):
        headers = {"Origin": origin} if origin else {}
        return socketio.test_client(self.app, namespace=namespace, headers=headers, auth=auth)

    def auth(self, **overrides):
        return {"public_id": self.public_id, "visitor_id": VISITOR, **overrides}

    def test_a_visitor_on_an_allowed_domain_connects_without_any_platform_login(self):
        client = self.connect(self.auth())
        self.assertTrue(client.is_connected(channel.NAMESPACE))
        self.assertEqual(channel.connected_hosts(self.public_id, VISITOR), {"shop.vn"})
        client.disconnect(channel.NAMESPACE)
        self.assertEqual(channel.connected_hosts(self.public_id, VISITOR), set(), "ngắt kết nối -> dọn presence")

    def test_subdomains_of_a_declared_domain_are_accepted_and_recorded_with_their_own_host(self):
        client = self.connect(self.auth(), origin="https://www.shop.vn")
        self.assertTrue(client.is_connected(channel.NAMESPACE))
        client.disconnect(channel.NAMESPACE)
        client = self.connect(self.auth(), origin="https://m.shop.vn")
        self.assertEqual(channel.connected_hosts(self.public_id, VISITOR), {"m.shop.vn"})
        client.disconnect(channel.NAMESPACE)

    def test_rejected_when_origin_is_missing_or_not_a_declared_domain(self):
        for origin in ("https://evil.com", "https://shop.vn.evil.com", "null", None, "http://shopx.vn"):
            with self.subTest(origin=origin):
                client = self.connect(self.auth(), origin=origin)
                self.assertFalse(client.is_connected(channel.NAMESPACE))
        self.assertEqual(channel.connected_hosts(self.public_id, VISITOR), set())

    def test_rejected_for_bad_or_missing_identifiers(self):
        cases = {
            "no auth": None, "auth not a dict": "x", "unknown public id": self.auth(public_id="khong-co"), "numeric bot id": self.auth(public_id=str(self.bot.id)),
            "no visitor": {"public_id": self.public_id}, "short visitor": self.auth(visitor_id="abc"), "visitor with slash": self.auth(visitor_id="a/b/c/d/e/f/g/h"),
            "visitor too long": self.auth(visitor_id="a" * 65), "public id not a string": self.auth(public_id=123),
        }
        for label, auth in cases.items():
            with self.subTest(label=label):
                self.assertFalse(self.connect(auth).is_connected(channel.NAMESPACE))

    def test_the_bots_own_domains_are_used_not_another_bots(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.db.session.add(BotDomain(bot_id=other.id, domain="other.vn"))
        self.db.session.commit()
        self.db.session.refresh(other)
        self.assertFalse(self.connect({"public_id": other.public_id, "visitor_id": VISITOR}, origin=SHOP).is_connected(channel.NAMESPACE))
        client = self.connect({"public_id": other.public_id, "visitor_id": VISITOR}, origin="https://other.vn")
        self.assertTrue(client.is_connected(channel.NAMESPACE))
        client.disconnect(channel.NAMESPACE)

    def test_two_tabs_of_one_visitor_are_tracked_separately(self):
        first, second = self.connect(self.auth()), self.connect(self.auth(), origin="https://m.shop.vn")
        self.assertEqual(channel.connected_hosts(self.public_id, VISITOR), {"shop.vn", "m.shop.vn"})
        first.disconnect(channel.NAMESPACE)
        self.assertEqual(channel.connected_hosts(self.public_id, VISITOR), {"m.shop.vn"})
        second.disconnect(channel.NAMESPACE)

    def test_the_admin_namespace_still_requires_login(self):
        self.assertFalse(socketio.test_client(self.app, headers={"Origin": "http://localhost"}).is_connected(), "namespace mặc định của khung quản trị không bị ảnh hưởng")

    def test_the_widget_handlers_never_read_the_platform_session(self):
        import ast
        import inspect

        from app.widget import events

        names = set()
        for node in ast.walk(ast.parse(inspect.getsource(events))):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        for forbidden in ("current_user", "is_authenticated", "session", "login_required", "resolve_team_id"):
            self.assertNotIn(forbidden, names, forbidden)


class DispatchHappyPath(WidgetCase):
    def test_reading_page_info_returns_the_widgets_real_answer_and_records_the_command(self):
        self.approved("doc_gia", spec={"selector": "#price", "attribute": "text"})
        self.connect_widget()
        self.widget_replies(status="done", data="199.000đ")
        response = self.dispatch("doc_gia")
        self.assertEqual(response.get_json(), {"status": "done", "reason": None, "data": "199.000đ"})
        (public_id, visitor, payload), = self.emitted
        self.assertEqual((public_id, visitor), (self.public_id, VISITOR))
        self.assertEqual((payload["type"], payload["name"], payload["expect_host"], payload["confirm"]), ("read_info", "doc_gia", "shop.vn", False))
        self.assertEqual(payload["spec"], {"selector": "#price", "attribute": "text"})
        self.assertEqual(payload["label"], "mô tả doc_gia")
        row = PendingWidgetAction.query.one()
        self.assertEqual((row.status, row.conversation_id, row.bot_id, row.result, row.requires_confirm), ("done", self.conversation_row.id, self.bot.id,
                                                                                                        {"status": "done", "reason": None, "data": "199.000đ"}, False))
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(payload["token"], row.token)

    def test_only_declared_params_reach_the_widget_cleaned(self):
        self.approved("dien_form", action_type="fill_form", spec={"form_selector": "#contact-form", "submit": False, "fields": [
            {"selector": "#fullname", "value_from_slot": "ho_ten"}, {"selector": "#phone", "value_from_slot": "sdt"}]})
        self.connect_widget()
        self.widget_replies()
        self.dispatch("dien_form", {"ho_ten": "  Nguyễn\x00 Văn\x07 A ", "sdt": 912345678, "la": "x", "bot_id": 5, "sdt2": "y"})
        (_, _, payload), = self.emitted
        self.assertEqual(payload["params"], {"ho_ten": "Nguyễn Văn A", "sdt": "912345678"})
        self.dispatch("dien_form", {"ho_ten": "x" * 1000})
        self.assertEqual(len(self.emitted[1][2]["params"]["ho_ten"]), dispatch.MAX_PARAM_CHARS)

    def test_a_form_action_with_no_customer_data_is_refused_before_touching_the_widget(self):
        self.approved("dien_form", action_type="fill_form", spec={"form_selector": "#f", "fields": [{"selector": "#a", "value_from_slot": "ho_ten"}]})
        self.connect_widget()
        for params in ({}, {"ho_ten": ""}, {"ho_ten": "   "}, {"khac": "x"}, {"ho_ten": None}, {"ho_ten": True}):
            with self.subTest(params=params):
                data = self.dispatch("dien_form", params).get_json()
                self.assertEqual(data["status"], "invalid")
                self.assertIn("ho_ten", data["reason"])
        self.assertEqual((self.emitted, PendingWidgetAction.query.count()), ([], 0))

    def test_a_click_without_params_is_fine(self):
        self.approved("bam", action_type="click", spec={"selector": "#add-btn", "event": "click"})
        self.connect_widget()
        self.widget_replies()
        self.assertEqual(self.dispatch("bam").get_json()["status"], "done")

    def test_widget_failure_is_relayed_as_failure_never_as_success(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.widget_replies(status="failed", reason="domain_mismatch")
        self.assertEqual(self.dispatch("doc_gia").get_json(), {"status": "failed", "reason": "domain_mismatch", "data": None})
        self.assertEqual(PendingWidgetAction.query.one().status, "failed")

    def test_no_reply_within_the_wait_is_a_timeout_not_a_success_and_a_late_result_is_still_recorded(self):
        self.approved("doc_gia")
        self.connect_widget()
        data = self.dispatch("doc_gia").get_json()
        self.assertEqual(data["status"], "timeout")
        row = PendingWidgetAction.query.one()
        self.assertEqual(row.status, "pending")
        payload = self.emitted[0][2]
        self.assertEqual(self.report(payload, data="199.000đ").status_code, 200, "kết quả đến muộn vẫn được nhận")
        self.db.session.refresh(row)
        self.assertEqual(row.status, "done")

    def test_each_call_is_a_fresh_command_with_its_own_token(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.widget_replies()
        self.dispatch("doc_gia", run_id="r1")
        self.dispatch("doc_gia", run_id="r2")
        tokens = {payload["token"] for _, _, payload in self.emitted}
        self.assertEqual((len(tokens), PendingWidgetAction.query.count()), (2, 2))


class DispatchRefusals(WidgetCase):
    def refused(self, reason_part, **kw):
        data = self.dispatch(**kw).get_json()
        self.assertEqual(data["status"], "unavailable", data)
        self.assertIn(reason_part, data["reason"])
        self.assertEqual((self.emitted, PendingWidgetAction.query.count()), ([], 0), "không đẩy lệnh và không ghi hàng đợi")

    def test_unapproved_or_unknown_actions_are_not_available(self):
        self.approved("cho_duyet", verified=False)
        self.connect_widget()
        self.refused("action_not_available", tool="cho_duyet")
        self.refused("action_not_available", tool="khong_co")
        self.refused("action_not_available", tool="finish_answer")

    def test_another_bots_action_cannot_be_reached_even_with_this_bots_token(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.make_action(self.make_module(bot=other, status="ready"), name="cua_bot_b", verified=True)
        self.connect_widget()
        self.refused("action_not_available", tool="cua_bot_b")

    def test_chat_preview_without_a_conversation_has_no_widget_to_run_in(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.context("run-x", conversation=False)
        self.refused("no_widget_session", run_id="run-x", context=False)

    def test_missing_expired_or_foreign_run_context_is_refused(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.refused("no_widget_session", context=False)
        other = self.service.create_bot(self.team.id, "Bot B")
        self.context("run-y", bot=other)   # ngữ cảnh lượt này thuộc bot khác
        self.refused("no_widget_session", run_id="run-y", context=False)

    def test_a_conversation_from_another_channel_or_bot_cannot_run_actions(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.conversation_row.channel = "facebook"
        self.db.session.commit()
        self.refused("no_widget_session")

    def test_customer_widget_must_be_connected(self):
        self.approved("doc_gia")
        self.refused("customer_widget_not_connected")
        self.connect_widget(visitor="ai-do-khac-12345")   # khách khác đang kết nối: không phải khách của hội thoại này
        self.refused("customer_widget_not_connected")

    def test_widget_embedded_on_a_different_domain_than_the_crawled_one_never_runs_actions(self):
        self.approved("doc_gia")
        self.connect_widget(host="other-site.vn")
        self.refused("widget_not_on_the_configured_website")
        self.connect_widget(host="shop.vn.evil.com")
        self.refused("widget_not_on_the_configured_website")

    def test_a_widget_on_a_subdomain_of_the_crawled_site_is_accepted(self):
        self.approved("doc_gia")
        self.connect_widget(host="m.shop.vn")
        self.widget_replies()
        self.assertEqual(self.dispatch("doc_gia").get_json()["status"], "done")

    def test_the_per_run_call_budget_is_enforced_by_flask(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.widget_replies()
        run = "run-budget"
        statuses = [self.dispatch("doc_gia", run_id=run).get_json()["status"] for _ in range(4)]
        self.assertEqual(statuses, ["done", "done", "limit", "limit"])
        self.assertEqual(len(self.emitted), 2)
        self.assertEqual(self.dispatch("doc_gia", run_id="run-khac").get_json()["status"], "done", "lượt khác có ngân sách riêng")

    def test_internal_route_layers_still_apply(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.assertEqual(self.dispatch(remote="203.0.113.5").status_code, 403, "chỉ loopback")
        self.assertEqual(self.dispatch(token="hong").status_code, 403)
        self.assertEqual(self.dispatch(token=p.sign_run_token(self.app.config["SECRET_KEY"], self.bot.id + 1, "run-1")).status_code, 403, "token của bot khác")
        self.assertEqual(self.client.post("/internal/actions/dispatch", json={"bot_id": self.bot.id, "run_id": "r", "token": "t"}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code, 403)
        run = "run-tool"
        self.context(run)
        token = p.sign_run_token(self.app.config["SECRET_KEY"], self.bot.id, run)
        for tool in (None, "", 5):
            response = self.client.post("/internal/actions/dispatch", json={"bot_id": self.bot.id, "run_id": run, "token": token, "tool": tool}, environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
            self.assertEqual(response.status_code, 400, tool)
        self.assertEqual(self.emitted, [])

    def test_the_payload_and_response_never_expose_selectors_or_secrets_to_the_model(self):
        self.approved("doc_gia")
        self.connect_widget()
        self.widget_replies(data="199.000đ")
        raw = self.dispatch("doc_gia").get_data(as_text=True)
        for hidden in ("selector", "#price", "token", "shop.vn"):
            self.assertNotIn(hidden, raw)


class PaymentSafety(WidgetCase):
    PAY = {"selector": "#pay-btn", "event": "click"}

    def pay_action(self, **kw):
        return self.approved("dat_hang", risk="payment", action_type="click", spec=self.PAY, confidence=0.95, **kw)

    def test_payment_action_is_not_reachable_while_the_switch_is_off_even_if_marked_verified(self):
        self.pay_action()
        self.connect_widget()
        data = self.dispatch("dat_hang").get_json()
        self.assertEqual((data["status"], data["reason"]), ("unavailable", "action_not_available"))
        self.assertEqual(self.emitted, [])

    def test_with_the_switch_on_the_customer_must_confirm_and_the_agent_is_told_it_is_only_pending(self):
        self.pay_action()
        service.set_payment_switch(self.bot, True)
        self.connect_widget()
        data = self.dispatch("dat_hang", {"ho_ten": "x"}).get_json()
        self.assertEqual(data["status"], "awaiting_confirmation", "không được trả 'done' khi khách chưa bấm Đồng ý")
        (_, _, payload), = self.emitted
        self.assertTrue(payload["confirm"], "widget phải hiện hộp xác nhận trước khi thực thi")
        row = PendingWidgetAction.query.one()
        self.assertEqual((row.status, row.requires_confirm), ("pending", True))
        # khách bấm Đồng ý -> widget thực thi -> báo kết quả: chỉ khi đó mới ghi done
        self.assertEqual(self.report(payload).status_code, 200)
        self.db.session.refresh(row)
        self.assertEqual(row.status, "done")

    def test_customer_cancelling_is_recorded_as_a_failed_command_and_does_not_unapprove_the_action(self):
        action = self.pay_action()
        service.set_payment_switch(self.bot, True)
        action.verified = True
        self.db.session.commit()
        self.connect_widget()
        self.dispatch("dat_hang")
        payload = self.emitted[0][2]
        self.report(payload, status="failed", reason="cancelled_by_customer")
        row = PendingWidgetAction.query.one()
        self.assertEqual((row.status, row.result["reason"]), ("failed", "cancelled_by_customer"))
        self.db.session.refresh(action)
        self.assertTrue(action.verified)

    def test_confirmation_is_required_even_when_the_stored_risk_was_tampered_down(self):
        # dữ liệu bị sửa tay xuống 'read_only' nhưng nút là trang thanh toán -> sàn cứng vẫn là payment: cần công tắc + xác nhận
        self.approved("dat_hang", risk="read_only", action_type="click", spec=self.PAY)
        self.module.urls[0].url_role = "checkout"
        self.db.session.commit()
        self.connect_widget()
        self.assertEqual(self.dispatch("dat_hang").get_json()["status"], "unavailable")
        service.set_payment_switch(self.bot, True)
        self.assertEqual(self.dispatch("dat_hang").get_json()["status"], "awaiting_confirmation")

    def test_cart_actions_do_not_need_the_payment_switch_or_the_confirmation_box(self):
        self.approved("them_gio", action_type="add_to_cart", risk="cart", spec={"selector": "#add-btn", "event": "click"})
        self.connect_widget()
        self.widget_replies()
        self.assertEqual(self.dispatch("them_gio").get_json()["status"], "done")
        self.assertFalse(self.emitted[0][2]["confirm"])


class ResultRoute(WidgetCase):
    action = None

    def pending(self, **kw):
        action = self.action or self.approved("doc_gia")
        row = PendingWidgetAction(bot_id=self.bot.id, conversation_id=self.conversation_row.id, action_id=action.id, action_name="doc_gia", token="tok-bi-mat-123",
                                  params={}, status=kw.get("status", "pending"))
        self.db.session.add(row)
        self.db.session.commit()
        self.action = action
        return row

    def post(self, row, origin=SHOP, public_id=None, **body):
        payload = {"visitor_id": VISITOR, "token": "tok-bi-mat-123", "status": "done", **body}
        headers = {"Origin": origin} if origin else {}
        return self.client.post(f"/widget/api/{public_id or self.public_id}/actions/{row.id}/result", json=payload, headers=headers)

    def test_valid_report_updates_the_row_and_answers_with_cors_for_the_embedding_site(self):
        row = self.pending()
        response = self.post(row, data="  199.000đ  ")
        self.assertEqual((response.status_code, response.get_json()), (200, {"ok": True}))
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], SHOP)
        self.db.session.refresh(row)
        self.assertEqual((row.status, row.result), ("done", {"status": "done", "reason": None, "data": "199.000đ"}))

    def test_preflight_is_answered_for_allowed_origins_only(self):
        row = self.pending()
        url = f"/widget/api/{self.public_id}/actions/{row.id}/result"
        allowed = self.client.options(url, headers={"Origin": SHOP})
        self.assertEqual((allowed.status_code, allowed.headers["Access-Control-Allow-Origin"]), (204, SHOP))
        self.assertEqual(self.client.options(url, headers={"Origin": "https://evil.com"}).status_code, 403)

    def test_origin_must_belong_to_the_bots_declared_domains(self):
        row = self.pending()
        for origin in ("https://evil.com", "https://shop.vn.evil.com", None):
            with self.subTest(origin=origin):
                self.assertEqual(self.post(row, origin=origin).status_code, 403)
        self.db.session.refresh(row)
        self.assertEqual(row.status, "pending")

    def test_wrong_token_visitor_bot_or_id_all_look_the_same_and_change_nothing(self):
        row = self.pending()
        other = self.service.create_bot(self.team.id, "Bot B")
        self.db.session.add(BotDomain(bot_id=other.id, domain="shop.vn"))
        self.db.session.commit()
        self.db.session.refresh(other)
        bad = [
            self.post(row, token="sai"), self.post(row, token=None), self.post(row, visitor_id="visitor-khac-12345"), self.post(row, visitor_id=None),
            self.post(row, public_id=other.public_id), self.client.post(f"/widget/api/{self.public_id}/actions/999999/result", json={"visitor_id": VISITOR, "token": "x", "status": "done"}, headers={"Origin": SHOP}),
            self.client.post(f"/widget/api/{self.bot.id}/actions/{row.id}/result", json={}, headers={"Origin": SHOP}),
        ]
        self.assertEqual([r.status_code for r in bad], [404, 404, 404, 404, 404, 404, 404])
        self.assertEqual(len({r.get_data(as_text=True) for r in bad[:6]}), 1, "cùng nội dung lỗi: không cho dò xem lệnh có tồn tại không")
        self.db.session.refresh(row)
        self.assertEqual(row.status, "pending")

    def test_a_command_can_be_answered_only_once(self):
        row = self.pending()
        self.assertEqual(self.post(row).status_code, 200)
        self.assertEqual(self.post(row, status="failed", reason="error").status_code, 409)
        self.db.session.refresh(row)
        self.assertEqual(row.status, "done")

    def test_only_known_statuses_and_reasons_are_stored(self):
        row = self.pending()
        self.assertEqual(self.post(row, status="hoan-thanh").status_code, 400)
        self.assertEqual(self.post(row, status=None).status_code, 400)
        response = self.post(row, status="failed", reason="<script>alert(1)</script>", data="x" * 5000)
        self.assertEqual(response.status_code, 200)
        self.db.session.refresh(row)
        self.assertEqual(row.result["reason"], "error", "lý do lạ -> 'error'")
        self.assertEqual(len(row.result["data"]), 500)

    def test_a_reason_is_ignored_on_success_and_data_blank_becomes_none(self):
        row = self.pending()
        self.post(row, status="done", reason="element_not_found", data="   ")
        self.db.session.refresh(row)
        self.assertEqual(row.result, {"status": "done", "reason": None, "data": None})
        self.db.session.refresh(self.action)
        self.assertTrue(self.action.verified, "báo thành công thì không đụng tới trạng thái duyệt")

    def test_element_not_found_unapproves_the_action_and_records_why_without_guessing_a_new_selector(self):
        row = self.pending()
        spec_before = dict(self.action.selector_spec)
        self.assertEqual(self.post(row, status="failed", reason="element_not_found").status_code, 200)
        self.db.session.refresh(self.action)
        self.assertFalse(self.action.verified)
        self.assertEqual(self.action.failure_reason, "element_not_found")
        self.assertIsNotNone(self.action.failed_at)
        self.assertEqual(self.action.selector_spec, spec_before, "không tự đoán selector mới")
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), [], "không còn giao cho agent")
        self.assertEqual(self.module.status, "ready", "không tự crawl lại")
        html = self.app.test_client()
        with html.session_transaction() as session:
            session["_user_id"] = str(self.team.user.id)
            session["team_id"] = self.team.id
        page = html.get(f"/bots/{self.bot.id}/modules/{self.module.id}").get_data(as_text=True)
        self.assertIn("Cảnh báo: trang đã đổi", page)
        self.assertIn("element_not_found", page)

    def test_other_failure_reasons_keep_the_action_approved(self):
        for reason in ("domain_mismatch", "cancelled_by_customer", "missing_param", "error", "invalid_spec"):
            with self.subTest(reason=reason):
                row = self.pending()
                self.post(row, status="failed", reason=reason)
                self.db.session.refresh(self.action)
                self.assertTrue(self.action.verified)

    def test_the_result_reaches_the_waiting_agent_turn_through_redis(self):
        row = self.pending()
        self.post(row, data="ok")
        self.assertEqual(channel.wait_result(row.id, 1), {"status": "done", "reason": None, "data": "ok"})
        self.assertIsNone(channel.wait_result(row.id, 1), "mỗi kết quả chỉ được nhận một lần")

    def test_a_deleted_action_keeps_its_history_row(self):
        row = self.pending()
        service.delete_module(self.module)
        self.db.session.refresh(row)
        self.assertIsNone(row.action_id)
        self.assertEqual(row.action_name, "doc_gia")

    def test_it_only_needs_the_widget_origin_not_a_platform_login(self):
        row = self.pending()
        self.assertEqual(self.app.test_client().post(f"/widget/api/{self.public_id}/actions/{row.id}/result", json={"visitor_id": VISITOR, "token": "tok-bi-mat-123", "status": "done"},
                                                     headers={"Origin": SHOP}).status_code, 200)


class RateLimit(WidgetCase):
    def test_the_result_route_is_rate_limited_per_ip(self):
        from extensions import limiter

        limiter.enabled = True
        ip = f"198.51.100.{uuid.uuid4().int % 250 + 1}"
        url = f"/widget/api/{self.public_id}/actions/1/result"
        codes = [self.client.post(url, json={}, headers={"Origin": SHOP}, environ_overrides={"REMOTE_ADDR": ip}).status_code for _ in range(62)]
        self.assertIn(429, codes)
        self.assertEqual(codes[0], 404)


class ManifestFeedsTheAgentThroughTheProvider(WidgetCase):
    def test_make_agent_runner_wires_the_action_provider_only_when_agent_mode_is_on(self):
        from app.dashboard import service as dashboard_service
        from config import Config

        with mock.patch.object(Config, "AGENT_ENABLED", False):
            self.assertIsNone(dashboard_service.make_agent_runner())
        with mock.patch.object(Config, "AGENT_ENABLED", True):
            runner = dashboard_service.make_agent_runner()
        self.assertIs(runner.action_provider, service.agent_tools_for_bot)
        self.approved("doc_gia")
        self.assertEqual([t["name"] for t in runner.action_provider(self.bot.id)], ["doc_gia"])


if __name__ == "__main__":
    unittest.main()
