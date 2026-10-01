"""A3 ở chế độ AI Agent: cấu hình Bước 1 (bot_settings) có THỰC SỰ đổi hành vi khi lượt trả lời đi qua agent không — cùng đường sản phẩm thật
(DB -> reply_to_customer -> engine -> AgentRunner) như tests/test_settings_effect.py, nhưng khẳng định trên thứ agent NHẬN (persona + input
dựng từ Context Builder) và quyết định cuối. Runner GIẢ (không worker/dsh). Không có ca `temperature`: Harness không truyền được tham số này
(đã chốt bỏ). Cần DB *_test (xem tests/README.md).

Kèm các ca của quyết định kiến trúc 2026-09-26: bộ nhớ hội thoại lấy từ DB (không dùng session của Harness) và kết quả truy xuất BAN ĐẦU
vẫn được đưa vào prompt đầu tiên."""
import dataclasses
from unittest import mock

from app.models import BotIntentConfig
from core import rag_engine
from core.context_engine.agent import protocol as p
from core.context_engine.agent import runtime as rt
from core.context_engine.settings import DEFAULTS
from tests.db_case import DbCase
from tests.test_agent_runtime import FakeAgentRunner
from tests.test_engine import passage, retrieval

ANSWER = ("finish_answer", {"answer": "Gói Pro 500.000đ/tháng", "intent_confidence": 0.95})


class AgentEffectCase(DbCase):
    def setUp(self):
        super().setUp()
        self.retrieve_calls: list[tuple[tuple, dict]] = []

        def fake_retrieve(*args, **kwargs):
            self.retrieve_calls.append((args, kwargs))
            return self.retrieval

        patcher = mock.patch.object(rag_engine, "retrieve", fake_retrieve)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runner = FakeAgentRunner(ANSWER)
        patcher = mock.patch("app.dashboard.service.make_agent_runner", lambda: self.runner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, question="Giá gói Pro?", conversation=None, terminal=ANSWER):
        self.runner.terminal = terminal
        conversation = conversation or self.conversation()
        customer = self.add_message(conversation, "customer", question)
        return self.service.reply_to_customer(self.bot, conversation, customer)

    def call(self, index=-1) -> dict:
        return self.runner.calls[index]

    def persona(self, index=-1) -> str:
        call = self.call(index)
        return rt.render_persona(call["plan"].settings, call["intents"])

    def agent_input(self, index=-1) -> str:
        return rt.render_input(self.call(index)["plan"])

    def fill_history(self, conversation, count, words=40):
        for i in range(count):
            self.add_message(conversation, "customer" if i % 2 == 0 else "bot", f"tin {i} " + "noi dung dai " * words)


class ConversationMemoryComesFromTheDatabase(AgentEffectCase):
    """Quyết định 1.2: mỗi lượt là 1 phiên Harness độc lập (bị xóa sau lượt) nên ngữ cảnh PHẢI dựng lại từ bảng Message mỗi lượt."""

    def test_second_turn_input_carries_the_first_turn_read_from_the_db(self):
        conversation = self.conversation()
        self.ask("Tên tôi là Lan", conversation, terminal=("finish_answer", {"answer": "Chào chị Lan ạ", "intent_confidence": 0.9}))
        self.ask("Bạn nhớ tên tôi không?", conversation)
        first, second = self.runner.calls
        self.assertEqual(first["plan"].recent, [])
        text = self.agent_input(1)
        self.assertIn("Tên tôi là Lan", text)
        self.assertIn("Chào chị Lan ạ", text)
        self.assertLess(text.index("Tên tôi là Lan"), text.index("Bạn nhớ tên tôi không?"), "lịch sử đứng trước câu hỏi hiện tại")

    def test_history_is_not_in_the_persona(self):
        conversation = self.conversation()
        self.ask("Tên tôi là Lan", conversation)
        self.ask("Bạn nhớ tên tôi không?", conversation)
        self.assertNotIn("Tên tôi là Lan", self.persona(1))

    def test_history_of_another_conversation_never_leaks(self):
        self.ask("Bí mật của khách A: 12345")
        self.ask("Xin chào")  # hội thoại MỚI (self.conversation() tạo mới mỗi lần)
        self.assertNotIn("12345", self.agent_input(1))

    def test_job_carries_no_harness_session_identity_of_the_conversation(self):
        fields = {f.name for f in dataclasses.fields(p.AgentJob)}
        self.assertFalse({"conversation_id", "session_id", "visitor_id"} & fields, fields)

    def test_bot_message_row_is_still_stored_for_display_and_audit(self):
        conversation = self.conversation()
        message = self.ask("Giá gói Pro?", conversation)
        self.assertEqual((message.sender, message.conversation_id), ("bot", conversation.id))


class InitialRetrievalStillReachesTheAgent(AgentEffectCase):
    """Quyết định 1.4: truy xuất ban đầu (rẻ, không qua LLM) vẫn vào prompt đầu tiên; agent chỉ tra cứu thêm khi cần."""

    def test_retrieved_passage_is_in_the_first_prompt(self):
        self.retrieval = retrieval(passages=[passage("Gói Pro giá 500.000đ/tháng, gồm 10 người dùng.")])
        self.ask()
        self.assertIn("Gói Pro giá 500.000đ/tháng", self.agent_input())
        self.assertEqual(len(self.retrieve_calls), 1, "đúng 1 lần truy xuất ban đầu trước khi giao cho agent")

    def test_no_match_says_so_instead_of_inventing_context(self):
        self.retrieval = retrieval(count=0)
        self.set_settings(low_confidence_reply_mode="decline")
        self.ask()
        self.assertEqual(len(self.runner.calls), 1, "agent vẫn được chạy (có thể tự tra cứu thêm); chốt an toàn nằm ở cây quyết định sau đó")
        self.assertIn(rt.texts("vi")["no_context"], self.agent_input())

    def test_search_tool_is_the_only_extra_way_to_get_context(self):
        self.assertIn(p.SEARCH_TOOL, p.MODEL_TOOL_NAMES)
        # ngoài tra cứu chỉ có công cụ tóm tắt hội thoại (không lấy thêm dữ liệu ngoài hội thoại này) và các công cụ kết thúc
        self.assertEqual(set(p.MODEL_TOOL_NAMES) - {p.SEARCH_TOOL, p.SUMMARIZE_TOOL}, set(p.TERMINAL_TOOLS))


class InstructionsAndLanguageReachTheAgent(AgentEffectCase):
    def test_instructions_are_in_the_persona(self):
        self.set_settings(instructions="Bạn là trợ lý bán hàng của An Phát, xưng em gọi khách là anh/chị.")
        self.ask()
        self.assertIn("xưng em gọi khách là anh/chị", self.persona())

    def test_changing_instructions_changes_the_next_persona(self):
        self.set_settings(instructions="CHỈ DẪN CŨ")
        conversation = self.conversation()
        self.ask(conversation=conversation)
        self.set_settings(instructions="CHỈ DẪN MỚI")
        self.ask(conversation=conversation)
        self.assertIn("CHỈ DẪN CŨ", self.persona(0))
        self.assertIn("CHỈ DẪN MỚI", self.persona(1))
        self.assertNotIn("CHỈ DẪN CŨ", self.persona(1))

    def test_language_switches_rules_and_reply_language_reminder(self):
        self.set_settings(language="vi")
        self.ask()
        self.assertIn("Quy tắc trả lời", self.persona())
        self.assertIn("bằng tiếng Việt", self.agent_input())
        self.set_settings(language="en")
        self.ask()
        self.assertIn("Answering rules", self.persona())
        self.assertIn("must be in English", self.agent_input())

    def test_unknown_language_falls_back_to_vietnamese(self):
        self.set_settings(language="fr")
        self.ask()
        self.assertIn("Quy tắc trả lời", self.persona())

    def test_persona_is_identical_across_turns_of_the_same_settings(self):
        # process_key của dsh dựa trên persona: persona chứa dữ liệu của lượt sẽ khiến mỗi lượt đẻ 1 tiến trình dsh mới
        conversation = self.conversation()
        self.ask("câu 1", conversation)
        self.ask("câu 2", conversation)
        self.assertEqual(self.persona(0), self.persona(1))


class RecentHistoryBudget(AgentEffectCase):
    def test_recent_message_limit_caps_history(self):
        self.set_settings(recent_message_limit=3)
        conversation = self.conversation()
        self.fill_history(conversation, 10, words=2)
        self.ask(conversation=conversation)
        self.assertEqual(len(self.call()["plan"].recent), 3)

    def test_recent_token_limit_caps_history_even_below_message_limit(self):
        conversation = self.conversation()
        self.fill_history(conversation, 10)
        self.set_settings(recent_message_limit=10, recent_token_limit=8000)
        self.ask(conversation=conversation)
        roomy = len(self.call()["plan"].recent)
        self.set_settings(recent_message_limit=10, recent_token_limit=300)
        self.ask(conversation=conversation)
        tight = len(self.call()["plan"].recent)
        self.assertEqual(roomy, 10)
        self.assertTrue(0 < tight < roomy, (tight, roomy))


class RetrievalParametersReachTheRetriever(AgentEffectCase):
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


class ContextBudgets(AgentEffectCase):
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


class DecisionThresholdsStillGovernTheAgent(AgentEffectCase):
    """Cây quyết định chạy TIẾP TRÊN đầu ra của agent: ngưỡng của chủ bot thắng ý muốn của agent."""

    def test_intent_confidence_threshold_is_the_system_default_and_ignores_stored_value(self):
        terminal = ("finish_answer", {"answer": "Gói Pro 500.000đ", "intent_confidence": 0.6})
        self.set_settings(intent_confidence_threshold=0.5)  # cũ: sẽ cho qua (0,6 >= 0,5); mặc định hệ thống 0,7 mới áp dụng
        message = self.ask(terminal=terminal)
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("low_intent_confidence", message.decision_trace["reasons"])

    def test_slot_completion_threshold_is_the_system_default_and_ignores_stored_value(self):
        self.db.session.add(BotIntentConfig(bot_id=self.bot.id, intent_name="ask_price", description="Hỏi giá", required_slots=["product", "budget"]))
        self.db.session.commit()
        terminal = ("finish_answer", {"answer": "ok", "intent": "ask_price", "intent_confidence": 0.95, "slots": {"product": "Gói Pro", "budget": None}})
        self.set_settings(slot_completion_threshold=0.4)  # cũ: 1/2 slot đủ = 0,5 >= 0,4 sẽ cho qua; mặc định hệ thống 0,8 mới áp dụng
        message = self.ask(terminal=terminal)
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("missing_required_slots", message.decision_trace["reasons"])

    def test_max_candidate_count_is_the_system_default_and_ignores_stored_value(self):
        self.retrieval = retrieval(count=6, gap=0.01)
        self.set_settings(max_candidate_count=8)  # cũ: 6 nguồn <= 8 sẽ trả lời; mặc định hệ thống 5 mới áp dụng
        message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("too_many_relevant_candidates", message.decision_trace["reasons"])

    def test_agent_cannot_answer_when_no_document_matches_anywhere(self):
        # điều kiện an toàn cốt lõi: agent "muốn" bịa khuyến mãi nhưng KHÔNG có tài liệu nào (ban đầu lẫn qua tra cứu thêm) -> câu của chủ bot
        self.retrieval = retrieval(count=0)
        self.set_settings(low_confidence_reply_mode="decline", low_confidence_decline_message="Chưa có thông tin, xin liên hệ 1900.")
        message = self.ask("Khuyến mãi 90% là gì?", terminal=("finish_answer", {"answer": "Giảm 90% toàn bộ!", "intent_confidence": 0.99}))
        self.assertEqual((message.decision_trace["decision"], message.content), ("decline", "Chưa có thông tin, xin liên hệ 1900."))

    def test_no_relevant_chunk_uses_the_configured_reply_mode_and_messages(self):
        self.retrieval = retrieval(count=0)
        self.set_settings(low_confidence_reply_mode="ask_clarify", low_confidence_clarify_message="Bạn hỏi rõ hơn được không?")
        message = self.ask()
        self.assertEqual((message.decision_trace["decision"], message.content), ("clarify", "Bạn hỏi rõ hơn được không?"))
