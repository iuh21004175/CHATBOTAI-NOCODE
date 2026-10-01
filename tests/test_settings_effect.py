"""Cấu hình Bước 1 (bot_settings) có THỰC SỰ đổi hành vi chatbot không — đi qua đường sản phẩm thật (DB -> reply_to_customer ->
engine) và khẳng định trên thứ quan sát được: tham số của lệnh gọi LLM, nội dung message gửi LLM, tham số truy xuất, quyết định.

Khác test_settings.py (chỉ kiểm tra chuyển cột -> EngineSettings) và test_engine/test_decision (logic nội bộ với EngineSettings dựng
sẵn): ở đây mỗi test đổi đúng 1 cột bot_settings rồi đối chiếu kết quả. Cần DB *_test (xem tests/README.md).

Các công tắc rag_enabled / summary_enabled / structured_memory_enabled / intent_tracking_enabled / slot_filling_enabled /
clarification_enabled, config_tier và max_clarification_turns KHÔNG còn được đọc (core/context_engine/settings.py:FIXED_TOGGLES,
docs/CONTEXT_ENGINE.md) — nhóm FixedTogglesIgnoreStoredValues khẳng định giá trị lưu ở các cột đó không làm đổi hành vi."""
import json
from unittest import mock

from app.models import BotIntentConfig
from core import rag_engine
from core.context_engine import jobs, state as ctx_state
from core.context_engine.settings import DEFAULT_TEMPERATURE, DEFAULTS, JSON_OVERHEAD_TOKENS, EngineSettings
from core.context_engine.structured import LLMReply
from tests.db_case import DbCase
from tests.helpers import llm_json, usage
from tests.test_engine import passage, retrieval


class EffectCase(DbCase):
    """DbCase.setUp thay lệnh gọi DeepSeek và truy xuất bằng bản giả BỎ QUA tham số; ở đây thay bằng bản ghi lại tham số nhận
    được để test khẳng định cấu hình đã chảy xuống đúng chỗ."""

    def setUp(self):
        super().setUp()
        self.llm_params: list[tuple] = []
        self.retrieve_calls: list[tuple[tuple, dict]] = []

        def fake_deepseek_call(temperature, max_tokens):
            self.llm_params.append((temperature, max_tokens))
            return self.llm

        def fake_retrieve(*args, **kwargs):
            self.retrieve_calls.append((args, kwargs))
            return self.retrieval

        for patcher in (
            mock.patch("core.context_engine.engine.deepseek_call", fake_deepseek_call),
            mock.patch.object(rag_engine, "retrieve", fake_retrieve),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def ask(self, question="Giá gói Pro?", conversation=None, **llm_fields):
        """Một lượt khách hỏi qua đường sản phẩm thật; llm_fields = các trường JSON model trả về."""
        conversation = conversation or self.conversation()
        self.llm.replies.append(LLMReply(llm_json(**llm_fields), usage()))
        customer = self.add_message(conversation, "customer", question)
        return self.service.reply_to_customer(self.bot, conversation, customer)

    @property
    def system_prompt(self) -> str:
        return self.llm.calls[-1][0]["content"]

    @property
    def last_user_message(self) -> str:
        return self.llm.calls[-1][-1]["content"]

    def history_sent(self) -> list[str]:
        """Các tin lịch sử (không gồm system và câu hỏi hiện tại) trong lệnh gọi LLM gần nhất."""
        return [m["content"] for m in self.llm.calls[-1][1:-1]]

    def fill_history(self, conversation, count, words=40):
        for i in range(count):
            self.add_message(conversation, "customer" if i % 2 == 0 else "bot", f"tin {i} " + "noi dung dai " * words)


class ModelParametersReachTheLlm(EffectCase):
    def test_max_tokens_is_passed_with_json_overhead_and_temperature_is_the_fixed_default(self):
        self.set_settings(max_tokens=321)
        self.ask()
        self.assertEqual(self.llm_params, [(DEFAULT_TEMPERATURE, 321 + JSON_OVERHEAD_TOKENS)])

    def test_stored_temperature_is_ignored(self):
        for stored in (0.0, 0.2, 1.0):
            self.llm_params.clear()
            self.set_settings(temperature=stored)  # cột còn trong DB nhưng không được đọc
            self.ask()
            self.assertEqual(self.llm_params[0][0], DEFAULT_TEMPERATURE, stored)

    def test_each_bot_uses_its_own_max_tokens(self):
        other_bot = self.service.create_bot(self.team.id, "Bot B")
        self.set_settings(max_tokens=100)
        other = self.service.get_or_create_settings(other_bot)
        other.max_tokens = 900
        self.db.session.commit()
        self.ask()
        conversation = self.conversation(other_bot)
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        customer = self.add_message(conversation, "customer", "hi")
        self.service.reply_to_customer(other_bot, conversation, customer)
        self.assertEqual(self.llm_params, [(DEFAULT_TEMPERATURE, 100 + JSON_OVERHEAD_TOKENS), (DEFAULT_TEMPERATURE, 900 + JSON_OVERHEAD_TOKENS)])

    def test_max_tokens_is_announced_to_the_model_in_the_prompt(self):
        self.set_settings(max_tokens=321)
        self.ask()
        self.assertIn("321", self.system_prompt)

    def test_ai_model_column_is_not_read(self):
        # Model cố định trong core/llm_client.py: cột ai_model chỉ còn để tương thích, đổi nó không đổi lệnh gọi LLM.
        self.set_settings(ai_model="model-khac-hoan-toan")
        self.ask()
        self.assertEqual(self.llm_params, [(0.7, 500 + JSON_OVERHEAD_TOKENS)])


class InstructionsAndLanguageReachThePrompt(EffectCase):
    def test_instructions_are_in_the_system_message(self):
        self.set_settings(instructions="Bạn là trợ lý bán hàng của An Phát, xưng em gọi khách là anh/chị.")
        self.ask()
        self.assertIn("xưng em gọi khách là anh/chị", self.system_prompt)

    def test_changing_instructions_changes_the_next_prompt(self):
        self.set_settings(instructions="CHỈ DẪN CŨ")
        conversation = self.conversation()
        self.ask(conversation=conversation)
        self.set_settings(instructions="CHỈ DẪN MỚI")
        self.ask(conversation=conversation)
        self.assertIn("CHỈ DẪN CŨ", self.llm.calls[0][0]["content"])
        self.assertIn("CHỈ DẪN MỚI", self.llm.calls[1][0]["content"])
        self.assertNotIn("CHỈ DẪN CŨ", self.llm.calls[1][0]["content"])

    def test_language_switches_rules_and_reply_language_reminder(self):
        self.set_settings(language="vi")
        self.ask()
        self.assertIn("Quy tắc trả lời", self.system_prompt)
        self.assertIn("bằng tiếng Việt", self.last_user_message)
        self.set_settings(language="en")
        self.ask()
        self.assertIn("Answering rules", self.system_prompt)
        self.assertIn("Always reply in English", self.last_user_message)

    def test_unknown_language_falls_back_to_vietnamese(self):
        self.set_settings(language="fr")
        self.ask()
        self.assertIn("Quy tắc trả lời", self.system_prompt)


class RecentHistoryBudget(EffectCase):
    def test_recent_message_limit_caps_history(self):
        self.set_settings(recent_message_limit=3)
        conversation = self.conversation()
        self.fill_history(conversation, 10, words=2)
        self.ask(conversation=conversation)
        self.assertEqual(len(self.history_sent()), 3)

    def test_recent_token_limit_caps_history_even_below_message_limit(self):
        conversation = self.conversation()
        self.fill_history(conversation, 10)  # ~ 120 token/tin
        self.set_settings(recent_message_limit=10, recent_token_limit=8000)
        self.ask(conversation=conversation)
        roomy = len(self.history_sent())
        self.set_settings(recent_message_limit=10, recent_token_limit=300)
        self.ask(conversation=conversation)
        tight = len(self.history_sent())
        self.assertEqual(roomy, 10)
        self.assertTrue(0 < tight < roomy, (tight, roomy))


class RetrievalParametersReachTheRetriever(EffectCase):
    def test_default_retrieval_parameters_reach_retrieve(self):
        self.ask("Giá gói Pro?")
        (args, kwargs), = self.retrieve_calls
        self.assertEqual(args, (self.bot.id, "Giá gói Pro?"))
        self.assertEqual((kwargs["top_k"], kwargs["rerank_top_n"], kwargs["distance_threshold"]),
                         (DEFAULTS["rag_top_k"], DEFAULTS["rag_rerank_top_n"], DEFAULTS["rag_distance_threshold"]))

    def test_stored_retrieval_parameters_are_ignored(self):
        # tham số nội bộ RAG không còn cho chủ bot chỉnh: giá trị cũ trong DB (kể cả sai/ngoài khoảng) không đổi việc truy xuất
        for stored in (dict(rag_top_k=12, rag_rerank_top_n=3, rag_distance_threshold=1.2), dict(rag_top_k=999, rag_rerank_top_n=9, rag_distance_threshold=50)):
            for config_tier in ("basic", "advanced"):
                with self.subTest(stored=stored, config_tier=config_tier):
                    self.retrieve_calls.clear()
                    self.set_settings(config_tier=config_tier, **stored)
                    self.ask()
                    kwargs = self.retrieve_calls[0][1]
                    self.assertEqual((kwargs["top_k"], kwargs["rerank_top_n"], kwargs["distance_threshold"]),
                                     (DEFAULTS["rag_top_k"], DEFAULTS["rag_rerank_top_n"], DEFAULTS["rag_distance_threshold"]))

    def test_retrieved_passage_reaches_the_customer_turn(self):
        self.retrieval = retrieval(passages=[passage("Gói Pro giá 500.000đ/tháng, gồm 10 người dùng.")])
        self.ask()
        self.assertIn("Gói Pro giá 500.000đ/tháng", self.last_user_message)


class ContextBudgets(EffectCase):
    def big_passage_retrieval(self):
        return retrieval(passages=[passage("noi dung tai lieu rat dai " * 120)])

    def test_default_rag_budget_answers_a_normal_sized_match_directly(self):
        self.retrieval = self.big_passage_retrieval()
        message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "answer")
        self.assertNotIn("context_exceeds_budget", message.decision_trace["reasons"])

    def test_stored_rag_max_context_tokens_is_ignored(self):
        self.retrieval = self.big_passage_retrieval()
        self.set_settings(rag_max_context_tokens=200)  # cũ: sẽ ép hỏi thu hẹp
        message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "answer")
        self.assertNotIn("context_exceeds_budget", message.decision_trace["reasons"])

    def test_stored_max_context_tokens_is_ignored(self):
        conversation = self.conversation()
        self.fill_history(conversation, 10, words=60)
        self.set_settings(max_context_tokens=2000)  # cũ: sẽ nén mạnh
        trace = self.ask(conversation=conversation).decision_trace
        self.assertEqual(trace["compression_steps"], [])
        self.assertEqual(trace["pressure_level"], "normal")


class DecisionThresholds(EffectCase):
    def test_intent_confidence_threshold_is_the_system_default_and_ignores_stored_value(self):
        fields = dict(intent_confidence=0.6, proposed_clarification_question="Bạn muốn hỏi về gói nào?")
        self.set_settings(intent_confidence_threshold=0.5)  # cũ: sẽ cho qua (0,6 >= 0,5); mặc định hệ thống 0,7 mới áp dụng
        message = self.ask(**fields)
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("low_intent_confidence", message.decision_trace["reasons"])
        self.assertEqual(message.content, "Bạn muốn hỏi về gói nào?")

    def test_slot_completion_threshold_is_the_system_default_and_ignores_stored_value(self):
        self.db.session.add(BotIntentConfig(
            bot_id=self.bot.id, intent_name="ask_price", description="Hỏi giá", required_slots=["product", "budget"],
        ))
        self.db.session.commit()
        fields = dict(intent="ask_price", slots={"product": "Gói Pro", "budget": None}, proposed_clarification_question="Ngân sách của bạn?")
        self.set_settings(slot_completion_threshold=0.4)  # cũ: 1/2 slot đủ = 0,5 >= 0,4 sẽ cho qua; mặc định hệ thống 0,8 mới áp dụng
        message = self.ask(**fields)
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("missing_required_slots", message.decision_trace["reasons"])
        self.assertEqual(message.decision_trace["slot_completion"], 0.5)

    def test_max_candidate_count_is_the_system_default_and_ignores_stored_value(self):
        self.retrieval = retrieval(count=6, gap=0.01)  # 6 nguồn, khoảng cách hạng 1-2 < DISTANCE_GAP_SMALL
        self.set_settings(max_candidate_count=8)  # cũ: 6 nguồn <= 8 sẽ trả lời; mặc định hệ thống 5 mới áp dụng
        message = self.ask(proposed_clarification_question="Bạn cần mục nào?")
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("too_many_relevant_candidates", message.decision_trace["reasons"])

    def test_no_relevant_chunk_uses_the_configured_reply_mode_and_messages(self):
        self.retrieval = retrieval(count=0)
        self.set_settings(
            low_confidence_reply_mode="decline", low_confidence_decline_message="Chưa có thông tin, xin liên hệ 1900.",
            low_confidence_clarify_message="Bạn hỏi rõ hơn được không?",
        )
        declined = self.ask()
        self.set_settings(low_confidence_reply_mode="ask_clarify")
        clarified = self.ask()
        self.assertEqual((declined.decision_trace["decision"], declined.content), ("decline", "Chưa có thông tin, xin liên hệ 1900."))
        self.assertEqual((clarified.decision_trace["decision"], clarified.content), ("clarify", "Bạn hỏi rõ hơn được không?"))

    def test_empty_configured_messages_fall_back_to_language_defaults(self):
        self.retrieval = retrieval(count=0)
        self.set_settings(low_confidence_reply_mode="decline", low_confidence_decline_message="   ", language="en")
        message = self.ask()
        self.assertIn("Sorry", message.content)


class SummarySettings(EffectCase):
    def summary_prompt(self, **stored):
        self.set_settings(recent_message_limit=2, **stored)
        conversation = self.conversation()
        self.fill_history(conversation, 8, words=2)
        state = ctx_state.get_or_create_state(conversation)
        state.summary_pending = True
        self.db.session.commit()
        seen = []

        def call(messages):
            seen.append(messages)
            return LLMReply("Tóm tắt.", usage(300, 20))

        settings = EngineSettings.from_model(self.service.get_or_create_settings(self.bot))
        self.assertTrue(jobs.summarize_conversation(state, settings, call=call))
        return json.dumps(seen[0], ensure_ascii=False)

    def test_summary_max_tokens_reaches_the_summary_prompt_in_advanced_mode(self):
        self.assertIn("777", self.summary_prompt(config_tier="advanced", summary_max_tokens=777))

    def test_summary_max_tokens_is_ignored_in_basic_mode(self):
        prompt = self.summary_prompt(config_tier="basic", summary_max_tokens=777)
        self.assertNotIn("777", prompt)
        self.assertIn(str(DEFAULTS["summary_max_tokens"]), prompt)


class FixedTogglesIgnoreStoredValues(EffectCase):
    """Cột công tắc vẫn còn trong bảng nhưng KHÔNG được đọc: cấu hình cũ trong DB không được làm tắt tính năng ngoài ý muốn."""

    def test_knowledge_base_stays_on_when_rag_enabled_is_stored_false(self):
        self.set_settings(rag_enabled=False)
        self.retrieval = retrieval(passages=[passage("Gói Pro giá 500.000đ/tháng")])
        self.ask()
        self.assertEqual(len(self.retrieve_calls), 1, "vẫn tra cứu tài liệu")
        self.assertIn("Gói Pro giá 500.000đ/tháng", self.last_user_message)

    def test_clarification_stays_on_when_clarification_enabled_is_stored_false(self):
        self.set_settings(clarification_enabled=False)
        message = self.ask(intent_confidence=0.2, proposed_clarification_question="Bạn nói rõ hơn được không?")
        self.assertEqual(message.decision_trace["decision"], "clarify")

    def test_no_clarification_cap_regardless_of_max_clarification_turns(self):
        self.set_settings(max_clarification_turns=1)
        conversation = self.conversation()
        decisions = [
            self.ask(q, conversation=conversation, intent_confidence=0.2, proposed_clarification_question="Rõ hơn?").decision_trace["decision"]
            for q in ("a", "b", "c")
        ]
        self.assertEqual(decisions, ["clarify"] * 3)

    def test_intent_tracking_and_slot_filling_stay_on_when_stored_false(self):
        self.set_settings(intent_tracking_enabled=False, slot_filling_enabled=False)
        message = self.ask(intent_confidence=0.2, proposed_clarification_question="Bạn cần gì ạ?")
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("low_intent_confidence", message.decision_trace["reasons"])

    def test_config_tier_does_not_hide_any_setting(self):
        self.set_settings(config_tier="basic", recent_message_limit=3)
        conversation = self.conversation()
        self.fill_history(conversation, 10, words=2)
        self.ask(conversation=conversation)
        self.assertEqual(len(self.history_sent()), 3, "mức cấu hình basic không còn ép recent_message_limit về mặc định")

    def test_structured_memory_stays_off_when_stored_true(self):
        from app.models import StructuredMemory

        self.set_settings(structured_memory_enabled=True)
        self.ask(memory_updates=[{"category": "entity", "key": "tên", "value": "Nam", "confidence": 0.99}])
        self.assertEqual(StructuredMemory.query.count(), 0)


class WidgetFacingSettings(EffectCase):
    """Lời chào và chữ giao diện của widget lấy từ cấu hình Bước 1 (widget_service.get_config = payload /config thật)."""

    def setUp(self):
        super().setUp()
        from app.widget import service as widget_service

        self.widget = widget_service

    def test_greeting_and_language_reach_the_widget_config(self):
        self.set_settings(greeting="Chào mừng đến An Phát!", language="vi")
        config = self.widget.get_config(self.bot)
        self.assertEqual((config["greeting"], config["language"], config["send"]), ("Chào mừng đến An Phát!", "vi", "Gửi"))
        self.set_settings(greeting="Welcome!", language="en")
        config = self.widget.get_config(self.bot)
        self.assertEqual((config["greeting"], config["language"], config["send"]), ("Welcome!", "en", "Send"))

    def test_empty_greeting_falls_back_to_the_language_default(self):
        self.set_settings(greeting="", language="en")
        self.assertEqual(self.widget.get_config(self.bot)["greeting"], "Hello! How can I help you?")

    def test_bot_name_reaches_the_widget_config(self):
        self.assertEqual(self.widget.get_config(self.bot)["name"], "Bot A")


class ChunkSettings(EffectCase):
    """Cấu hình chunk của trợ lý (Bước 2) là mặc định cho tài liệu chưa có cấu hình riêng; cấu hình riêng của tài liệu thắng."""

    def make_document(self, **kw):
        from app.models import Document

        document = Document(bot_id=self.bot.id, filename="a.md", storage_path="x/a.md", **kw)
        self.db.session.add(document)
        self.db.session.commit()
        return document

    def test_bot_chunk_settings_are_the_default_for_documents(self):
        self.set_settings(chunk_size=300, chunk_overlap=40)
        self.assertEqual(self.service.chunk_params_for(self.bot, self.make_document()), (300, 40))

    def test_document_level_chunk_settings_override_the_bot(self):
        self.set_settings(chunk_size=300, chunk_overlap=40)
        self.assertEqual(self.service.chunk_params_for(self.bot, self.make_document(chunk_size=700, chunk_overlap=90)), (700, 90))


if __name__ == "__main__":
    import unittest

    unittest.main()
