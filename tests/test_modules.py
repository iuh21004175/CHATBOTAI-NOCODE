"""Website Action Engine (Phase M) — phần có DB: dữ liệu seed loại module, khai báo module bằng TẢI LÊN file .zip (validate từ DB, không hard-code),
xác nhận domain (bắt buộc trước khi module 'ready'), duyệt hành động + ngưỡng + công tắc thanh toán, danh sách tool cho agent, quy trình phân tích
(giải nén .zip giả lập + LLM giả) + trừ Credit (M5), route/quyền. Đây là kiểm tra hồi quy ở mức mã — KHÔNG thay cho kiểm thử chức năng thực tế."""
import io
import json
import unittest
from decimal import Decimal
from unittest import mock

from werkzeug.datastructures import FileStorage

from app.credits import service as credits
from app.models import (
    BotModule, CreditAccount, CreditTransaction, ModuleAction, ModuleType, ModuleTypeUrlRole, ModuleUrl, TeamMember, User,
)
from app.modules import runner, service
from config import Config
from core.context_engine.structured import LLMReply
from tests.db_case import DbCase
from tests.test_website_actions import ADD_CART, PRODUCT_HTML, make_zip, reply

D = Decimal
PRODUCT_DOMAIN = "shop.vn"
# PRODUCT_HTML (tests/test_website_actions.py) không có <base>/<link>/<meta> — dùng để kiểm domain KHÔNG đoán được. Bản có <base> dùng làm mặc định
# cho phần lớn test ở đây vì đa số test không quan tâm tới cơ chế đoán domain, chỉ cần module có domain hợp lệ để xác nhận/duyệt được.
PRODUCT_HTML_WITH_BASE = PRODUCT_HTML.replace("<head>", f'<head><base href="https://{PRODUCT_DOMAIN}/">', 1)


def zip_of(html=PRODUCT_HTML_WITH_BASE) -> bytes:
    return make_zip({"index.html": html})


def file_of(html=PRODUCT_HTML_WITH_BASE) -> FileStorage:
    return FileStorage(stream=io.BytesIO(zip_of(html)), filename="page.zip")


def zip_field(html=PRODUCT_HTML_WITH_BASE):
    """Giá trị cho werkzeug test client (`data={...}`) mô phỏng 1 ô <input type=file> trong form thật."""
    return (io.BytesIO(zip_of(html)), "page.zip")


class ModulesCase(DbCase):
    def setUp(self):
        super().setUp()
        self._blobs = {}
        for patcher in (
            mock.patch("app.modules.events.emit_module_status", lambda *a, **k: None),
            # MinIO thật không có trong môi trường test (xem tests/README.md) — giả lập bằng dict trong RAM, đúng hợp đồng của storage_service
            # (save trả về key; get_file trả về đối tượng có .read()/.close()/.release_conn()) để service.py/runner.py không cần biết đang giả.
            mock.patch("core.storage_service.save_module_zip", self._fake_save_zip),
            mock.patch("core.storage_service.get_file", self._fake_get_file),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.restore_types()
        self.type = ModuleType.query.filter_by(key="sales_support").one()

    def _fake_save_zip(self, team_id, bot_id, module_id, url_id, raw):
        key = f"{team_id}/{bot_id}/modules/{module_id}/url-{url_id}.zip"
        self._blobs[key] = raw
        return key

    def _fake_get_file(self, key):
        raw = self._blobs[key]

        class _Obj:
            def read(_self):
                return raw

            def close(_self):
                pass

            def release_conn(_self):
                pass

        return _Obj()

    def restore_types(self):
        """module_types là dữ liệu cấu hình (không nằm trong danh sách xoá của DbCase): đưa về đúng trạng thái seed sau mỗi test để test này không làm hỏng test khác.
        Không xoá loại sales_support do migration chèn (test SeedData kiểm chứng đúng dòng đó)."""
        service.ensure_module_types()
        seed = service.MODULE_TYPE_SEEDS[0]
        canonical = ModuleType.query.filter_by(key=seed["key"]).one()
        canonical.name, canonical.description, canonical.is_active = seed["name"], seed["description"], True
        wanted = {role: (label, required, order) for order, (role, label, required) in enumerate(seed["roles"], start=1)}
        for role in list(canonical.url_roles):
            if role.url_role not in wanted:
                self.db.session.delete(role)
        for role in canonical.url_roles:
            if role.url_role in wanted:
                role.label, role.is_required, role.display_order = wanted[role.url_role]
        for extra in ModuleType.query.filter(ModuleType.key != seed["key"]).all():
            self.db.session.delete(extra)
        self.db.session.commit()

    def make_module(self, bot=None, html_by_role=None, status="pending", confirm_domains=True, domain=PRODUCT_DOMAIN):
        """Tạo module qua ĐÚNG đường dẫn sản xuất (service.create_module, file .zip thật). confirm_domains=True (mặc định) mô phỏng module đã
        được worker xử lý xong VÀ chủ bot đã xác nhận domain — tiện cho các test không quan tâm tới cơ chế tải lên/xác nhận. Test nào cần trạng thái
        'vừa tạo, chưa xử lý' (vd Analyze) phải truyền confirm_domains=False."""
        bot = bot or self.bot
        files = {role: file_of(html) for role, html in (html_by_role or {"product_detail": PRODUCT_HTML_WITH_BASE}).items()}
        module, error = service.create_module(bot, self.type.id, "Cửa hàng", files)
        self.assertIsNone(error)
        if confirm_domains:
            for url in module.urls:
                url.upload_status = "extracted"
                self.assertIsNone(service.confirm_domain(url, domain))
        module.status = status
        self.db.session.commit()
        return module

    def make_action(self, module=None, *, verified=False, risk="read_only", confidence=0.9, action_type="read_info", spec=None, name="doc_gia", url=None):
        module = module or self.make_module()
        url = url or module.urls[0]
        action = ModuleAction(
            module_id=module.id, url_id=url.id, action_type=action_type, action_name=name, description=f"mô tả {name}",
            selector_spec=spec or {"selector": "#price", "attribute": "text"}, confidence=confidence, risk_level=risk, verified=verified,
        )
        self.db.session.add(action)
        self.db.session.commit()
        return action


class SeedData(ModulesCase):
    def test_migration_seeded_the_sales_support_type_with_the_agreed_url_roles(self):
        # KHÔNG gọi ensure_module_types trước: kiểm chứng dữ liệu do chính migration 1a2b3c4d5e10 chèn (setUp gọi ensure nhưng không ghi đè loại đã có)
        roles = {r.url_role: (r.label, r.is_required, r.display_order) for r in self.type.url_roles}
        self.assertEqual(self.type.name, "Hỗ trợ bán hàng")
        self.assertTrue(self.type.is_active)
        self.assertEqual(roles["product_detail"][:2], ("URL trang sản phẩm", True))
        for optional in ("product_listing", "cart", "checkout"):
            self.assertFalse(roles[optional][1], optional)
        seeded = {(r[0], r[1], r[2]) for r in service.MODULE_TYPE_SEEDS[0]["roles"]}
        self.assertEqual({(role, label, req) for role, (label, req, _) in roles.items()}, seeded, "migration và service.MODULE_TYPE_SEEDS phải khớp nhau")

    def test_ensure_module_types_is_idempotent_and_creates_a_missing_type_without_touching_existing_ones(self):
        service.ensure_module_types()
        service.ensure_module_types()
        self.assertEqual(ModuleType.query.filter_by(key="sales_support").count(), 1)
        self.type.description = "mô tả do chủ hệ thống chỉnh"
        self.db.session.commit()
        extra = {"key": "extra_type", "name": "Loại thêm", "description": "d", "roles": (("other", "URL khác", True),)}
        with mock.patch.object(service, "MODULE_TYPE_SEEDS", service.MODULE_TYPE_SEEDS + (extra,)):
            service.ensure_module_types()
            service.ensure_module_types()
        created = ModuleType.query.filter_by(key="extra_type").one()
        self.assertEqual([(r.url_role, r.is_required) for r in created.url_roles], [("other", True)])
        self.assertEqual(self.type.description, "mô tả do chủ hệ thống chỉnh", "loại đã có không bị ghi đè")

    def test_only_active_types_are_offered_and_the_client_payload_carries_the_roles(self):
        self.db.session.add(ModuleType(key="hidden", name="Ẩn", is_active=False))
        self.db.session.commit()
        types = service.active_types()
        self.assertNotIn("hidden", [t.key for t in types])
        payload = service.types_for_client(types)
        sales = next(t for t in payload if t["name"] == "Hỗ trợ bán hàng")
        self.assertEqual([r["role"] for r in sales["roles"]], ["product_detail", "product_listing", "cart", "checkout", "other"])
        self.assertTrue(sales["roles"][0]["required"])


class CreateModule(ModulesCase):
    def test_creates_pending_module_with_uploaded_zip_files(self):
        files = {"product_detail": file_of(), "cart": file_of()}
        module, error = service.create_module(self.bot, self.type.id, "  Shop chính  ", files)
        self.assertIsNone(error)
        self.assertEqual((module.status, module.module_type_id, module.name, module.bot_id, module.progress_total), ("pending", self.type.id, "Shop chính", self.bot.id, 2))
        self.assertEqual({u.url_role for u in module.urls}, {"product_detail", "cart"})
        for u in module.urls:
            self.assertEqual(u.upload_status, "uploaded")
            self.assertIsNotNone(u.upload_storage_key)
            self.assertIsNone(u.source_url)
            self.assertIsNone(u.entry_html_filename, "worker mới xác định file chính, không phải lúc tải lên")

    def test_required_file_is_enforced_from_the_database_roles_not_hard_coded(self):
        module, error = service.create_module(self.bot, self.type.id, "x", {"cart": file_of()})
        self.assertIsNone(module)
        self.assertIn("URL trang sản phẩm", error)
        # đổi dữ liệu: bắt buộc chuyển sang giỏ hàng -> validate đổi theo, không cần sửa code
        for role in self.type.url_roles:
            role.is_required = role.url_role == "cart"
        self.db.session.commit()
        self.assertIsNone(service.create_module(self.bot, self.type.id, "x", {"product_detail": file_of()})[0])
        self.assertIsNotNone(service.create_module(self.bot, self.type.id, "x", {"cart": file_of()})[0])

    def test_a_second_type_with_different_required_roles_is_validated_on_its_own_roles(self):
        other = ModuleType(key="support_x", name="Loại khác", is_active=True)
        other.url_roles.append(ModuleTypeUrlRole(url_role="other", label="URL trang hỗ trợ", is_required=True, display_order=1))
        self.db.session.add(other)
        self.db.session.commit()
        module, error = service.create_module(self.bot, other.id, "", {"product_detail": file_of()})
        self.assertIsNone(module)
        self.assertIn("URL trang hỗ trợ", error)
        module, error = service.create_module(self.bot, other.id, "", {"other": file_of(), "product_detail": file_of()})
        self.assertIsNone(error)
        self.assertEqual([u.url_role for u in module.urls], ["other"], "ô không thuộc loại này bị bỏ qua")
        self.assertEqual(module.name, "Loại khác", "không nhập tên -> lấy tên loại")

    def test_invalid_zip_uploads_and_type_errors_are_rejected_with_a_reason(self):
        bad = {
            "not a zip": FileStorage(stream=io.BytesIO(b"khong phai file zip"), filename="x.zip"),
            "no root html": FileStorage(stream=io.BytesIO(make_zip({"style.css": "x"})), filename="x.zip"),
            "two root html": FileStorage(stream=io.BytesIO(make_zip({"a.html": "a", "b.html": "b"})), filename="x.zip"),
            "html only in a subfolder": FileStorage(stream=io.BytesIO(make_zip({"pages/index.html": "x"})), filename="x.zip"),
            "path traversal": FileStorage(stream=io.BytesIO(make_zip({"index.html": PRODUCT_HTML_WITH_BASE, "../evil.html": "x"})), filename="x.zip"),
            "empty file": FileStorage(stream=io.BytesIO(b""), filename="x.zip"),
        }
        for label, file in bad.items():
            with self.subTest(label=label):
                module, error = service.create_module(self.bot, self.type.id, "x", {"product_detail": file})
                self.assertIsNone(module)
                self.assertTrue(error)
        for type_id in (None, "1", 0, 999999):
            self.assertIn("chọn loại", service.create_module(self.bot, type_id, "x", {"product_detail": file_of()})[1])
        self.type.is_active = False
        self.db.session.commit()
        self.assertIn("chọn loại", service.create_module(self.bot, self.type.id, "x", {"product_detail": file_of()})[1])
        self.assertEqual(BotModule.query.count(), 0, "khai báo lỗi không để lại module dở")

    def test_oversized_file_is_rejected(self):
        with mock.patch.object(Config, "MODULE_ZIP_MAX_BYTES", 10):
            module, error = service.create_module(self.bot, self.type.id, "x", {"product_detail": file_of()})
        self.assertIsNone(module)
        self.assertIn("quá lớn", error)

    def test_url_count_is_capped(self):
        with mock.patch.object(Config, "MODULE_MAX_URLS", 1):
            module, error = service.create_module(self.bot, self.type.id, "x", {"product_detail": file_of(), "cart": file_of()})
        self.assertIsNone(module)
        self.assertIn("Tối đa 1", error)

    def test_modules_are_scoped_to_their_bot(self):
        other_bot = self.service.create_bot(self.team.id, "Bot B")
        module = self.make_module()
        self.assertIsNotNone(service.get_module(self.bot, module.id))
        self.assertIsNone(service.get_module(other_bot, module.id))
        self.assertEqual(service.list_modules(other_bot), [])

    def test_request_analysis_and_delete_rules(self):
        module = self.make_module(status="ready")
        module.error_message, module.analysis_note, module.progress_done = "cũ", "ghi chú cũ", 1
        self.assertIsNone(service.request_analysis(module))
        self.assertEqual((module.status, module.error_message, module.analysis_note, module.progress_done), ("pending", None, None, 0))
        self.assertEqual(module.urls[0].upload_status, "uploaded", "phân tích lại -> url về lại 'uploaded' để worker xử lý")
        self.assertIsNone(module.urls[0].source_url, "phải xác nhận domain lại sau mỗi lần phân tích lại")
        module.status = "analyzing"
        self.db.session.commit()
        self.assertIn("đang được phân tích", service.request_analysis(module))
        self.assertIn("đang được phân tích", service.delete_module(module))
        module.status = "failed"
        self.db.session.commit()
        action = self.make_action(module)
        self.assertIsNone(service.delete_module(module))
        self.assertEqual((BotModule.query.count(), ModuleUrl.query.count(), ModuleAction.query.count()), (0, 0, 0), action.id and "xoá kéo theo URL + hành động")

    def test_domain_warning_when_the_site_is_not_a_declared_embed_domain(self):
        module = self.make_module()
        self.assertIn("shop.vn", service.domain_warning(self.bot, module))
        from app.models import BotDomain

        self.db.session.add(BotDomain(bot_id=self.bot.id, domain="shop.vn"))
        self.db.session.commit()
        self.db.session.refresh(self.bot)
        self.assertIsNone(service.domain_warning(self.bot, module))


class DomainConfirmation(ModulesCase):
    def test_confirming_requires_the_url_to_have_finished_extraction(self):
        module = self.make_module(confirm_domains=False)
        url = module.urls[0]
        self.assertEqual(url.upload_status, "uploaded")
        self.assertIn("chưa phân tích xong", service.confirm_domain(url, "shop.vn"))
        self.assertNotEqual(url.upload_status, "domain_confirmed")

    def test_empty_or_invalid_domain_is_rejected(self):
        module = self.make_module(confirm_domains=False)
        url = module.urls[0]
        url.upload_status = "extracted"
        self.db.session.commit()
        for bad in ("", "   ", "not a domain!!", "shop_vn", "a" * 300):
            with self.subTest(bad=bad):
                self.assertIsNotNone(service.confirm_domain(url, bad))
                self.assertNotEqual(url.upload_status, "domain_confirmed")

    def test_confirming_normalizes_and_stores_the_domain(self):
        module = self.make_module(confirm_domains=False)
        url = module.urls[0]
        url.upload_status = "extracted"
        self.db.session.commit()
        self.assertIsNone(service.confirm_domain(url, "https://www.Shop.vn/abc"))
        self.assertEqual((url.source_url, url.upload_status), ("shop.vn", "domain_confirmed"))

    def test_module_only_becomes_ready_once_every_required_url_is_confirmed(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML_WITH_BASE, "cart": PRODUCT_HTML_WITH_BASE}, confirm_domains=False)
        for url in module.urls:
            url.upload_status = "extracted"
        module.status = service.STATUS_AWAITING_DOMAIN
        self.db.session.commit()
        by_role = {u.url_role: u for u in module.urls}
        self.assertIsNone(service.confirm_domain(by_role["cart"], "shop.vn"))  # tuỳ chọn xác nhận trước
        self.db.session.refresh(module)
        self.assertEqual(module.status, service.STATUS_AWAITING_DOMAIN, "url bắt buộc (product_detail) chưa xác nhận -> chưa cho 'ready'")
        self.assertIsNone(service.confirm_domain(by_role["product_detail"], "shop.vn"))
        self.db.session.refresh(module)
        self.assertEqual(module.status, service.STATUS_READY)

    def test_optional_role_that_was_never_uploaded_does_not_block_ready(self):
        module = self.make_module(confirm_domains=False)  # chỉ có product_detail (bắt buộc); cart/checkout bỏ trống hoàn toàn
        module.urls[0].upload_status = "extracted"
        module.status = service.STATUS_AWAITING_DOMAIN
        self.db.session.commit()
        self.assertIsNone(service.confirm_domain(module.urls[0], "shop.vn"))
        self.db.session.refresh(module)
        self.assertEqual(module.status, service.STATUS_READY)

    def test_recompute_never_touches_a_module_still_pending_or_analyzing_or_failed(self):
        for status in ("pending", "analyzing", "failed", "ready"):
            module = self.make_module(confirm_domains=False, status=status)
            module.urls[0].upload_status = "extracted"
            self.db.session.commit()
            self.assertIsNone(service.confirm_domain(module.urls[0], "shop.vn"))
            self.db.session.refresh(module)
            self.assertEqual(module.status, status, status)


class ApprovalAndThresholds(ModulesCase):
    def approve(self, action):
        return service.approve_action(self.bot, action)

    def test_verified_is_never_automatic(self):
        action = self.make_action(confidence=1.0)
        self.assertFalse(action.verified, "confidence cao mấy cũng phải chủ bot bấm duyệt")

    def test_read_only_needs_at_least_60_percent(self):
        low, ok = self.make_action(confidence=0.59, name="low"), self.make_action(confidence=0.6, name="ok")
        self.assertIn("ngưỡng", self.approve(low))
        self.assertFalse(low.verified)
        self.assertIsNone(self.approve(ok))
        self.assertTrue(ok.verified)

    def test_cart_needs_at_least_80_percent(self):
        spec = {"selector": "#add-btn", "event": "click"}
        low = self.make_action(action_type="add_to_cart", risk="cart", confidence=0.79, spec=spec, name="low")
        ok = self.make_action(action_type="add_to_cart", risk="cart", confidence=0.8, spec=spec, name="ok")
        self.assertIsNotNone(self.approve(low))
        self.assertIsNone(self.approve(ok))

    def test_a_lowered_stored_risk_does_not_lower_the_bar(self):
        # DB bị sửa tay để hạ mức xuống read_only, nhưng add_to_cart luôn là ít nhất 'cart' (sàn cứng tính lại lúc duyệt)
        action = self.make_action(action_type="add_to_cart", risk="read_only", confidence=0.7, spec={"selector": "#add-btn", "event": "click"})
        self.assertEqual(service.effective_risk_of(action), "cart")
        self.assertIsNotNone(self.approve(action))
        action.confidence = 0.85
        self.db.session.commit()
        self.assertIsNone(self.approve(action))
        self.assertEqual(action.risk_level, "cart", "mức hiệu lực được ghi lại")

    def test_payment_actions_cannot_be_approved_while_the_switch_is_off_even_at_full_confidence(self):
        settings = self.service.get_or_create_settings(self.bot)
        self.assertFalse(settings.allow_agent_payment_actions, "mặc định TẮT")
        action = self.make_action(risk="payment", confidence=1.0, action_type="click", spec={"selector": "#pay-btn", "event": "click"}, name="pay")
        self.assertIn("công tắc", self.approve(action))
        self.assertFalse(action.verified)

    def test_payment_actions_need_the_switch_and_a_higher_confidence(self):
        service.set_payment_switch(self.bot, True)
        spec = {"selector": "#pay-btn", "event": "click"}
        weak = self.make_action(risk="payment", confidence=0.85, action_type="click", spec=spec, name="weak")
        strong = self.make_action(risk="payment", confidence=0.95, action_type="click", spec=spec, name="strong")
        self.assertIsNotNone(self.approve(weak))
        self.assertIsNone(self.approve(strong))

    def test_turning_the_switch_off_unapproves_payment_actions_only(self):
        service.set_payment_switch(self.bot, True)
        pay = self.make_action(risk="payment", confidence=0.95, action_type="click", spec={"selector": "#pay-btn", "event": "click"}, name="pay")
        read = self.make_action(confidence=0.9, name="read")
        self.assertIsNone(self.approve(pay))
        self.assertIsNone(self.approve(read))
        service.set_payment_switch(self.bot, False)
        self.db.session.refresh(pay), self.db.session.refresh(read)
        self.assertFalse(pay.verified)
        self.assertTrue(read.verified)
        self.assertFalse(service.payment_allowed(self.bot.id))

    def test_approving_clears_a_previous_failure_and_unapprove_works(self):
        action = self.make_action(confidence=0.9)
        service.mark_action_failed(action, "element_not_found")
        self.db.session.commit()
        self.assertFalse(action.verified)
        self.assertEqual(action.failure_reason, "element_not_found")
        self.assertIsNone(self.approve(action))
        self.assertIsNone(action.failure_reason)
        service.unapprove_action(action)
        self.assertFalse(action.verified)

    def test_missing_or_invalid_confidence_is_never_approvable(self):
        for confidence in (None, 0.0, -1, 0.3):
            action = self.make_action(confidence=confidence, name=f"c{confidence}")
            self.assertIsNotNone(self.approve(action), confidence)

    def test_an_action_on_an_unconfirmed_url_cannot_be_approved(self):
        module = self.make_module(confirm_domains=False)
        action = self.make_action(module, confidence=0.99)
        self.assertIn("xác nhận domain", self.approve(action))
        self.assertFalse(action.verified)
        module.urls[0].upload_status = "extracted"
        self.db.session.commit()
        self.assertIsNone(service.confirm_domain(module.urls[0], "shop.vn"))
        self.assertIsNone(self.approve(action), "sau khi xác nhận domain thì duyệt được bình thường")


class AgentToolManifest(ModulesCase):
    def test_bot_without_modules_gets_no_tools(self):
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), [])

    def test_only_verified_actions_become_tools_with_description_and_params(self):
        module = self.make_module()
        self.make_action(module, verified=True, name="doc_gia")
        self.make_action(module, verified=False, name="chua_duyet")
        fill = self.make_action(module, verified=True, action_type="fill_form", name="dien_thong_tin", spec={
            "form_selector": "#contact-form", "fields": [{"selector": "#fullname", "value_from_slot": "ho_ten"}, {"selector": "#phone", "value_from_slot": "so_dien_thoai"}], "submit": False})
        tools = service.agent_tools_for_bot(self.bot.id)
        self.assertEqual([t["name"] for t in tools], ["doc_gia", "dien_thong_tin"])
        by_name = {t["name"]: t for t in tools}
        self.assertEqual(by_name["doc_gia"]["description"], "mô tả doc_gia")
        self.assertEqual(by_name["dien_thong_tin"]["params"], [{"name": "ho_ten"}, {"name": "so_dien_thoai"}])
        self.assertFalse(by_name["dien_thong_tin"]["confirm"])
        self.assertEqual(fill.id, service.resolve_tool(self.bot.id, "dien_thong_tin").id)
        self.assertIsNone(service.resolve_tool(self.bot.id, "chua_duyet"))

    def test_other_bots_actions_are_never_listed_or_resolved(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        module_b = self.make_module(bot=other)
        self.make_action(module_b, verified=True, name="cua_bot_b")
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), [])
        self.assertIsNone(service.resolve_tool(self.bot.id, "cua_bot_b"))
        self.assertEqual([t["name"] for t in service.agent_tools_for_bot(other.id)], ["cua_bot_b"])

    def test_payment_tools_appear_only_with_the_switch_on_and_always_require_customer_confirmation(self):
        module = self.make_module()
        pay = self.make_action(module, verified=True, risk="payment", action_type="click", spec={"selector": "#pay-btn", "event": "click"}, name="dat_hang")
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), [], "verified=1 trong DB nhưng công tắc tắt -> không giao cho agent")
        self.assertIsNone(service.resolve_tool(self.bot.id, "dat_hang"))
        self.service.get_or_create_settings(self.bot).allow_agent_payment_actions = True
        self.db.session.commit()
        tools = service.agent_tools_for_bot(self.bot.id)
        self.assertEqual([(t["name"], t["confirm"]) for t in tools], [("dat_hang", True)])
        self.assertEqual(service.resolve_tool(self.bot.id, "dat_hang").id, pay.id)

    def test_a_payment_action_whose_stored_risk_was_lowered_is_still_treated_as_payment(self):
        self.make_action(verified=True, risk="read_only", action_type="click", spec={"selector": "#pay-btn", "event": "click"}, name="tro_thanh_toan", url=None)
        module = BotModule.query.first()
        module.urls[0].url_role = "checkout"
        self.db.session.commit()
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), [], "trang thanh toán -> sàn 'payment' -> cần công tắc")

    def test_name_clashes_with_system_tools_and_between_modules_get_stable_suffixes(self):
        first = self.make_module()
        second = self.make_module()
        a = self.make_action(first, verified=True, name="decline")
        b = self.make_action(second, verified=True, name="doc_gia")
        c = self.make_action(first, verified=True, name="doc_gia")
        names = [t["name"] for t in service.agent_tools_for_bot(self.bot.id)]
        self.assertEqual(names, [f"decline_{a.id}", "doc_gia", f"doc_gia_{c.id}"])
        self.assertEqual(len(set(names)), 3)
        self.assertEqual(service.resolve_tool(self.bot.id, "doc_gia").id, b.id)
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), service.agent_tools_for_bot(self.bot.id), "ổn định giữa các lần gọi (process_key không đổi vô cớ)")

    def test_actions_on_an_unconfirmed_url_are_never_usable_even_if_verified_in_the_db(self):
        module = self.make_module(confirm_domains=False)
        self.make_action(module, verified=True, name="doc_gia")
        self.assertEqual(service.agent_tools_for_bot(self.bot.id), [])
        self.assertIsNone(service.resolve_tool(self.bot.id, "doc_gia"))


class SavePageActions(ModulesCase):
    def candidate(self, name="them_gio", **overrides):
        from core.website_actions.analysis import Candidate

        base = dict(action_type="click", action_name=name, description="mô tả", selector_spec={"selector": "#add-btn", "event": "click"}, confidence=0.9,
                    risk_level="read_only", ai_risk_level="read_only")
        base.update(overrides)
        return Candidate(**base)

    def test_new_actions_are_saved_unverified(self):
        module = self.make_module()
        created, updated, removed = service.save_page_actions(module, module.urls[0], [self.candidate("a"), self.candidate("b")])
        self.db.session.commit()
        self.assertEqual((created, updated, removed), (2, 0, 0))
        self.assertEqual([(a.action_name, a.verified) for a in module.actions], [("a", False), ("b", False)])

    def test_reanalysis_keeps_an_approval_only_if_nothing_changed(self):
        module = self.make_module()
        service.save_page_actions(module, module.urls[0], [self.candidate("giu"), self.candidate("doi_selector"), self.candidate("doi_rui_ro"), self.candidate("bien_mat")])
        self.db.session.commit()
        for action in module.actions:
            action.verified = True
        self.db.session.commit()
        created, updated, removed = service.save_page_actions(module, module.urls[0], [
            self.candidate("giu"), self.candidate("doi_selector", selector_spec={"selector": "#khac", "event": "click"}), self.candidate("doi_rui_ro", risk_level="cart"),
            self.candidate("moi"),
        ])
        self.db.session.commit()
        self.assertEqual((created, updated, removed), (1, 3, 1))
        state = {a.action_name: a.verified for a in module.actions}
        self.assertEqual(state, {"giu": True, "doi_selector": False, "doi_rui_ro": False, "moi": False})

    def test_reanalysis_clears_the_failure_flag_but_needs_a_new_approval(self):
        module = self.make_module()
        service.save_page_actions(module, module.urls[0], [self.candidate("a")])
        self.db.session.commit()
        action = module.actions[0]
        action.verified = True
        service.mark_action_failed(action, "element_not_found")
        self.db.session.commit()
        service.save_page_actions(module, module.urls[0], [self.candidate("a")])
        self.db.session.commit()
        self.assertEqual((action.failure_reason, action.verified), (None, False))

    def test_same_name_from_another_page_of_the_module_gets_a_suffix_instead_of_clobbering(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML_WITH_BASE, "cart": PRODUCT_HTML_WITH_BASE})
        first, second = module.urls
        service.save_page_actions(module, first, [self.candidate("mo_ta")])
        service.save_page_actions(module, second, [self.candidate("mo_ta")])
        self.db.session.commit()
        self.assertEqual(sorted(a.action_name for a in module.actions), ["mo_ta", "mo_ta_2"])
        self.assertEqual({a.url_id for a in module.actions}, {first.id, second.id})
        # phân tích lại trang thứ hai: chỉ đụng hành động của CHÍNH trang đó
        service.save_page_actions(module, second, [])
        self.db.session.commit()
        self.assertEqual([a.action_name for a in module.actions], ["mo_ta"])


class Analyze(ModulesCase):
    def get_zip_with_override(self, overrides: dict):
        """overrides: {url_role: Exception | bytes}. Vai trò không có trong overrides dùng đúng kho lưu trữ giả (mô phỏng MinIO thật)."""
        def get_zip(module_url):
            if module_url.url_role in overrides:
                result = overrides[module_url.url_role]
                if isinstance(result, Exception):
                    raise result
                return result
            return runner._get_zip(module_url)
        return get_zip

    def run_analysis(self, module, llm=None, get_zip=None, **kw):
        # Mô phỏng ĐÚNG những gì service.request_analysis làm trước khi worker nhận việc (xem app/modules/service.py) — runner.analyze_module chỉ
        # xử lý module_urls có upload_status='uploaded', nên gọi trực tiếp analyze_module nhiều lần (test "phân tích lại") phải tự đưa lại trạng
        # thái đó, đúng như route "Phân tích lại" thật sự làm.
        for url in module.urls:
            url.upload_status = "uploaded"
        module.status = "analyzing"
        self.db.session.commit()
        return runner.analyze_module(module.id, get_zip=get_zip, llm_call=llm or (lambda messages: reply([ADD_CART])), **kw)

    def balance(self):
        return credits.get_balance(self.team.id)

    def test_happy_path_saves_actions_html_progress_and_charges_credit(self):
        module = self.make_module(confirm_domains=False)
        before = self.balance()
        self.assertEqual(self.run_analysis(module), "awaiting_domain_confirmation")
        self.db.session.refresh(module)
        self.assertEqual((module.progress_done, module.progress_total, module.error_message), (1, 1, None))
        self.assertIsNotNone(module.last_analyzed_at)
        (action,) = module.actions
        self.assertEqual((action.action_name, action.action_type, action.verified, action.risk_level), ("them_gio_hang", "add_to_cart", False, "cart"))
        url = module.urls[0]
        self.assertEqual((url.entry_html_filename, url.upload_status, url.detected_domain), ("index.html", "extracted", "shop.vn"))
        self.assertIsNotNone(url.extracted_at)
        # M5: 1000 prompt + 200 output -> giá vốn thật -> Credit bị trừ đúng bằng giá bán; sổ cái vẫn khớp số dư
        row = CreditTransaction.query.filter_by(team_id=self.team.id, type="module_analysis_charge").one()
        self.assertLess(row.amount_vnd, 0)
        self.assertEqual(module.analysis_charged_vnd, -row.amount_vnd)
        self.assertEqual(self.balance(), before + row.amount_vnd)
        self.assertGreater(module.analysis_cost_vnd, 0)
        self.assertEqual(sum(t.amount_vnd for t in CreditTransaction.query.filter_by(team_id=self.team.id)), CreditAccount.query.filter_by(team_id=self.team.id).one().balance_vnd)
        self.assertEqual(CreditTransaction.query.filter(CreditTransaction.type.in_(("reserve", "release"))).count(), 0, "phân tích chạy nền: không giữ chỗ")

    def test_when_nothing_in_the_html_gives_a_domain_hint_detected_domain_is_left_empty(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML}, confirm_domains=False)  # bản KHÔNG có <base>/<link>/<meta>
        self.assertEqual(self.run_analysis(module), "awaiting_domain_confirmation")
        self.db.session.refresh(module)
        self.assertIsNone(module.urls[0].detected_domain)
        self.assertEqual(module.urls[0].upload_status, "extracted", "không đoán được domain vẫn không phải là lỗi")

    def test_charge_uses_the_markup_and_is_shown_to_the_customer_but_without_tokens(self):
        module = self.make_module(confirm_domains=False)
        with mock.patch.object(Config, "PLATFORM_MARKUP_MULTIPLIER", D("2.0")):
            self.run_analysis(module)
        self.db.session.refresh(module)
        self.assertEqual(module.analysis_charged_vnd, (module.analysis_cost_vnd * 2).quantize(D("0.0001")))
        self.assertIn(credits.visible_transactions(self.team.id)[0].type, ("module_analysis_charge",))

    def test_one_failing_url_is_recorded_and_the_others_still_succeed(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML_WITH_BASE, "cart": PRODUCT_HTML_WITH_BASE}, confirm_domains=False)
        status = self.run_analysis(module, get_zip=self.get_zip_with_override({"cart": b"khong phai file zip"}))
        self.assertEqual(status, "awaiting_domain_confirmation")
        self.db.session.refresh(module)
        by_role = {u.url_role: u for u in module.urls}
        self.assertIn("không phải file .zip hợp lệ", by_role["cart"].error_message)
        self.assertEqual(by_role["cart"].upload_status, "failed")
        self.assertIsNone(by_role["product_detail"].error_message)
        self.assertEqual(by_role["product_detail"].upload_status, "extracted")
        self.assertEqual((module.progress_done, module.progress_total, len(module.actions)), (2, 2, 1))
        self.assertIsNone(module.error_message)

    def test_every_url_failing_marks_the_module_failed_with_the_reasons_and_charges_nothing(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML_WITH_BASE, "cart": PRODUCT_HTML_WITH_BASE}, confirm_domains=False)
        overrides = {"product_detail": b"khong phai file zip", "cart": make_zip({"style.css": "x"})}
        before = self.balance()
        self.assertEqual(self.run_analysis(module, get_zip=self.get_zip_with_override(overrides)), "failed")
        self.db.session.refresh(module)
        self.assertEqual(module.status, "failed")
        self.assertIn("không phải file .zip hợp lệ", module.error_message)
        self.assertIn("Cần đúng 1 file .html", module.error_message)
        self.assertEqual(module.actions, [])
        self.assertEqual(self.balance(), before, "chưa tốn LLM nào -> không trừ Credit")
        self.assertEqual(CreditTransaction.query.filter_by(type="module_analysis_charge").count(), 0)

    def test_llm_failures_are_isolated_per_url_and_never_swallowed_silently(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML_WITH_BASE, "cart": PRODUCT_HTML_WITH_BASE}, confirm_domains=False)
        calls = []

        def llm(messages):
            calls.append(1)
            if len(calls) == 2:
                raise ConnectionError("mạng đứt")
            return reply([ADD_CART])

        with self.assertLogs("app.modules.runner", level="ERROR"):
            self.assertEqual(self.run_analysis(module, llm=llm), "awaiting_domain_confirmation")
        self.db.session.refresh(module)
        failed = next(u for u in module.urls if u.error_message)
        self.assertIn("ConnectionError", failed.error_message)
        self.assertIn("mạng đứt", failed.error_message)
        self.assertEqual(failed.upload_status, "extracted", "giải nén + phát hiện domain vẫn thành công dù LLM lỗi")
        self.assertEqual(len(module.actions), 1)

    def test_invalid_llm_json_is_reported_on_the_url_and_the_usage_is_still_charged(self):
        module = self.make_module(confirm_domains=False)
        bad = lambda messages: LLMReply("không phải json", {"prompt_tokens": 500, "completion_tokens": 10})
        self.assertEqual(self.run_analysis(module, llm=bad), "failed")
        self.db.session.refresh(module)
        self.assertIn("không trả JSON hợp lệ", module.urls[0].error_message)
        self.assertEqual(module.urls[0].upload_status, "extracted", "giải nén thành công, chỉ bước LLM lỗi")
        self.assertGreater(module.analysis_charged_vnd, 0, "2 lần gọi LLM đã tốn tiền thật")

    def test_not_enough_credit_blocks_before_any_extraction_or_llm_call(self):
        module = self.make_module(confirm_domains=False)
        account = credits._locked_account(self.team.id)
        credits._add_transaction(account, "adjustment", -(account.balance_vnd - D("0.01")))
        self.db.session.commit()
        llm_calls = []

        def get_zip(module_url):
            raise AssertionError("không được gọi vì chưa đủ Credit")

        self.assertEqual(self.run_analysis(module, get_zip=get_zip, llm=lambda m: llm_calls.append(m) or reply([])), "failed")
        self.db.session.refresh(module)
        self.assertIn("Không đủ AI Credit", module.error_message)
        self.assertEqual(llm_calls, [])
        self.assertEqual(self.balance(), D("0.0100"))

    def test_cost_beyond_the_balance_collects_only_the_balance_and_never_goes_negative(self):
        module = self.make_module(confirm_domains=False)
        account = credits._locked_account(self.team.id)
        target = runner.estimate_analysis_vnd(1)
        credits._add_transaction(account, "adjustment", -(account.balance_vnd - target))
        self.db.session.commit()
        huge = lambda messages: LLMReply(json.dumps({"actions": []}), {"prompt_tokens": 50_000_000, "completion_tokens": 5_000_000})
        with self.assertLogs("app.credits.service", level="WARNING"):
            self.run_analysis(module, llm=huge)
        self.db.session.refresh(module)
        self.assertEqual(self.balance(), D("0.0000"))
        self.assertEqual(module.analysis_charged_vnd, target)
        self.assertGreater(module.analysis_cost_vnd, target)
        self.assertGreaterEqual(min(t.balance_after_vnd for t in CreditTransaction.query.filter_by(team_id=self.team.id)), 0)

    def test_dropped_hallucinated_actions_and_empty_results_are_explained_to_the_owner(self):
        module = self.make_module(confirm_domains=False)
        hallucinated = {**ADD_CART, "action_name": "ma", "selector_spec": {"selector": "#khong-co"}}
        self.run_analysis(module, llm=lambda m: reply([hallucinated]))
        self.db.session.refresh(module)
        self.assertEqual(module.actions, [])
        self.assertIn("đã bỏ hành động ma", module.analysis_note)
        self.assertIn("JavaScript", module.analysis_note, "không tìm thấy gì -> gợi ý lưu lại trang sau khi nội dung JS đã tải xong")

    def test_reanalysis_replaces_the_old_result_and_requires_approval_for_changes(self):
        module = self.make_module(confirm_domains=False)
        self.run_analysis(module)
        action = module.actions[0]
        action.verified = True
        self.db.session.commit()
        self.run_analysis(module)
        self.db.session.refresh(action)
        self.assertTrue(action.verified, "kết quả giống hệt -> giữ nguyên trạng thái đã duyệt")
        changed = {**ADD_CART, "selector_spec": {"selector": "#add-form button"}}
        self.run_analysis(module, llm=lambda m: reply([changed]))
        self.db.session.refresh(action)
        self.assertFalse(action.verified)

    def test_keepalive_runs_after_every_url_and_a_deleted_module_is_a_noop(self):
        module = self.make_module(html_by_role={"product_detail": PRODUCT_HTML_WITH_BASE, "cart": PRODUCT_HTML_WITH_BASE}, confirm_domains=False)
        ticks = []
        self.run_analysis(module, keepalive=lambda: ticks.append(1))
        self.assertEqual(len(ticks), 2)
        self.assertIsNone(runner.analyze_module(987654))

    def test_estimate_is_an_upper_bound_of_a_typical_run_and_scales_with_urls_and_markup(self):
        one, three = runner.estimate_analysis_vnd(1), runner.estimate_analysis_vnd(3)
        self.assertGreater(three, one)
        with mock.patch.object(Config, "PLATFORM_MARKUP_MULTIPLIER", D("2.0")):
            self.assertEqual(runner.estimate_analysis_vnd(1), (one * 2).quantize(D("0.0001")))
        module = self.make_module(confirm_domains=False)
        self.run_analysis(module)
        self.db.session.refresh(module)
        self.assertLess(module.analysis_charged_vnd, one)


class WorkerClaim(ModulesCase):
    def test_a_pending_module_is_claimed_exactly_once(self):
        from workers import module_analysis

        module = self.make_module(status="pending")
        first = module_analysis.claim_next_module()
        self.assertEqual(first.id, module.id)
        self.db.session.refresh(module)
        self.assertEqual(module.status, "analyzing")
        self.assertIsNone(module_analysis.claim_next_module())

    def test_handle_marks_a_crashing_analysis_as_failed_instead_of_leaving_it_stuck(self):
        from workers import module_analysis

        module = self.make_module(status="analyzing")
        with mock.patch("app.modules.runner.analyze_module", side_effect=RuntimeError("DB chập chờn")), self.assertLogs("workers.module_analysis", level="ERROR"):
            module_analysis.handle(module)
        self.db.session.refresh(module)
        self.assertEqual(module.status, "failed")
        self.assertIn("RuntimeError", module.error_message)

    def test_worker_uses_its_own_lock_key(self):
        from workers import context_jobs, module_analysis, process_documents

        keys = {module_analysis.LOCK_KEY, process_documents.LOCK_KEY, getattr(context_jobs, "LOCK_KEY", "context-jobs-lock")}
        self.assertEqual(module_analysis.LOCK_KEY, "module-analysis-worker-lock")
        self.assertEqual(len(keys), 3)


class Routes(ModulesCase):
    def setUp(self):
        super().setUp()
        self.client = self.login(self.team.user, self.team)

    def login(self, user, team):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["team_id"] = team.id
            session["csrf_token"] = "tok"
        return client

    def member(self):
        user = User(email="member@example.com", password_hash="x", full_name="Member")
        self.db.session.add(user)
        self.db.session.flush()
        self.db.session.add(TeamMember(team_id=self.team.id, user_id=user.id, role="Member"))
        self.db.session.commit()
        return self.login(user, self.team)

    def url(self, path=""):
        return f"/bots/{self.bot.id}/modules{path}"

    def post(self, path, data=None, client=None):
        return (client or self.client).post(self.url(path), data={"csrf_token": "tok", **(data or {})})

    def test_index_lists_types_and_modules_and_offers_the_form_to_owners(self):
        self.make_module()
        html = self.client.get(self.url()).get_data(as_text=True)
        self.assertIn("Hỗ trợ bán hàng", html)
        self.assertIn("Khai báo module mới", html)
        self.assertIn("Cho phép trợ lý thực hiện hành động thanh toán", html)
        self.assertIn("Đang chờ phân tích", html)
        self.assertIn('href="/bots/%d/modules"' % self.bot.id, html, "mục điều hướng của bot")

    def test_members_can_view_but_not_change_anything(self):
        module = self.make_module()
        action = self.make_action(module)
        member = self.member()
        self.assertEqual(member.get(self.url()).status_code, 200)
        self.assertNotIn("Khai báo module mới", member.get(self.url()).get_data(as_text=True))
        detail = member.get(self.url(f"/{module.id}")).get_data(as_text=True)
        self.assertNotIn(">Duyệt<", detail)
        for path, data in (
            ("", {"module_type_id": self.type.id, "file_product_detail": zip_field()}), (f"/{module.id}/analyze", {}), (f"/{module.id}/delete", {}),
            (f"/{module.id}/actions/{action.id}/approve", {}), (f"/{module.id}/actions/{action.id}/unapprove", {}), ("/settings", {"allow_agent_payment_actions": "1"}),
            (f"/{module.id}/urls/{module.urls[0].id}/confirm-domain", {"source_url": "shop.vn"}),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.post(path, data, client=member).status_code, 403)
        self.db.session.refresh(action)
        self.assertFalse(action.verified)

    def test_anonymous_users_are_redirected_and_other_teams_get_404(self):
        module = self.make_module()
        self.assertEqual(self.app.test_client().get(self.url()).status_code, 302)
        other_team = self.make_team("Team B")
        other = self.login(other_team.user, other_team)
        for path in ("", f"/{module.id}", f"/{module.id}/status"):
            self.assertEqual(other.get(self.url(path)).status_code, 404, path)
        for path in (f"/{module.id}/analyze", f"/{module.id}/delete"):
            self.assertEqual(self.post(path, client=other).status_code, 404, path)
        self.assertEqual(self.client.get(self.url("/999999")).status_code, 404)

    def test_create_through_the_form_uploads_zip_files_and_validates_on_the_server(self):
        response = self.post("", {"module_type_id": self.type.id, "name": "Shop", "file_product_detail": zip_field()})
        self.assertEqual(response.status_code, 302)
        module = BotModule.query.one()
        self.assertEqual((module.module_type_id, module.status, module.name), (self.type.id, "pending", "Shop"))
        self.assertEqual([u.url_role for u in module.urls], ["product_detail"])
        self.assertEqual(module.urls[0].upload_status, "uploaded")
        self.assertIsNotNone(module.urls[0].upload_storage_key)
        self.assertIn(f"/modules/{module.id}", response.headers["Location"])
        # thiếu ô bắt buộc / file không phải .zip hợp lệ -> báo lỗi, không tạo thêm
        self.post("", {"module_type_id": self.type.id, "file_cart": zip_field()})
        self.post("", {"module_type_id": self.type.id, "file_product_detail": (io.BytesIO(b"khong phai zip"), "bad.zip")})
        self.post("", {})
        self.assertEqual(BotModule.query.count(), 1)
        with self.client.session_transaction() as session:
            flashes = [message for _, message in session.get("_flashes", [])]
        self.assertTrue(any("URL trang sản phẩm" in m for m in flashes))

    def test_confirm_domain_route_updates_the_url_and_can_flip_the_module_to_ready(self):
        module = self.make_module(confirm_domains=False)
        module.urls[0].upload_status, module.status = "extracted", "awaiting_domain_confirmation"
        self.db.session.commit()
        response = self.post(f"/{module.id}/urls/{module.urls[0].id}/confirm-domain", {"source_url": "https://www.Shop.vn/x"})
        self.assertEqual(response.status_code, 302)
        self.db.session.refresh(module)
        self.assertEqual((module.urls[0].source_url, module.urls[0].upload_status, module.status), ("shop.vn", "domain_confirmed", "ready"))
        self.assertEqual(self.post("/999999/urls/1/confirm-domain", {"source_url": "shop.vn"}).status_code, 404)
        self.assertEqual(self.post(f"/{module.id}/urls/999999/confirm-domain", {"source_url": "shop.vn"}).status_code, 404)

    def test_csrf_is_required_for_every_change(self):
        module = self.make_module()
        action = self.make_action(module)
        for path in ("", f"/{module.id}/analyze", f"/{module.id}/delete", f"/{module.id}/actions/{action.id}/approve", "/settings"):
            with self.subTest(path=path):
                response = self.client.post(self.url(path), data={"csrf_token": "sai", "module_type_id": self.type.id, "file_product_detail": zip_field()})
                self.assertEqual(response.status_code, 302)
        self.assertEqual(BotModule.query.count(), 1)
        self.db.session.refresh(action)
        self.assertFalse(action.verified)
        self.assertFalse(self.service.get_or_create_settings(self.bot).allow_agent_payment_actions)

    def test_detail_shows_progress_urls_errors_actions_and_the_domain_warning(self):
        module = self.make_module(status="ready")
        module.urls[0].error_message = "Lỗi khi giải nén: ZipExtractError: hỏng"
        module.analysis_note = "đã bỏ hành động ma"
        action = self.make_action(module, risk="cart", confidence=0.7, action_type="add_to_cart", spec={"selector": "#add-btn", "event": "click"}, name="them_gio")
        html = self.client.get(self.url(f"/{module.id}")).get_data(as_text=True)
        for needle in ("Đã phân tích xong", "shop.vn", "Lỗi khi giải nén", "đã bỏ hành động ma", "them_gio", "Giỏ hàng", "70%", "chưa được khai báo ở Bước 3", "Đã xác nhận domain"):
            self.assertIn(needle, html, needle)
        self.assertIn("thấp hơn ngưỡng 0.80", html, "lý do không duyệt được hiện cạnh nút bị vô hiệu")
        for hidden in ("prompt_tokens", "llm_input", "cache_hit"):
            self.assertNotIn(hidden, html)
        status = self.client.get(self.url(f"/{module.id}/status")).get_json()
        self.assertEqual((status["status"], status["done"], status["total"]), ("ready", 0, 1))
        self.assertEqual(action.verified, False)

    def test_detail_shows_the_confirm_domain_form_when_a_url_is_waiting(self):
        module = self.make_module(confirm_domains=False)
        module.urls[0].upload_status, module.urls[0].detected_domain, module.status = "extracted", "shop.vn", "awaiting_domain_confirmation"
        self.db.session.commit()
        html = self.client.get(self.url(f"/{module.id}")).get_data(as_text=True)
        self.assertIn("Chờ xác nhận domain", html)
        self.assertIn('value="shop.vn"', html, "điền sẵn detected_domain vào ô xác nhận")
        self.assertIn(f'/urls/{module.urls[0].id}/confirm-domain', html)

    def test_approve_and_unapprove_routes_enforce_the_thresholds(self):
        module = self.make_module(status="ready")
        good = self.make_action(module, confidence=0.9, name="tot")
        weak = self.make_action(module, confidence=0.3, name="yeu")
        self.post(f"/{module.id}/actions/{good.id}/approve")
        self.post(f"/{module.id}/actions/{weak.id}/approve")
        self.db.session.refresh(good), self.db.session.refresh(weak)
        self.assertEqual((good.verified, weak.verified), (True, False))
        self.post(f"/{module.id}/actions/{good.id}/unapprove")
        self.db.session.refresh(good)
        self.assertFalse(good.verified)
        self.assertEqual(self.post(f"/{module.id}/actions/99999/approve").status_code, 404)
        other_module = self.make_module(status="ready")
        self.assertEqual(self.post(f"/{other_module.id}/actions/{good.id}/approve").status_code, 404, "action phải thuộc đúng module")

    def test_payment_action_cannot_be_approved_through_the_ui_until_the_switch_is_on(self):
        module = self.make_module(status="ready")
        pay = self.make_action(module, risk="payment", confidence=0.99, action_type="click", spec={"selector": "#pay-btn", "event": "click"}, name="dat_hang")
        html = self.client.get(self.url(f"/{module.id}")).get_data(as_text=True)
        self.assertIn("công tắc", html)
        self.assertRegex(html, r'<button class="btnp" type="submit" disabled>Duyệt</button>')
        self.post(f"/{module.id}/actions/{pay.id}/approve")
        self.db.session.refresh(pay)
        self.assertFalse(pay.verified, "POST thẳng cũng bị chặn ở server")
        self.post("/settings", {"allow_agent_payment_actions": "1"})
        self.assertTrue(self.service.get_or_create_settings(self.bot).allow_agent_payment_actions)
        self.post(f"/{module.id}/actions/{pay.id}/approve")
        self.db.session.refresh(pay)
        self.assertTrue(pay.verified)
        self.post("/settings", {"allow_agent_payment_actions": "0"})
        self.db.session.refresh(pay)
        self.assertFalse(pay.verified, "tắt công tắc -> bỏ duyệt các hành động thanh toán")

    def test_there_is_no_bulk_approve_route(self):
        rules = [rule.rule for rule in self.app.url_map.iter_rules() if "modules" in rule.rule]
        self.assertFalse([r for r in rules if "approve" in r and "<int:action_id>" not in r], rules)
        self.assertEqual(self.post("/1/approve-all").status_code, 404)

    def test_analyze_and_delete_routes(self):
        module = self.make_module(status="failed")
        self.post(f"/{module.id}/analyze")
        self.db.session.refresh(module)
        self.assertEqual(module.status, "pending")
        self.assertEqual(module.urls[0].upload_status, "uploaded")
        module.status = "analyzing"
        self.db.session.commit()
        self.post(f"/{module.id}/delete")
        self.assertEqual(BotModule.query.count(), 1, "đang phân tích thì không xoá được")
        module.status = "ready"
        self.db.session.commit()
        self.post(f"/{module.id}/delete")
        self.assertEqual(BotModule.query.count(), 0)

    def test_payment_switch_defaults_off_and_the_column_default_is_safe(self):
        self.assertFalse(self.service.get_or_create_settings(self.bot).allow_agent_payment_actions)
        column = self.db.session.execute(self.db.text("SHOW COLUMNS FROM bot_settings LIKE 'allow_agent_payment_actions'")).one()
        self.assertEqual(str(column[4]), "0", "server_default = 0 (tắt)")


if __name__ == "__main__":
    unittest.main()
