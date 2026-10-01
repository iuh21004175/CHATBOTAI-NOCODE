"""Nạp AI Credit qua payOS: chữ ký/định dạng gọi API (core/payos_client.py, thuần), tạo đơn, webhook, trang quay lại, hiển thị /profile.
KHÔNG gọi mạng thật (urlopen/create_payment_link/get_payment được thay bằng bản giả). Phần thuần không cần DB; còn lại cần DB *_test
(xem tests/README.md). Đây là kiểm tra hồi quy ở mức mã — KHÔNG thay cho kiểm thử chức năng thực tế với payOS thật."""
import hashlib
import hmac
import io
import json
import unittest
import urllib.error
from decimal import Decimal
from unittest import mock

from app.credits import service as credits
from app.models import CreditTransaction, PaymentOrder, TeamMember, User
from app.payments import service as payments
from config import Config
from core import payos_client as payos
from tests.test_credit import CreditCase

KEY = "checksum-test-key"
CFG = payos.PayOSConfig("cid", "akey", KEY, "https://payos.test")


def hex_hmac(message: str, key: str = KEY) -> str:
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(data):
    return FakeResponse(json.dumps({"code": "00", "desc": "success", "data": data}).encode())


class Signatures(unittest.TestCase):
    def test_payment_request_signature_uses_the_documented_alphabetical_string(self):
        signature = payos.sign_payment_request(KEY, amount=50000, cancel_url="https://x.vn/c", description="AICREDIT", order_code=123456789012, return_url="https://x.vn/r")
        expected = hex_hmac("amount=50000&cancelUrl=https://x.vn/c&description=AICREDIT&orderCode=123456789012&returnUrl=https://x.vn/r")
        self.assertEqual(signature, expected)

    def test_object_signature_sorts_keys_and_normalises_values(self):
        data = {"orderCode": 123, "amount": 3000, "flag": True, "off": False, "nothing": None, "list": [{"b": 1, "a": 2}], "text": "Thành công"}
        expected = hex_hmac('amount=3000&flag=true&list=[{"b":1,"a":2}]&nothing=&off=false&orderCode=123&text=Thành công')
        self.assertEqual(payos.sign_object(data, KEY), expected)
        self.assertEqual(payos.sign_object({"a": 1, "b": 2}, KEY), payos.sign_object({"b": 2, "a": 1}, KEY), "thứ tự khóa đầu vào không ảnh hưởng")

    def test_verify_webhook_accepts_only_an_intact_signed_payload(self):
        data = {"orderCode": 1, "amount": 50000, "code": "00", "desc": "Thành công", "counterAccountName": None}
        good = {"code": "00", "desc": "success", "success": True, "data": data, "signature": payos.sign_object(data, KEY)}
        self.assertEqual(payos.verify_webhook(good, KEY), data)
        tampered = {**good, "data": {**data, "amount": 1}}
        for bad in (tampered, {**good, "signature": "0" * 64}, {**good, "signature": None}, {"data": data}, {"signature": "x"}, None, [], "str"):
            with self.assertRaises(payos.PayOSSignatureError, msg=repr(bad)[:60]):
                payos.verify_webhook(bad, KEY)
        with self.assertRaises(payos.PayOSSignatureError):
            payos.verify_webhook(good, "khoa-khac")


class ApiCalls(unittest.TestCase):
    def test_create_link_sends_signed_body_with_auth_headers_and_returns_the_link(self):
        seen = []

        def fake(request, timeout=None):
            seen.append(request)
            return ok({"checkoutUrl": "https://pay.payos.vn/web/abc", "paymentLinkId": "plid"})

        with mock.patch("urllib.request.urlopen", fake):
            data = payos.create_payment_link(CFG, order_code=111222333444, amount=200000, description="AICREDIT", return_url="https://x/r", cancel_url="https://x/c", expired_at=1800000000)
        (request,) = seen
        self.assertEqual((request.method, request.full_url), ("POST", "https://payos.test/v2/payment-requests"))
        self.assertEqual((request.get_header("X-client-id"), request.get_header("X-api-key")), ("cid", "akey"))
        body = json.loads(request.data)
        self.assertEqual((body["orderCode"], body["amount"], body["description"], body["expiredAt"]), (111222333444, 200000, "AICREDIT", 1800000000))
        self.assertEqual(body["signature"], payos.sign_payment_request(KEY, amount=200000, cancel_url="https://x/c", description="AICREDIT", order_code=111222333444, return_url="https://x/r"))
        self.assertEqual(data["checkoutUrl"], "https://pay.payos.vn/web/abc")

    def test_get_payment_and_confirm_webhook_paths(self):
        seen = []

        def fake(request, timeout=None):
            seen.append(request)
            return ok({"status": "PAID"})

        with mock.patch("urllib.request.urlopen", fake):
            self.assertEqual(payos.get_payment(CFG, 42)["status"], "PAID")
            payos.confirm_webhook(CFG, "https://x.vn/payos/webhook")
        self.assertEqual([(r.method, r.full_url) for r in seen], [("GET", "https://payos.test/v2/payment-requests/42"), ("POST", "https://payos.test/confirm-webhook")])
        self.assertEqual(json.loads(seen[1].data), {"webhookUrl": "https://x.vn/payos/webhook"})

    def test_every_failure_is_raised_not_swallowed(self):
        cases = {
            "http": urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(json.dumps({"code": "401", "desc": "Sai khóa"}).encode())),
            "network": urllib.error.URLError("dns"),
            "timeout": TimeoutError("chậm"),
        }
        for name, error in cases.items():
            with mock.patch("urllib.request.urlopen", side_effect=error), self.assertRaises(payos.PayOSError, msg=name):
                payos.get_payment(CFG, 1)
        bad_bodies = [b"khong phai json", b"[]", json.dumps({"code": "231", "desc": "Đơn tồn tại"}).encode(), json.dumps({"code": "00", "desc": "ok"}).encode()]
        for body in bad_bodies:
            with mock.patch("urllib.request.urlopen", return_value=FakeResponse(body)), self.assertRaises(payos.PayOSError, msg=body[:20]):
                payos.get_payment(CFG, 1)
        with mock.patch("urllib.request.urlopen", return_value=ok({"paymentLinkId": "x"})), self.assertRaises(payos.PayOSError):
            payos.create_payment_link(CFG, order_code=1, amount=50000, description="d", return_url="r", cancel_url="c")

    def test_http_error_message_carries_the_reason_from_payos(self):
        error = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(json.dumps({"code": "20", "desc": "Mô tả quá dài"}).encode()))
        with mock.patch("urllib.request.urlopen", side_effect=error), self.assertRaises(payos.PayOSError) as caught:
            payos.get_payment(CFG, 1)
        self.assertIn("Mô tả quá dài", str(caught.exception))

    def test_missing_configuration_fails_before_any_network_call(self):
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("không được gọi mạng")), self.assertRaises(payos.PayOSError):
            payos.get_payment(payos.PayOSConfig("", "", ""), 1)


class Amounts(unittest.TestCase):
    def test_packages_and_custom_amounts(self):
        for good in (50_000, 200_000, 500_000, 1_000_000, 1_500_000, 100_000_000):
            self.assertIsNone(payments.validate_amount(good), good)
        for bad in (0, -50_000, 49_999, 100_000, 999_000, 1_000_500, 100_001_000, "50000", None, True, 12.5):
            self.assertTrue(payments.validate_amount(bad), bad)

    def test_parse_amount(self):
        self.assertEqual([payments.parse_amount(x) for x in ("50000", " 1.000.000 ", "1,500,000", "200000")], [50000, 1000000, 1500000, 200000])
        for bad in ("", None, "abc", "-5", "1e6", "12.5x", "0", "9" * 13):
            self.assertIsNone(payments.parse_amount(bad), bad)


class PaymentCase(CreditCase):
    def setUp(self):
        super().setUp()
        for name, value in (("PAYOS_CLIENT_ID", "cid"), ("PAYOS_API_KEY", "akey"), ("PAYOS_CHECKSUM_KEY", KEY), ("PUBLIC_BASE_URL", "https://shop.test")):
            patcher = mock.patch.object(Config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        from extensions import limiter

        enabled, limiter.enabled = limiter.enabled, False
        self.addCleanup(setattr, limiter, "enabled", enabled)
        self.client = self.login(self.team.user)
        credits.ensure_account(self.team.id)
        self.db.session.commit()

    def login(self, user):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["team_id"] = self.team.id
            session["csrf_token"] = "tok"
        return client

    def order(self, amount=50_000, status="pending", team=None, code=None):
        order = PaymentOrder(order_code=code or 555000111000 + PaymentOrder.query.count(), team_id=(team or self.team).id, amount_vnd=amount,
                             credit_vnd=Decimal(amount), status=status, payment_link_id="plid")
        self.db.session.add(order)
        self.db.session.commit()
        return order

    def signed(self, order_code, amount, code="00", tamper=False):
        data = {"orderCode": order_code, "amount": amount, "description": "AICREDIT", "accountNumber": "123", "reference": "TF1", "transactionDateTime": "2026-09-25 20:00:00",
                "currency": "VND", "paymentLinkId": "plid", "code": code, "desc": "Thành công", "counterAccountBankId": None, "counterAccountName": None,
                "virtualAccountNumber": ""}
        payload = {"code": code, "desc": "success", "success": code == "00", "data": data, "signature": payos.sign_object(data, KEY)}
        if tamper:
            payload["data"] = {**data, "amount": amount * 100}
        return payload

    def webhook(self, payload):
        return self.app.test_client().post("/payos/webhook", json=payload)

    def topups(self):
        self.db.session.expire_all()
        return CreditTransaction.query.filter_by(team_id=self.team.id, type="topup").all()

    def balance(self):
        return self.account().balance_vnd


class CreateOrder(PaymentCase):
    def post(self, data, client=None):
        return (client or self.client).post("/profile/topup", data={"csrf_token": "tok", **data})

    def fake_link(self):
        return mock.patch("core.payos_client.create_payment_link", return_value={"checkoutUrl": "https://pay.payos.vn/web/xyz", "paymentLinkId": "plid-1"})

    def test_a_package_creates_a_pending_order_and_redirects_to_the_payos_checkout(self):
        with self.fake_link() as create:
            response = self.post({"package": "200000"})
        self.assertEqual((response.status_code, response.headers["Location"]), (303, "https://pay.payos.vn/web/xyz"))
        (order,) = PaymentOrder.query.all()
        self.assertEqual((order.team_id, order.user_id, order.amount_vnd, order.credit_vnd, order.status), (self.team.id, self.team.user.id, 200000, Decimal(200000), "pending"))
        self.assertEqual((order.payment_link_id, order.checkout_url), ("plid-1", "https://pay.payos.vn/web/xyz"))
        self.assertTrue(100_000_000_000 <= order.order_code < 1_000_000_000_000)
        kwargs = create.call_args.kwargs
        self.assertEqual((kwargs["order_code"], kwargs["amount"], kwargs["description"]), (order.order_code, 200000, "AICREDIT"))
        self.assertLessEqual(len(kwargs["description"]), 9)
        self.assertEqual(kwargs["return_url"], "https://shop.test/profile/topup/return")
        self.assertEqual(self.balance(), Config.TRIAL_CREDIT_VND, "chưa thanh toán thì chưa có Credit")

    def test_custom_amount_from_one_million_is_accepted(self):
        with self.fake_link():
            response = self.post({"package": "custom", "custom_amount": "1.500.000"})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(PaymentOrder.query.one().amount_vnd, 1_500_000)

    def test_invalid_amounts_and_packages_create_nothing_and_never_call_payos(self):
        bad_forms = [{"package": "12345"}, {"package": "custom", "custom_amount": "999.000"}, {"package": "custom", "custom_amount": "1.000.500"},
                     {"package": "custom", "custom_amount": "abc"}, {"package": "custom", "custom_amount": ""}, {}, {"package": "-50000"},
                     {"package": "custom", "custom_amount": "200.000.000"}]
        with self.fake_link() as create:
            for form in bad_forms:
                response = self.post(form)
                self.assertEqual(response.status_code, 302, form)
        self.assertEqual((PaymentOrder.query.count(), create.call_count), (0, 0))

    def test_bad_csrf_or_missing_keys_create_nothing(self):
        with self.fake_link() as create:
            self.client.post("/profile/topup", data={"csrf_token": "sai", "package": "50000"})
            self.client.post("/profile/topup", data={"package": "50000"})
            with mock.patch.object(Config, "PAYOS_API_KEY", ""):
                self.post({"package": "50000"})
        self.assertEqual((PaymentOrder.query.count(), create.call_count), (0, 0))

    def test_payos_error_marks_the_order_failed_and_shows_a_friendly_message(self):
        with mock.patch("core.payos_client.create_payment_link", side_effect=payos.PayOSError("payOS trả HTTP 401: Sai khóa")):
            response = self.post({"package": "50000"}, )
            page = self.client.get("/profile").get_data(as_text=True)
        self.assertEqual(response.status_code, 302)
        order = PaymentOrder.query.one()
        self.assertEqual(order.status, "failed")
        self.assertIn("Sai khóa", order.note)
        self.assertIn("Không tạo được liên kết thanh toán", page)
        self.assertNotIn("Sai khóa", page, "lỗi kỹ thuật không lộ ra giao diện khách")

    def test_only_owner_or_admin_may_top_up(self):
        member = User(email="thanhvien@example.com", password_hash="x", full_name="Thành viên")
        self.db.session.add(member)
        self.db.session.flush()
        self.db.session.add(TeamMember(team_id=self.team.id, user_id=member.id, role="Member"))
        self.db.session.commit()
        client = self.login(member)
        with self.fake_link() as create:
            self.assertEqual(self.post({"package": "50000"}, client).status_code, 403)
        self.assertEqual((PaymentOrder.query.count(), create.call_count), (0, 0))
        page = client.get("/profile").get_data(as_text=True)
        self.assertIn("Chỉ chủ nhóm hoặc quản trị viên", page)
        self.assertNotIn('id="topup-form"', page)

    def test_anonymous_is_redirected_to_login(self):
        response = self.app.test_client().post("/profile/topup", data={"csrf_token": "tok", "package": "50000"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class Webhook(PaymentCase):
    def test_a_valid_paid_webhook_credits_exactly_once(self):
        order = self.order(200_000)
        before = self.balance()
        first = self.webhook(self.signed(order.order_code, 200_000))
        self.assertEqual((first.status_code, first.get_json()["result"]), (200, "credited"))
        self.assertEqual(self.balance(), before + 200_000)
        (row,) = self.topups()
        self.assertEqual((row.amount_vnd, row.balance_after_vnd, row.execution_id), (Decimal(200_000), before + 200_000, None))
        self.assertIn(str(order.order_code), row.note)
        self.db.session.expire_all()
        paid = PaymentOrder.query.one()
        self.assertEqual(paid.status, "paid")
        self.assertIsNotNone(paid.paid_at)
        for _ in range(3):  # payOS gửi lặp
            again = self.webhook(self.signed(order.order_code, 200_000))
            self.assertEqual((again.status_code, again.get_json()["result"]), (200, "ignored"))
        self.assertEqual(len(self.topups()), 1)
        self.assertEqual(self.balance(), before + 200_000)
        self.assert_ledger_matches_balance()

    def test_invalid_signature_is_rejected_and_credits_nothing(self):
        order = self.order(50_000)
        before = self.balance()
        for payload in (self.signed(order.order_code, 50_000, tamper=True), {"code": "00", "data": {"orderCode": order.order_code, "amount": 50_000}}, {}, None):
            response = self.webhook(payload)
            self.assertEqual(response.status_code, 400)
        response = self.app.test_client().post("/payos/webhook", data="không phải json", content_type="text/plain")
        self.assertEqual(response.status_code, 400)
        self.assertEqual((self.balance(), len(self.topups()), PaymentOrder.query.one().status), (before, 0, "pending"))

    def test_amount_mismatch_is_not_credited_and_is_recorded(self):
        order = self.order(200_000)
        before = self.balance()
        response = self.webhook(self.signed(order.order_code, 50_000))
        self.assertEqual((response.status_code, response.get_json()["result"]), (200, "ignored"))
        self.db.session.expire_all()
        row = PaymentOrder.query.one()
        self.assertEqual((row.status, self.balance()), ("pending", before))
        self.assertIn("lệch", row.note)

    def test_unknown_order_and_unsuccessful_codes_are_acknowledged_without_effect(self):
        before = self.balance()
        test_webhook = self.webhook(self.signed(123, 3000))  # webhook thử khi đăng ký URL (orderCode giả)
        failed = self.webhook(self.signed(self.order(50_000).order_code, 50_000, code="01"))
        self.assertEqual([test_webhook.status_code, failed.status_code], [200, 200])
        self.assertEqual((self.balance(), len(self.topups())), (before, 0))

    def test_a_late_payment_on_an_expired_order_still_credits(self):
        order = self.order(50_000, status="expired")
        self.webhook(self.signed(order.order_code, 50_000))
        self.assertEqual(len(self.topups()), 1, "tiền đã thu thì khách phải nhận Credit")

    def test_credit_from_topup_is_usable_by_the_agent_afterwards(self):
        self.set_balance(Decimal("0"))
        order = self.order(50_000)
        self.webhook(self.signed(order.order_code, 50_000))
        _, message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "answer")


class ReturnPage(PaymentCase):
    def visit(self, order, client=None, **query):
        return (client or self.client).get("/profile/topup/return", query_string={"orderCode": order.order_code, **query})

    def test_paid_at_payos_credits_once_even_if_the_webhook_also_arrives(self):
        order = self.order(500_000)
        before = self.balance()
        with mock.patch("core.payos_client.get_payment", return_value={"status": "PAID", "amount": 500000, "amountPaid": 500000}) as get:
            response = self.visit(order)
            self.visit(order)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(get.call_count, 1, "đơn đã 'paid' thì không hỏi lại payOS")
        self.webhook(self.signed(order.order_code, 500_000))
        self.assertEqual((len(self.topups()), self.balance()), (1, before + 500_000))
        self.assertIn("Thanh toán thành công", self.client.get(response.headers["Location"]).get_data(as_text=True))

    def test_the_query_string_is_never_trusted(self):
        order = self.order(50_000)
        before = self.balance()
        with mock.patch("core.payos_client.get_payment", return_value={"status": "PENDING", "amount": 50000}):
            self.visit(order, status="PAID", code="00", cancel="false")
        self.assertEqual((len(self.topups()), self.balance(), PaymentOrder.query.one().status), (0, before, "pending"))

    def test_cancelled_or_expired_at_payos_closes_the_order_without_credit(self):
        for remote, local in (("CANCELLED", "cancelled"), ("EXPIRED", "expired")):
            order = self.order(50_000)
            with mock.patch("core.payos_client.get_payment", return_value={"status": remote}):
                self.visit(order)
            self.db.session.expire_all()
            self.assertEqual(PaymentOrder.query.filter_by(order_code=order.order_code).one().status, local)
        self.assertEqual(len(self.topups()), 0)

    def test_payos_error_leaves_the_order_pending_and_says_so(self):
        order = self.order(50_000)
        with mock.patch("core.payos_client.get_payment", side_effect=payos.PayOSError("mạng")):
            response = self.visit(order)
            page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertEqual(PaymentOrder.query.one().status, "pending")
        self.assertIn("Chưa xác nhận được kết quả thanh toán", page)

    def test_other_teams_or_unknown_orders_are_ignored(self):
        other_team = self.make_team("Team B")
        foreign = self.order(50_000, team=other_team, code=777000111222)
        with mock.patch("core.payos_client.get_payment", return_value={"status": "PAID", "amount": 50000}) as get:
            self.visit(foreign)
            self.client.get("/profile/topup/return", query_string={"orderCode": 1})
            self.client.get("/profile/topup/return")
            self.client.get("/profile/topup/return", query_string={"orderCode": "abc"})
        self.assertEqual(get.call_count, 0)
        self.assertEqual(PaymentOrder.query.filter_by(order_code=777000111222).one().status, "pending")

    def test_anonymous_is_redirected(self):
        order = self.order(50_000)
        response = self.app.test_client().get("/profile/topup/return", query_string={"orderCode": order.order_code})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class ProfileTopupUi(PaymentCase):
    def test_shows_the_four_price_cards_with_the_popular_tag_and_an_enabled_button(self):
        html = self.client.get("/profile").get_data(as_text=True)
        for text in ("50.000đ", "200.000đ", "500.000đ", "1.000.000đ+", "PHỔ BIẾN", "Doanh nghiệp / Custom", "Nạp Credit"):
            self.assertIn(text, html, text)
        self.assertRegex(html, r'name="package" value="200000" checked')
        self.assertRegex(html, r'<button class="primary" type="submit" >Nạp Credit</button>')
        self.assertIn('name="custom_amount"', html)
        self.assertNotIn("Sắp có", html)

    def test_button_is_disabled_and_explained_when_payos_is_not_configured(self):
        with mock.patch.object(Config, "PAYOS_CHECKSUM_KEY", ""):
            html = self.client.get("/profile").get_data(as_text=True)
        self.assertRegex(html, r'<button class="primary" type="submit" disabled[^>]*>Nạp Credit</button>')
        self.assertIn("Chưa mở thanh toán", html)

    def test_history_shows_paid_topups_and_orders_but_no_keys_or_internal_ids(self):
        order = self.order(200_000)
        self.webhook(self.signed(order.order_code, 200_000))
        self.order(50_000, status="cancelled")
        html = self.client.get("/profile").get_data(as_text=True)
        self.assertIn("+200.000đ", html)
        self.assertIn("Nạp Credit", html)
        self.assertIn("Đã thanh toán", html)
        self.assertIn("Đã hủy", html)
        for secret in (KEY, "akey", "plid", str(order.order_code)):
            self.assertNotIn(secret, html, "không lộ khóa/mã nội bộ")
        self.assertIn("210.000đ", html)  # 10.000 dùng thử + 200.000 vừa nạp


if __name__ == "__main__":
    unittest.main()
