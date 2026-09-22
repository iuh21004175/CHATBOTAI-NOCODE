"""Bước 1 — cấu hình Decision Engine theo tier: kiểm tra form (thuần) và lưu/hiển thị qua route thật (DB *_test)."""
import unittest

from app.dashboard import service
from core.context_engine.settings import DEFAULTS
from tests.db_case import DbCase


def form(tier, **fields):
    data = {"config_tier": tier}
    data.update({k: (v if isinstance(v, str) else str(v)) for k, v in fields.items() if v is not None})
    return data


class ParseEngineForm(unittest.TestCase):
    def test_invalid_tier(self):
        for tier in (None, "", "root", "BASIC"):
            values, error = service.parse_engine_form({"config_tier": tier} if tier is not None else {})
            self.assertIsNone(values)
            self.assertIn("không hợp lệ", error)

    def test_basic_reads_only_three_switches(self):
        values, error = service.parse_engine_form(form("basic", memory_enabled="on", clarification_enabled="on", rag_top_k=15, max_candidate_count=9))
        self.assertIsNone(error)
        self.assertEqual(values, {
            "config_tier": "basic", "rag_enabled": False, "clarification_enabled": True,
            "structured_memory_enabled": True, "summary_enabled": True,
        })

    def test_memory_switch_controls_both_memory_and_summary_in_basic_and_advanced(self):
        for tier in ("basic", "advanced"):
            on, _ = service.parse_engine_form(form(tier, memory_enabled="on"))
            off, _ = service.parse_engine_form(form(tier))
            self.assertEqual((on["structured_memory_enabled"], on["summary_enabled"]), (True, True), tier)
            self.assertEqual((off["structured_memory_enabled"], off["summary_enabled"]), (False, False), tier)

    def test_expert_has_separate_memory_and_summary_switches(self):
        values, _ = service.parse_engine_form(form("expert", structured_memory_enabled="on", low_confidence_reply_mode="decline"))
        self.assertEqual((values["structured_memory_enabled"], values["summary_enabled"]), (True, False))

    def test_advanced_reads_its_fields_and_ignores_expert_only(self):
        values, error = service.parse_engine_form(form("advanced", recent_message_limit=20, rag_distance_threshold="1.25", max_candidate_count=9))
        self.assertIsNone(error)
        self.assertEqual((values["recent_message_limit"], values["rag_distance_threshold"]), (20, 1.25))
        self.assertNotIn("max_candidate_count", values)

    def test_blank_or_missing_numbers_keep_stored_value(self):
        values, _ = service.parse_engine_form(form("advanced", recent_message_limit="  ", rag_top_k=None))
        self.assertNotIn("recent_message_limit", values)
        self.assertNotIn("rag_top_k", values)

    def test_out_of_range_and_bad_numbers_are_rejected_not_clamped(self):
        cases = {
            "recent_message_limit": ["0", "31", "abc", "1.5", "-3"],
            "rag_distance_threshold": ["0.1", "1.95", "nan", "inf", "x"],
            "rag_top_k": ["21", "0"],
            "max_clarification_turns": ["6", "-1"],
        }
        for name, bad_values in cases.items():
            for bad in bad_values:
                with self.subTest(name=name, value=bad):
                    values, error = service.parse_engine_form(form("advanced", **{name: bad}))
                    self.assertIsNone(values)
                    self.assertTrue(error)

    def test_boundaries_are_accepted(self):
        for name, (low, high) in {"recent_message_limit": (1, 30), "rag_distance_threshold": (0.2, 1.9), "max_clarification_turns": (0, 5)}.items():
            for value in (low, high):
                values, error = service.parse_engine_form(form("advanced", **{name: value}))
                self.assertIsNone(error, (name, value))

    def test_expert_validation(self):
        base = {"low_confidence_reply_mode": "ask_clarify"}
        self.assertTrue(service.parse_engine_form(form("expert", low_confidence_reply_mode="explode"))[1])
        self.assertTrue(service.parse_engine_form(form("expert", **base, low_confidence_decline_message="x" * 501))[1])
        self.assertTrue(service.parse_engine_form(form("expert", **base, context_pressure_warning=0.9, context_pressure_hard_limit=0.8))[1])
        values, error = service.parse_engine_form(form("expert", **base, low_confidence_clarify_message="  Bạn nói rõ hơn?  ", low_confidence_decline_message=" "))
        self.assertIsNone(error)
        self.assertEqual(values["low_confidence_clarify_message"], "Bạn nói rõ hơn?")
        self.assertIsNone(values["low_confidence_decline_message"], "để trống = dùng câu mặc định")


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
        payload = {"csrf_token": "tok", "name": "Bot A", **data}
        return self.client.post(f"/bots/{(bot or self.bot).id}/setup", data=payload, **kw)

    def settings(self):
        self.db.session.expire_all()
        return self.service.get_or_create_settings(self.bot)

    def test_new_bot_defaults(self):
        s = self.settings()
        self.assertEqual(s.config_tier, "basic")
        for name, default in DEFAULTS.items():
            self.assertEqual(getattr(s, name), default, name)

    def test_get_renders_tier_selector_and_fields(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertIn('id="config_tier"', html)
        self.assertIn('name="rag_distance_threshold"', html)
        self.assertIn('name="low_confidence_reply_mode"', html)
        self.assertNotIn('name="min_similarity"', html, "slider cosine cũ đã được thay bằng ngưỡng khoảng cách")

    def test_model_settings_are_merged_into_smart_reply_card(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertNotIn("Cấu hình mô hình AI", html, "đã gộp vào card Trả lời thông minh")
        card = html[html.index('id="engine-card"'):html.index("Chuyển tiếp &amp; thu thập dữ liệu")]
        for field_id in ("language", "max_tokens", "temperature", "config_tier"):
            self.assertIn(f'id="{field_id}"', card, field_id)
        self.assertEqual(html.count('id="max_tokens"'), 1)
        self.assertEqual(html.count('id="temperature"'), 1)

    def test_labels_show_no_static_values(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        for label in ("Token đầu ra tối đa", "Độ sáng tạo (temperature)", "Ngưỡng khoảng cách tra cứu"):
            self.assertIn(f'>{label}</label>', html, f"nhãn '{label}' không kèm giá trị")
        for old_id in ("max-tokens-value", "temp-value"):
            self.assertNotIn(old_id, html)

    def test_every_engine_field_has_tooltip_and_slider_shows_live_value_outside_label(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        for spec in service.ENGINE_FIELDS:
            self.assertTrue(spec.get("help"), f"{spec['name']} thiếu mô tả tác dụng")
            self.assertIn(f'id="tip-{spec["name"]}"', html, spec["name"])
            self.assertIn(f'aria-describedby="tip-{spec["name"]}"', html, spec["name"])
        for name in ("max_tokens", "temperature", "rag_distance_threshold"):
            self.assertIn(f'<output class="slider-val" id="{name}-value"', html, name)

    def test_tier_descriptions_are_rendered_for_selected_tier_and_in_tooltip(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        by_value = {t["value"]: t for t in service.ENGINE_TIER_INFO}
        self.assertEqual(set(by_value), {"basic", "advanced", "expert"})
        desc = html[html.index('id="tier-desc"'):]
        self.assertTrue(desc[desc.index(">") + 1:].startswith(by_value["basic"]["desc"]), "bot mới ở mức Cơ bản")
        tooltip = html[html.index('id="tip-config_tier"'):html.index('id="config_tier"')]
        for info in by_value.values():
            self.assertIn(f'<b>{info["name"]}:</b>', tooltip)

    def test_basic_save(self):
        response = self.post(form("basic", memory_enabled="on", rag_top_k=15))
        self.assertEqual(response.status_code, 302)
        s = self.settings()
        self.assertEqual((s.config_tier, s.rag_enabled, s.clarification_enabled), ("basic", False, False))
        self.assertEqual((s.structured_memory_enabled, s.summary_enabled), (True, True))
        self.assertEqual(s.rag_top_k, DEFAULTS["rag_top_k"], "trường tier cao hơn bị bỏ qua dù có trong form")

    def test_advanced_save(self):
        self.post(form("advanced", memory_enabled="on", rag_enabled="on", recent_message_limit=20, rag_distance_threshold="1.2",
                       max_clarification_turns=3, max_candidate_count=9))
        s = self.settings()
        self.assertEqual((s.config_tier, s.recent_message_limit, s.rag_distance_threshold, s.max_clarification_turns), ("advanced", 20, 1.2, 3))
        self.assertEqual(s.max_candidate_count, DEFAULTS["max_candidate_count"])

    def test_expert_save(self):
        self.post(form("expert", rag_enabled="on", clarification_enabled="on", structured_memory_enabled="on", low_confidence_reply_mode="decline",
                       low_confidence_decline_message="Chưa có thông tin, xin liên hệ 1900.", max_candidate_count=7, context_pressure_warning=0.7, context_pressure_hard_limit=0.85))
        s = self.settings()
        self.assertEqual((s.config_tier, s.low_confidence_reply_mode, s.max_candidate_count), ("expert", "decline", 7))
        self.assertEqual(s.low_confidence_decline_message, "Chưa có thông tin, xin liên hệ 1900.")
        self.assertEqual((s.structured_memory_enabled, s.summary_enabled), (True, False))

    def test_invalid_value_saves_nothing_and_shows_error(self):
        response = self.post(form("advanced", rag_enabled="on", recent_message_limit=999), )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Số tin gần nhất", response.get_data(as_text=True))
        s = self.settings()
        self.assertEqual(s.config_tier, "basic")
        self.assertEqual(s.recent_message_limit, DEFAULTS["recent_message_limit"])
        self.assertTrue(s.rag_enabled)

    def test_invalid_value_does_not_rename_the_bot(self):
        self.client.post(f"/bots/{self.bot.id}/setup", data={"csrf_token": "tok", "name": "Tên mới", **form("advanced", recent_message_limit=999)})
        self.db.session.expire_all()
        self.assertEqual(self.db.session.get(type(self.bot), self.bot.id).name, "Bot A")

    def test_missing_or_wrong_csrf_saves_nothing(self):
        self.client.post(f"/bots/{self.bot.id}/setup", data={"name": "X", **form("expert", low_confidence_reply_mode="decline")})
        self.assertEqual(self.settings().config_tier, "basic")
        self.client.post(f"/bots/{self.bot.id}/setup", data={"csrf_token": "sai", "name": "X", **form("expert", low_confidence_reply_mode="decline")})
        self.assertEqual(self.settings().config_tier, "basic")

    def test_legacy_min_similarity_field_is_ignored(self):
        before = self.settings().min_similarity
        self.assertEqual(self.post(form("basic", min_similarity="0.55")).status_code, 302)
        self.assertEqual(self.settings().min_similarity, before)

    def test_other_teams_bot_is_404_and_untouched(self):
        other_team = self.make_team("Team B")
        other_client = self.app.test_client()
        self.login(other_team, other_client)
        response = other_client.post(f"/bots/{self.bot.id}/setup", data={"csrf_token": "tok", "name": "Hack", **form("expert", low_confidence_reply_mode="decline")})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(other_client.get(f"/bots/{self.bot.id}/setup").status_code, 404)
        self.assertEqual(self.settings().config_tier, "basic")

    def test_anonymous_is_redirected_to_login(self):
        anonymous = self.app.test_client()
        response = anonymous.post(f"/bots/{self.bot.id}/setup", data=form("expert"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def estimate(self, data, csrf="tok", client=None, bot=None):
        return (client or self.client).post(f"/bots/{(bot or self.bot).id}/setup/cost-estimate", data=data, headers={"X-CSRF-Token": csrf})

    def test_cost_estimate_uses_unsaved_form_values_and_saves_nothing(self):
        small = self.estimate({**form("basic"), "max_tokens": "200", "temperature": "0.3", "language": "vi"})
        big = self.estimate({**form("basic"), "max_tokens": "2500", "temperature": "0.3", "language": "vi"})
        self.assertEqual((small.status_code, big.status_code), (200, 200))
        s, b = small.get_json()["question"], big.get_json()["question"]
        self.assertLess(s["max"]["vnd"]["off_peak"], b["max"]["vnd"]["off_peak"])
        self.assertEqual(s["min"], b["min"])
        self.assertLess(s["min"]["vnd"]["off_peak"], s["max"]["vnd"]["off_peak"])
        self.assertLess(s["max"]["vnd"]["off_peak"], s["max"]["vnd"]["peak"])
        saved = self.settings()
        self.assertEqual((saved.max_tokens, saved.config_tier), (500, "basic"), "chỉ ước tính, không lưu")

    def test_cost_estimate_follows_instructions_and_language(self):
        base = self.estimate({**form("basic"), "instructions": "Ngắn."}).get_json()
        long = self.estimate({**form("basic"), "instructions": "Chỉ dẫn rất dài. " * 200}).get_json()
        self.assertGreater(long["question"]["min"]["input"], base["question"]["min"]["input"])
        en = self.estimate({**form("basic"), "instructions": "Ngắn.", "language": "en"}).get_json()
        self.assertNotEqual(en["question"]["min"]["input"], base["question"]["min"]["input"])

    def test_cost_estimate_respects_tier_rules_like_saving(self):
        default = self.estimate({**form("basic"), "rag_enabled": "on"}).get_json()
        basic_ignores_advanced_field = self.estimate({**form("basic"), "rag_enabled": "on", "rag_max_context_tokens": "200"}).get_json()
        self.assertEqual(basic_ignores_advanced_field["question"], default["question"])
        advanced = self.estimate({**form("advanced"), "rag_enabled": "on", "rag_max_context_tokens": "200"}).get_json()
        rag = next(c for c in advanced["components"] if c["key"] == "rag")
        self.assertEqual(rag["max"], 200)
        self.assertLess(advanced["question"]["max"]["input"], default["question"]["max"]["input"])

    def test_cost_estimate_switches_change_prompt_parts(self):
        off = self.estimate(form("basic")).get_json()  # công tắc không gửi = tắt
        on = self.estimate({**form("basic"), "rag_enabled": "on", "memory_enabled": "on"}).get_json()
        parts = lambda r: {c["key"]: c["max"] for c in r["components"]}
        self.assertEqual((parts(off)["rag"], parts(off)["memory"], parts(off)["summary"]), (0, 0, 0))
        self.assertGreater(parts(on)["rag"], 0)
        self.assertGreater(parts(on)["memory"], 0)
        self.assertGreater(parts(on)["summary"], 0)
        self.assertIsNone(off["summary_job"])
        self.assertIsNotNone(on["summary_job"])

    def test_cost_estimate_invalid_values_report_same_error_as_saving(self):
        response = self.estimate({**form("advanced"), "recent_message_limit": "999"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("Số tin gần nhất", response.get_json()["error"])
        self.assertEqual(self.estimate({"config_tier": "hack"}).status_code, 422)
        too_long = self.estimate({**form("basic"), "instructions": "x" * (service.MAX_INSTRUCTIONS_CHARS + 1)})
        self.assertEqual(too_long.status_code, 422)

    def test_cost_estimate_requires_csrf(self):
        self.assertEqual(self.estimate(form("basic"), csrf="sai").status_code, 400)
        self.assertEqual(self.estimate(form("basic"), csrf="").status_code, 400)

    def test_cost_estimate_other_teams_bot_is_404(self):
        other_client = self.app.test_client()
        self.login(self.make_team("Team B"), other_client)
        self.assertEqual(self.estimate(form("basic"), client=other_client).status_code, 404)

    def test_cost_estimate_anonymous_is_redirected_to_login(self):
        response = self.estimate(form("basic"), client=self.app.test_client())
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_setup_page_renders_cost_box_wired_to_endpoint(self):
        html = self.client.get(f"/bots/{self.bot.id}/setup").get_data(as_text=True)
        self.assertIn('id="cost-box"', html)
        self.assertIn(f'data-url="/bots/{self.bot.id}/setup/cost-estimate"', html)
        card = html[html.index('id="engine-card"'):html.index("Chuyển tiếp &amp; thu thập dữ liệu")]
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
