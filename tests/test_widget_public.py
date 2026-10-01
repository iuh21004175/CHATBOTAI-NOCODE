"""Web Widget công khai: định danh bot bằng public_id (không lộ id tuần tự) + nhiều domain được phép nhúng cho 1 bot.
Phần thuần (domains.py) không cần DB; phần route/DB cần DB *_test (xem tests/README.md)."""
import unittest

from app.models import Bot, BotDomain, new_public_id
from app.widget import domains
from app.widget.domains import is_valid_domain, normalize_domain, origin_allowed
from core.context_engine.structured import LLMReply
from tests.db_case import DbCase
from tests.helpers import llm_json, usage


class NormalizeAndValidate(unittest.TestCase):
    def test_normalize_domain_forms(self):
        for raw, expected in {
            "shop.vn": "shop.vn", "  Shop.VN ": "shop.vn", "www.shop.vn": "shop.vn", "https://www.Shop.vn/abc?x=1": "shop.vn",
            "shop.vn:443": "shop.vn", "http://localhost:5000/": "localhost", "": "", None: "",
        }.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_domain(raw), expected)

    def test_valid_and_invalid_hostnames(self):
        for ok in ("shopabc.vn", "shop-abc.com.vn", "localhost", "a.b.c.example.org", "127.0.0.1"):
            self.assertTrue(is_valid_domain(ok), ok)
        for bad in ("", "shop abc.vn", "shop_abc.vn", "-shop.vn", "shop-.vn", "shop..vn", ".shop.vn", "shop.vn.", "a" * 64 + ".vn", "sh@p.vn"):
            self.assertFalse(is_valid_domain(bad), bad)


class OriginAllowed(unittest.TestCase):
    def test_exact_and_subdomain_match(self):
        self.assertTrue(origin_allowed("https://shopabc.vn", ["shopabc.vn"]))
        self.assertTrue(origin_allowed("https://www.shopabc.vn", ["shopabc.vn"]))
        self.assertTrue(origin_allowed("https://blog.shop.shopabc.vn", ["shopabc.vn"]))

    def test_any_domain_in_the_list_matches(self):
        allowed = ["shopabc.vn", "shopabc.com.vn", "localhost"]
        for origin in ("https://shopabc.vn", "https://shopabc.com.vn", "http://localhost:5000", "https://m.shopabc.com.vn"):
            self.assertTrue(origin_allowed(origin, allowed), origin)

    def test_unrelated_and_lookalike_domains_are_rejected(self):
        allowed = ["shopabc.vn", "shopabc.com.vn"]
        for origin in ("https://evil.com", "https://shopabc.vn.evil.com", "https://notshopabc.vn", "https://shopabc.com", "https://xshopabc.com.vn"):
            self.assertFalse(origin_allowed(origin, allowed), origin)

    def test_empty_missing_or_null_origin_is_rejected(self):
        self.assertFalse(origin_allowed("https://shopabc.vn", []))
        self.assertFalse(origin_allowed("https://shopabc.vn", [""]))
        for origin in (None, "", "null"):
            self.assertFalse(origin_allowed(origin, ["shopabc.vn"]), origin)

    def test_a_plain_string_is_not_treated_as_a_list_of_characters(self):
        # Truyền nhầm chuỗi thay vì danh sách: từng ký tự bị coi là 1 domain -> "s" không được khớp "shop.vn"
        self.assertFalse(origin_allowed("https://shop.vn", "shop.vn"))


class PublicIdGeneration(unittest.TestCase):
    def test_public_ids_are_unguessable_and_unique(self):
        ids = {new_public_id() for _ in range(2000)}
        self.assertEqual(len(ids), 2000)
        for value in list(ids)[:50]:
            self.assertGreaterEqual(len(value), 32)
            self.assertLessEqual(len(value), 64)
            self.assertFalse(value.isdigit())
        self.assertLessEqual(domains.MAX_DOMAINS_PER_BOT, 50)


class WidgetApi(DbCase):
    """Route công khai qua test client thật (không đăng nhập)."""

    ORIGIN = "https://shopabc.vn"

    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()
        self.add_domain(self.bot, "shopabc.vn")

    def add_domain(self, bot, domain):
        self.db.session.add(BotDomain(bot_id=bot.id, domain=domain))
        self.db.session.commit()

    def get(self, ident, path="config", origin=ORIGIN, **kw):
        headers = {"Origin": origin} if origin else {}
        return self.client.get(f"/widget/api/{ident}/{path}", headers=headers, **kw)

    def post_message(self, ident, origin=ORIGIN, message="Giá gói Pro?"):
        headers = {"Origin": origin} if origin else {}
        return self.client.post(f"/widget/api/{ident}/messages", json={"message": message, "visitor_id": "v1"}, headers=headers)

    def test_new_bots_get_a_public_id_different_from_their_numeric_id(self):
        self.assertTrue(self.bot.public_id)
        self.assertNotEqual(self.bot.public_id, str(self.bot.id))
        other = self.service.create_bot(self.team.id, "Bot B")
        self.assertNotEqual(other.public_id, self.bot.public_id)

    def test_config_with_public_id_is_200_and_exposes_no_internal_id(self):
        response = self.get(self.bot.public_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["name"], "Bot A")
        self.assertNotIn("id", response.get_json())
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], self.ORIGIN)

    def test_old_numeric_id_is_404_on_every_public_endpoint(self):
        for path in ("config", "icon", "staff-messages"):
            with self.subTest(path=path):
                self.assertEqual(self.get(self.bot.id, path).status_code, 404)
        self.assertEqual(self.post_message(self.bot.id).status_code, 404)

    def test_unknown_or_malformed_public_id_is_404_not_500(self):
        for ident in ("khong-ton-tai", "x" * 65, "x" * 500, "%20", "0", "-1"):
            with self.subTest(ident=ident):
                self.assertEqual(self.get(ident).status_code, 404)

    def test_sequential_ids_cannot_enumerate_bots(self):
        other_team = self.make_team("Team B")
        other = self.service.create_bot(other_team.id, "Bot B")
        self.add_domain(other, "shopabc.vn")
        for guess in range(1, other.id + 3):
            self.assertEqual(self.get(guess).status_code, 404, guess)

    def test_message_flow_works_with_public_id(self):
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        response = self.post_message(self.bot.public_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "Gói Pro giá 500.000đ.")

    def test_origin_must_match_a_declared_domain_even_with_a_valid_public_id(self):
        self.assertEqual(self.get(self.bot.public_id, origin="https://evil.com").status_code, 403)
        self.assertEqual(self.get(self.bot.public_id, origin=None).status_code, 403)
        self.assertEqual(self.post_message(self.bot.public_id, origin="https://evil.com").status_code, 403)

    def test_several_domains_are_all_accepted_and_others_rejected(self):
        self.add_domain(self.bot, "shopabc.com.vn")
        for origin in ("https://shopabc.vn", "https://shopabc.com.vn", "https://www.shopabc.com.vn", "https://blog.shopabc.vn"):
            self.assertEqual(self.get(self.bot.public_id, origin=origin).status_code, 200, origin)
        self.assertEqual(self.get(self.bot.public_id, origin="https://shopabc.com").status_code, 403)

    def test_bot_without_any_domain_is_refused(self):
        BotDomain.query.delete()
        self.db.session.commit()
        self.assertEqual(self.get(self.bot.public_id).status_code, 403)

    def test_domains_are_scoped_per_bot(self):
        other_team = self.make_team("Team B")
        other = self.service.create_bot(other_team.id, "Bot B")
        self.add_domain(other, "khac.vn")
        self.assertEqual(self.get(other.public_id, origin="https://khac.vn").status_code, 200)
        self.assertEqual(self.get(other.public_id, origin=self.ORIGIN).status_code, 403, "domain của bot A không mở được bot B")
        self.assertEqual(self.get(self.bot.public_id, origin="https://khac.vn").status_code, 403)

    def test_conversations_stay_with_the_bot_the_public_id_points_to(self):
        from app.models import Conversation

        other_team = self.make_team("Team B")
        other = self.service.create_bot(other_team.id, "Bot B")
        self.add_domain(other, "shopabc.vn")
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        self.post_message(other.public_id)
        self.assertEqual(Conversation.query.filter_by(bot_id=self.bot.id).count(), 0)
        self.assertEqual(Conversation.query.filter_by(bot_id=other.id).count(), 1)

    def test_custom_icon_url_uses_public_id(self):
        from app.widget import service as widget_service

        settings = self.service.get_or_create_settings(self.bot)
        settings.widget_icon, settings.widget_icon_path = "custom", "icons/1/x.png"
        self.db.session.commit()
        with self.app.test_request_context("/"):
            url = widget_service.icon_url(self.bot, settings)
        self.assertIn(f"/widget/api/{self.bot.public_id}/icon", url)
        self.assertNotIn(f"/widget/api/{self.bot.id}/", url)


class ManageDomainsInPublishStep(DbCase):
    """Bước 3 (Xuất bản): thêm/xóa domain và mã nhúng — qua route thật, có đăng nhập + CSRF."""

    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()
        self.login(self.client, self.team)

    def login(self, client, team):
        with client.session_transaction() as session:
            session["_user_id"] = str(team.user.id)
            session["team_id"] = team.id
            session["csrf_token"] = "tok"

    def add(self, value, client=None, bot=None, csrf="tok"):
        return (client or self.client).post(f"/bots/{(bot or self.bot).id}/publish/domains", data={"csrf_token": csrf, "widget_domain": value})

    def domains_of(self, bot=None):
        self.db.session.expire_all()
        return [d.domain for d in BotDomain.query.filter_by(bot_id=(bot or self.bot).id).order_by(BotDomain.id)]

    def test_add_normalises_and_lists_several_domains(self):
        self.assertEqual(self.add("https://www.ShopABC.vn/trang").status_code, 302)
        self.add("shopabc.com.vn")
        self.assertEqual(self.domains_of(), ["shopabc.vn", "shopabc.com.vn"])

    def test_invalid_duplicate_and_over_limit_are_rejected(self):
        self.add("shopabc.vn")
        for bad in ("", "  ", "khong hop le", "a b.vn", "shopabc.vn", "https://www.shopabc.vn/x"):
            self.add(bad)
        self.assertEqual(self.domains_of(), ["shopabc.vn"])
        for i in range(domains.MAX_DOMAINS_PER_BOT + 3):
            self.add(f"site{i}.vn")
        self.assertEqual(len(self.domains_of()), domains.MAX_DOMAINS_PER_BOT)

    def test_add_requires_csrf(self):
        self.add("shopabc.vn", csrf="sai")
        self.add("shopabc.vn", csrf="")
        self.assertEqual(self.domains_of(), [])

    def test_delete_removes_only_that_domain(self):
        self.add("a.vn")
        self.add("b.vn")
        first = BotDomain.query.filter_by(bot_id=self.bot.id, domain="a.vn").one()
        response = self.client.post(f"/bots/{self.bot.id}/publish/domains/{first.id}/delete", data={"csrf_token": "tok"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.domains_of(), ["b.vn"])

    def test_delete_needs_csrf_and_cannot_target_another_bots_domain(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.add("b.vn", bot=other)
        foreign = BotDomain.query.filter_by(bot_id=other.id).one()
        self.client.post(f"/bots/{self.bot.id}/publish/domains/{foreign.id}/delete", data={"csrf_token": "tok"})
        self.assertEqual(self.domains_of(other), ["b.vn"], "domain của bot khác không xóa được qua bot này")
        self.client.post(f"/bots/{other.id}/publish/domains/{foreign.id}/delete", data={"csrf_token": "sai"})
        self.assertEqual(self.domains_of(other), ["b.vn"])

    def test_other_team_cannot_add_or_delete(self):
        other_team = self.make_team("Team B")
        other_client = self.app.test_client()
        self.login(other_client, other_team)
        self.assertEqual(self.add("evil.com", client=other_client).status_code, 404)
        self.add("a.vn")
        row = BotDomain.query.filter_by(bot_id=self.bot.id).one()
        response = other_client.post(f"/bots/{self.bot.id}/publish/domains/{row.id}/delete", data={"csrf_token": "tok"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.domains_of(), ["a.vn"])

    def test_anonymous_is_redirected_to_login(self):
        response = self.add("a.vn", client=self.app.test_client())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertEqual(self.domains_of(), [])

    def test_publish_page_shows_domains_and_embed_code_with_public_id_only(self):
        self.add("shopabc.vn")
        html = self.client.get(f"/bots/{self.bot.id}/publish").get_data(as_text=True)
        self.assertIn("shopabc.vn", html)
        self.assertIn(f'data-bot-id="{self.bot.public_id}"', html)
        self.assertNotIn(f'data-bot-id="{self.bot.id}"', html)

    def test_publish_page_still_renders_without_domains(self):
        response = self.client.get(f"/bots/{self.bot.id}/publish")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Chưa cấu hình domain", response.get_data(as_text=True))

    def test_old_single_domain_post_endpoint_is_gone(self):
        self.assertEqual(self.client.post(f"/bots/{self.bot.id}/publish", data={"csrf_token": "tok", "widget_domain": "x.vn"}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
