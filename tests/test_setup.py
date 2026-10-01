"""Bước 1 — cấu hình Decision Engine: chế độ Cơ bản/Nâng cao (config_tier), tham số nội bộ RAG/Context Engine không nhận từ form,
công tắc bật/tắt tính năng đã cố định: kiểm tra form (thuần) và lưu/hiển thị qua route thật (DB *_test)."""
import unittest

from app.dashboard import service
from core.context_engine.settings import ADVANCED_FIELDS, DEFAULTS, ENGINE_INTERNAL, FIXED_TOGGLES, MEMORY_LEVELS
from tests.db_case import DbCase


def form(**fields):
    """Form hợp lệ mặc định (low_confidence_reply_mode luôn cần có kể từ khi bỏ mức cấu hình); truyền
    low_confidence_reply_mode=None để test trường hợp thiếu trường này."""
    data = {"low_confidence_reply_mode": "ask_clarify"}
    data.update(fields)
    return {k: (v if isinstance(v, str) else str(v)) for k, v in data.items() if v is not None}


class ParseEngineForm(unittest.TestCase):
    def test_toggles_are_always_fixed_regardless_of_form_input(self):
        values, error = service.parse_engine_form(form(rag_enabled="on", structured_memory_enabled="on", summary_enabled=""))
        self.assertIsNone(error)
        for name, expected in FIXED_TOGGLES.items():
            self.assertEqual(values[name], expected, name)

    def test_advanced_reads_every_advanced_field(self):
        values, error = service.parse_engine_form(form(
            config_tier="advanced", recent_message_limit=20, recent_token_limit=3000, summary_trigger_tokens=9000, summary_max_tokens=800))
        self.assertIsNone(error)
        self.assertEqual(values["config_tier"], "advanced")
        self.assertEqual([values[n] for n in ADVANCED_FIELDS], [20, 3000, 9000, 800])

    def test_tier_missing_reads_numbers_without_changing_the_tier(self):
        values, error = service.parse_engine_form(form(recent_message_limit=20))
        self.assertIsNone(error)
        self.assertEqual(values["recent_message_limit"], 20)
        self.assertNotIn("config_tier", values)

    def test_basic_maps_memory_level_to_the_preset_and_ignores_raw_numbers(self):
        for level, (messages, tokens) in MEMORY_LEVELS.items():
            values, error = service.parse_engine_form(form(
                config_tier="basic", memory_level=level, recent_message_limit=30, summary_max_tokens=2000))
            self.assertIsNone(error, level)
            self.assertEqual((values["recent_message_limit"], values["recent_token_limit"]), (messages, tokens), level)
            self.assertNotIn("summary_max_tokens", values, "ô số của chế độ Nâng cao bị bỏ qua khi đang ở Cơ bản")

    def test_basic_custom_or_blank_memory_level_keeps_stored_values(self):
        for level in ("custom", ""):
            values, error = service.parse_engine_form(form(config_tier="basic", memory_level=level))
            self.assertIsNone(error, level)
            self.assertNotIn("recent_message_limit", values)
            self.assertNotIn("recent_token_limit", values)

    def test_invalid_tier_or_memory_level_is_rejected(self):
        self.assertTrue(service.parse_engine_form(form(config_tier="expert"))[1])
        self.assertTrue(service.parse_engine_form(form(config_tier="basic", memory_level="huge"))[1])

    def test_engine_internal_fields_are_never_read_from_the_form(self):
        raw = {name: "1" for name in ENGINE_INTERNAL}
        for tier in ("basic", "advanced", None):
            values, error = service.parse_engine_form(form(config_tier=tier, **raw))
            self.assertIsNone(error, tier)
            self.assertFalse(set(ENGINE_INTERNAL) & set(values), tier)

    def test_blank_or_missing_numbers_keep_stored_value(self):
        values, _ = service.parse_engine_form(form(config_tier="advanced", recent_message_limit="  ", summary_max_tokens=None))
        self.assertNotIn("recent_message_limit", values)
        self.assertNotIn("summary_max_tokens", values)

    def test_out_of_range_and_bad_numbers_are_rejected_not_clamped(self):
        cases = {
            "recent_message_limit": ["0", "31", "abc", "1.5", "-3"],
            "recent_token_limit": ["199", "8001", "nan", "inf", "x"],
            "summary_max_tokens": ["99", "2001"],
        }
        for name, bad_values in cases.items():
            for bad in bad_values:
                with self.subTest(name=name, value=bad):
                    values, error = service.parse_engine_form(form(config_tier="advanced", **{name: bad}))
                    self.assertIsNone(values)
                    self.assertTrue(error)

    def test_boundaries_are_accepted(self):
        for name, (low, high) in {"recent_message_limit": (1, 30), "recent_token_limit": (200, 8000)}.items():
            for value in (low, high):
                values, error = service.parse_engine_form(form(config_tier="advanced", **{name: value}))
                self.assertIsNone(error, (name, value))

    def test_validation(self):
        base = {"low_confidence_reply_mode": "ask_clarify"}
        self.assertTrue(service.parse_engine_form(form(low_confidence_reply_mode="explode"))[1])
        self.assertTrue(service.parse_engine_form(form(**base, low_confidence_decline_message="x" * 501))[1])
        values, error = service.parse_engine_form(form(**base, low_confidence_clarify_message="  Bạn nói rõ hơn?  ", low_confidence_decline_message=" "))
        self.assertIsNone(error)
        self.assertEqual(values["low_confidence_clarify_message"], "Bạn nói rõ hơn?")
        self.assertIsNone(values["low_confidence_decline_message"], "để trống = dùng câu mặc định")

    def test_missing_reply_mode_is_rejected(self):
        values, error = service.parse_engine_form(form(low_confidence_reply_mode=None))
        self.assertIsNone(values)
        self.assertIn("không hợp lệ", error)


class SetupRoute(DbCase):
    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()
        self.login(self.team)

    def login(self, team, client=None):
        with (client or self.client).session_transaction() as session:
            session["_user_id"] = str(team.user.id)
            session["team_id"] = team.id
            session["csrf_token"] = "tok"

    def post(self, data, bot=None, **kw):
        payload = {"csrf_token": "tok", "name": "Bot A", "low_confidence_reply_mode": "ask_clarify", **data}
        return self.client.post(f"/bots/{(bot or self.bot).id}/setup", data=payload, **kw)

    def settings(self):
        self.db.session.expire_all()
        return self.service.get_or_create_settings(self.bot)

    def test_new_bot_defaults(self):
        s = self.settings()
        for name, default in DEFAULTS.items():
            self.assertEqual(getattr(s, name), default, name)

    def test_get_renders_tier_selector_defaulting_to_basic_and_hides_the_advanced_fields(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertIn('name="config_tier" value="basic" checked', html)
        self.assertRegex(html, r'name="config_tier" value="advanced"\s*>')
        self.assertIn('id="memory_level"', html)
        self.assertRegex(html, r'data-tier-show="advanced" hidden')
        self.assertNotRegex(html, r'data-tier-show="basic" hidden')
        for name in ADVANCED_FIELDS:
            self.assertIn(f'name="{name}"', html, name)
        self.assertIn('name="low_confidence_reply_mode"', html)
        self.assertNotIn('name="min_similarity"', html, "slider cosine cũ đã được thay bằng ngưỡng khoảng cách")

    def test_advanced_tier_renders_the_advanced_block_visible_and_basic_block_hidden(self):
        self.post(form(config_tier="advanced"))
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertNotRegex(html, r'data-tier-show="advanced" hidden')
        self.assertRegex(html, r'data-tier-show="basic" hidden')

    def test_engine_internal_and_dead_fields_are_not_rendered_in_any_tier(self):
        for tier in ("basic", "advanced"):
            self.post(form(config_tier=tier))
            html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
            for name in (*ENGINE_INTERNAL, "forward_to_staff", "away_message"):
                self.assertNotIn(f'name="{name}"', html, (tier, name))
            self.assertNotIn("Chuyển tiếp cho nhân viên", html)
            self.assertIn('name="collect_customer_info"', html)

    def test_fixed_toggles_are_not_rendered_as_controls(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        for name in FIXED_TOGGLES:
            self.assertNotIn(f'data-toggle-target="{name}"', html, name)
            self.assertNotIn(f'name="{name}"', html, name)

    def test_model_settings_are_merged_into_smart_reply_card(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertNotIn("Cấu hình mô hình AI", html, "đã gộp vào card Trả lời thông minh")
        card = html[html.index('id="engine-card"'):html.index('<div class="card-title">Thu thập dữ liệu</div>')]
        for field_id in ("language", "max_tokens"):
            self.assertIn(f'id="{field_id}"', card, field_id)
        self.assertEqual(html.count('id="max_tokens"'), 1)
        for gone in ('id="temperature"', 'name="temperature"', "Độ sáng tạo"):
            self.assertNotIn(gone, html, "temperature không còn cấu hình theo bot")

    def test_labels_show_no_static_values(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        for label in ("Token đầu ra tối đa", "Số tin gần nhất đưa vào ngữ cảnh"):
            self.assertIn(f'>{label}</label>', html, f"nhãn '{label}' không kèm giá trị")
        for old_id in ("max-tokens-value", "temp-value"):
            self.assertNotIn(old_id, html)

    def test_every_engine_field_has_tooltip_and_slider_shows_live_value_outside_label(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        for spec in service.ENGINE_FIELDS:
            self.assertTrue(spec.get("help"), f"{spec['name']} thiếu mô tả tác dụng")
            self.assertIn(f'id="tip-{spec["name"]}"', html, spec["name"])
            self.assertIn(f'aria-describedby="tip-{spec["name"]}"', html, spec["name"])
        self.assertIn('<output class="slider-val" id="max_tokens-value"', html)

    def test_advanced_save_honours_the_advanced_fields_and_fixes_toggles(self):
        response = self.post(form(config_tier="advanced", recent_message_limit=20, recent_token_limit=3000, summary_trigger_tokens=9000,
                                   summary_max_tokens=800, low_confidence_reply_mode="decline"))
        self.assertEqual(response.status_code, 302)
        s = self.settings()
        self.assertEqual(s.config_tier, "advanced")
        self.assertEqual([getattr(s, n) for n in ADVANCED_FIELDS], [20, 3000, 9000, 800])
        self.assertEqual(s.low_confidence_reply_mode, "decline")
        for name, expected in FIXED_TOGGLES.items():
            self.assertEqual(getattr(s, name), expected, name)

    def test_basic_save_applies_the_memory_level_preset_and_keeps_the_advanced_only_values(self):
        self.post(form(config_tier="advanced", summary_max_tokens=800))
        for level, (messages, tokens) in MEMORY_LEVELS.items():
            self.post(form(config_tier="basic", memory_level=level, recent_message_limit=30, summary_max_tokens=100))
            s = self.settings()
            self.assertEqual((s.config_tier, s.recent_message_limit, s.recent_token_limit), ("basic", messages, tokens), level)
            self.assertEqual(s.summary_max_tokens, 800, "ô số của Nâng cao bị bỏ qua khi lưu ở Cơ bản, giá trị cũ giữ nguyên")

    def test_custom_memory_level_is_offered_and_saving_it_keeps_the_stored_numbers(self):
        self.post(form(config_tier="advanced", recent_message_limit=7, recent_token_limit=1500))
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertRegex(html, r'<option value="custom" selected>')
        self.post(form(config_tier="basic", memory_level="custom"))
        s = self.settings()
        self.assertEqual((s.config_tier, s.recent_message_limit, s.recent_token_limit), ("basic", 7, 1500))

    def test_preset_memory_level_is_preselected_and_no_custom_option_then(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertRegex(html, r'<option value="medium" selected>')
        self.assertNotIn('value="custom"', html)

    def test_engine_internal_values_in_the_form_or_db_never_reach_the_engine(self):
        from core.context_engine.settings import EngineSettings

        self.post(form(config_tier="advanced", **{name: "1" for name in ENGINE_INTERNAL}))
        s = self.settings()
        for name in ENGINE_INTERNAL:
            self.assertEqual(getattr(s, name), DEFAULTS[name], f"form không được ghi {name}")
        self.set_settings(rag_top_k=12)
        self.assertEqual(EngineSettings.from_model(self.settings()).rag_top_k, DEFAULTS["rag_top_k"])

    def test_invalid_tier_saves_nothing(self):
        response = self.post(form(config_tier="expert", recent_message_limit=20))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Chế độ cấu hình không hợp lệ", response.get_data(as_text=True))
        self.assertEqual(self.settings().recent_message_limit, DEFAULTS["recent_message_limit"])

    def test_saving_does_not_overwrite_the_hidden_forward_and_away_columns(self):
        s = self.settings()
        s.forward_to_staff, s.away_message = False, "Ngoài giờ làm việc"
        self.db.session.commit()
        self.post(form(config_tier="basic", memory_level="medium"))
        s = self.settings()
        self.assertEqual((s.forward_to_staff, s.away_message), (False, "Ngoài giờ làm việc"))

    def test_toggles_stay_fixed_even_if_client_sends_the_opposite(self):
        # UI không còn gửi các trường này, nhưng nếu có ai POST thủ công giá trị ngược lại thì server vẫn phải bỏ qua.
        opposite_form = {name: ("" if expected else "on") for name, expected in FIXED_TOGGLES.items()}
        self.post(form(**opposite_form))
        s = self.settings()
        for name, expected in FIXED_TOGGLES.items():
            self.assertEqual(getattr(s, name), expected, name)

    def test_invalid_value_saves_nothing_and_shows_error(self):
        response = self.post(form(recent_message_limit=999))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Số tin gần nhất", response.get_data(as_text=True))
        s = self.settings()
        self.assertEqual(s.recent_message_limit, DEFAULTS["recent_message_limit"])

    def test_invalid_value_does_not_rename_the_bot(self):
        self.client.post(f"/bots/{self.bot.id}/setup", data={"csrf_token": "tok", "name": "Tên mới", "low_confidence_reply_mode": "ask_clarify", **form(recent_message_limit=999)})
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(type(self.bot), self.bot.id).name, "Bot A")

    def test_missing_or_wrong_csrf_saves_nothing(self):
        self.client.post(f"/bots/{self.bot.id}/setup", data={"name": "X", "low_confidence_reply_mode": "decline"})
        self.assertEqual(self.settings().recent_message_limit, DEFAULTS["recent_message_limit"])
        self.client.post(f"/bots/{self.bot.id}/setup", data={"csrf_token": "sai", "name": "X", "low_confidence_reply_mode": "decline"})
        self.assertEqual(self.settings().recent_message_limit, DEFAULTS["recent_message_limit"])

    def test_legacy_min_similarity_field_is_ignored(self):
        before = self.settings().min_similarity
        self.assertEqual(self.post(form(min_similarity="0.55")).status_code, 302)
        self.assertEqual(self.settings().min_similarity, before)

    def test_other_teams_bot_is_404_and_untouched(self):
        other_team = self.make_team("Team B")
        other_client = self.app.test_client()
        self.login(other_team, other_client)
        response = other_client.post(f"/bots/{self.bot.id}/setup", data={"csrf_token": "tok", "name": "Hack", "low_confidence_reply_mode": "decline"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(other_client.get(f"/bots/{self.bot.id}/setup").status_code, 404)
        self.assertEqual(self.settings().recent_message_limit, DEFAULTS["recent_message_limit"])

    def test_anonymous_is_redirected_to_login(self):
        anonymous = self.app.test_client()
        response = anonymous.post(f"/bots/{self.bot.id}/setup", data=form(low_confidence_reply_mode="ask_clarify"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def estimate(self, data, csrf="tok", client=None, bot=None):
        payload = {"low_confidence_reply_mode": "ask_clarify", **data}
        return (client or self.client).post(f"/bots/{(bot or self.bot).id}/setup/cost-estimate", data=payload, headers={"X-CSRF-Token": csrf})

    def test_cost_estimate_uses_unsaved_form_values_and_saves_nothing(self):
        small = self.estimate({**form(), "max_tokens": "200", "language": "vi"})
        big = self.estimate({**form(), "max_tokens": "2500", "language": "vi"})
        self.assertEqual((small.status_code, big.status_code), (200, 200))
        s, b = small.get_json()["question"], big.get_json()["question"]
        self.assertLess(s["max"]["vnd"]["off_peak"], b["max"]["vnd"]["off_peak"])
        self.assertEqual(s["min"], b["min"])
        self.assertLess(s["min"]["vnd"]["off_peak"], s["max"]["vnd"]["off_peak"])
        self.assertLess(s["max"]["vnd"]["off_peak"], s["max"]["vnd"]["peak"])
        saved = self.settings()
        self.assertEqual(saved.max_tokens, 500, "chỉ ước tính, không lưu")

    def test_cost_estimate_follows_instructions_and_language(self):
        base = self.estimate({**form(), "instructions": "Ngắn."}).get_json()
        long = self.estimate({**form(), "instructions": "Chỉ dẫn rất dài. " * 200}).get_json()
        self.assertGreater(long["question"]["min"]["input"], base["question"]["min"]["input"])
        en = self.estimate({**form(), "instructions": "Ngắn.", "language": "en"}).get_json()
        self.assertNotEqual(en["question"]["min"]["input"], base["question"]["min"]["input"])

    def test_cost_estimate_follows_the_tier_and_memory_settings_on_the_form(self):
        def part(result, key):
            return next(c for c in result["components"] if c["key"] == key)["max"]

        default = self.estimate(form()).get_json()
        short = self.estimate({**form(), "config_tier": "basic", "memory_level": "short"}).get_json()
        self.assertLess(part(short, "recent"), part(default, "recent"))
        self.assertLess(short["question"]["max"]["input"], default["question"]["max"]["input"])
        advanced = self.estimate({**form(), "config_tier": "advanced", "recent_token_limit": "300", "summary_max_tokens": "150"}).get_json()
        self.assertEqual(part(advanced, "recent"), 300)
        self.assertLess(part(advanced, "summary"), part(default, "summary"))
        basic_ignores_summary = self.estimate({**form(), "config_tier": "basic", "memory_level": "medium", "summary_max_tokens": "150"}).get_json()
        self.assertEqual(part(basic_ignores_summary, "summary"), part(default, "summary"), "chế độ Cơ bản bỏ qua ô số của Nâng cao")

    def test_cost_estimate_reflects_fixed_toggles(self):
        result = self.estimate(form()).get_json()
        parts = {c["key"]: c["max"] for c in result["components"]}
        self.assertGreater(parts["rag"], 0, "RAG cố định bật")
        self.assertEqual(parts["memory"], 0, "ghi nhớ thông tin khách cố định tắt")
        self.assertGreater(parts["summary"], 0, "tóm tắt hội thoại cố định bật")
        self.assertIsNotNone(result["summary_job"])

    def test_cost_estimate_invalid_values_report_same_error_as_saving(self):
        response = self.estimate({**form(), "recent_message_limit": "999"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Số tin gần nhất", response.get_json()["error"])
        self.assertEqual(self.estimate(form(low_confidence_reply_mode="hack")).status_code, 422)
        too_long = self.estimate({**form(), "instructions": "x" * (service.MAX_INSTRUCTIONS_CHARS + 1)})
        self.assertEqual(too_long.status_code, 422)

    def test_cost_estimate_requires_csrf(self):
        self.assertEqual(self.estimate(form(), csrf="sai").status_code, 400)
        self.assertEqual(self.estimate(form(), csrf="").status_code, 400)

    def test_cost_estimate_other_teams_bot_is_404(self):
        other_client = self.app.test_client()
        self.login(self.make_team("Team B"), other_client)
        self.assertEqual(self.estimate(form(), client=other_client).status_code, 404)

    def test_cost_estimate_anonymous_is_redirected_to_login(self):
        response = self.estimate(form(), client=self.app.test_client())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_setup_page_renders_cost_box_wired_to_endpoint(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertIn('id="cost-box"', html)
        self.assertIn(f'data-url="/bots/{self.bot.id}/setup/cost-estimate"', html)
        card = html[html.index('id="engine-card"'):html.index('<div class="card-title">Thu thập dữ liệu</div>')]
        self.assertIn('id="cost-box"', card, "ô chi phí nằm trong card Trả lời thông minh")

    def preview(self, payload, csrf="tok"):
        return self.client.post(f"/bots/{self.bot.id}/preview-chat", json=payload, headers={"X-CSRF-Token": csrf})

    def test_preview_chat_returns_reply_and_decision_and_saves_nothing(self):
        from app.models import Message
        from core.context_engine.structured import LLMReply
        from tests.helpers import llm_json, usage

        self.llm.replies.append(LLMReply(llm_json(), usage()))
        response = self.preview({"message": "Giá gói Pro?", "history": [{"role": "customer", "content": "Xin chào"}, {"role": "bot", "content": "Chào bạn"}]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"reply": "Gói Pro giá 500.000đ.", "decision": "answer"})
        self.assertEqual(Message.query.count(), 0)

    def test_preview_chat_validation(self):
        self.assertEqual(self.preview({"message": "  "}).status_code, 400)
        self.assertEqual(self.preview({"message": "x" * (service.MAX_MESSAGE_CHARS + 1)}).status_code, 400)
        self.assertEqual(self.preview({"message": "hi"}, csrf="sai").status_code, 400)

    def test_preview_chat_llm_failure_is_502(self):
        from core.context_engine.structured import LLMReply

        self.llm.replies += [LLMReply("x", None), LLMReply("y", None)]
        self.assertEqual(self.preview({"message": "hi"}).status_code, 502)


if __name__ == "__main__":
    unittest.main()
