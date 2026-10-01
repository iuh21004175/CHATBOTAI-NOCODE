"""Luồng trả lời khách ở chế độ AI Agent qua đường sản phẩm thật (widget -> reply_to_customer -> engine -> DB): bản ghi agent_executions cho
MỌI lượt (kể cả lỗi), usage cộng dồn vào tin bot, slot -> Customer, lỗi không tạo câu trả lời giả. Runner GIẢ (không worker, không dsh).
Cần DB *_test (xem tests/README.md)."""
import json
import os
from unittest import mock

from app.models import AgentExecution, Conversation, ConversationState, Customer, Message
from config import Config
from core.context_engine.agent import protocol as p
from core.context_engine.agent import runtime as rt
from tests.db_case import DbCase
from tests.test_agent_runtime import FakeAgentRunner


class AgentFlowCase(DbCase):
    def use_agent(self, runner):
        patcher = mock.patch("app.dashboard.service.make_agent_runner", lambda: runner)
        patcher.start()
        self.addCleanup(patcher.stop)
        return runner

    def ask(self, question="Giá gói Pro?", conversation=None):
        conversation = conversation or self.conversation()
        customer = self.add_message(conversation, "customer", question)
        return conversation, self.service.reply_to_customer(self.bot, conversation, customer)

    def executions(self):
        self.db.session.expire_all()
        return AgentExecution.query.order_by(AgentExecution.id).all()


class Records(AgentFlowCase):
    def test_a_turn_writes_the_reply_the_trace_the_usage_and_an_execution_row(self):
        self.use_agent(FakeAgentRunner(("finish_answer", {"answer": "Gói Pro 500.000đ/tháng", "intent": "ask_price", "intent_confidence": 0.95}), steps=3))
        conversation, message = self.ask()
        self.assertEqual((message.sender, message.content), ("bot", "Gói Pro 500.000đ/tháng"))
        self.assertEqual(message.decision_trace["decision"], "answer")
        self.assertEqual(message.decision_trace["agent"]["execution_id"], "job-x")
        self.assertEqual((message.usage_prompt_tokens, message.usage_cache_hit_tokens, message.usage_completion_tokens), (1500, 900, 120))
        (row,) = self.executions()
        self.assertEqual((row.job_id, row.bot_id, row.conversation_id, row.message_id), ("job-x", self.bot.id, conversation.id, message.id))
        self.assertEqual((row.status, row.iterations_used, row.total_llm_calls, row.tool_calls_used, row.stop_reason), ("completed", 3, 3, 1, "completed"))
        self.assertIsNotNone(row.started_at)
        self.assertIsNotNone(row.finished_at)
        self.assertIsNone(row.error_message)

    def test_old_json_llm_is_not_called_in_agent_mode(self):
        self.use_agent(FakeAgentRunner(("finish_answer", {"answer": "ok", "intent_confidence": 0.9})))
        self.ask()
        self.assertEqual(self.llm.calls, [])

    def test_agent_mode_off_keeps_the_old_path_and_writes_no_execution(self):
        from tests.helpers import llm_json, usage
        from core.context_engine.structured import LLMReply

        self.llm.replies.append(LLMReply(llm_json(), usage()))
        _, message = self.ask()
        self.assertEqual(message.content, "Gói Pro giá 500.000đ.")
        self.assertEqual(self.executions(), [])
        self.assertNotIn("agent", message.decision_trace)

    def test_every_turn_gets_its_own_row(self):
        self.use_agent(FakeAgentRunner(("finish_answer", {"answer": "ok", "intent_confidence": 0.9})))
        conversation, _ = self.ask("a")
        self.ask("b", conversation)
        self.ask("c", conversation)
        self.assertEqual([r.job_id for r in self.executions()], ["job-x", "job-x-2", "job-x-3"])

    def test_clarify_and_decline_turns_are_recorded_too(self):
        self.use_agent(FakeAgentRunner(("ask_clarification", {"question": "Bạn cần gói nào?", "intent_confidence": 0.9})))
        _, message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertEqual(self.executions()[0].message_id, message.id)

    def test_limit_reached_turn_is_recorded_with_its_status(self):
        self.use_agent(FakeAgentRunner(steps=4, status=p.STATUS_MAX_ITERATIONS))
        _, message = self.ask()
        self.assertEqual(message.decision_trace["decision"], "decline")
        (row,) = self.executions()
        self.assertEqual((row.status, row.iterations_used, row.message_id), (p.STATUS_MAX_ITERATIONS, 4, message.id))

    def test_tenant_scoping_of_the_row(self):
        other_team = self.make_team("Team B")
        other_bot = self.service.create_bot(other_team.id, "Bot B")
        self.use_agent(FakeAgentRunner(("finish_answer", {"answer": "ok", "intent_confidence": 0.9})))
        conversation = self.conversation(other_bot)
        customer = self.add_message(conversation, "customer", "hi")
        self.service.reply_to_customer(other_bot, conversation, customer)
        (row,) = self.executions()
        self.assertEqual((row.bot_id, row.conversation_id), (other_bot.id, conversation.id))


class FailureIsNotHidden(AgentFlowCase):
    def failing(self, error):
        class Boom:
            def run(self, **kw):
                raise error

        return self.use_agent(Boom())

    def test_error_keeps_the_customer_message_creates_no_bot_message_and_still_records_the_execution(self):
        info = {"execution_id": "job-fail", "status": "timeout", "iterations_used": 2, "total_llm_calls": 2, "tool_calls_used": 1, "stop_reason": "timeout",
                "error": "quá 25s", "started_at": 1000.0, "finished_at": 1025.0}
        self.failing(rt.AgentRunError("AI Agent timeout", info))
        conversation = self.conversation()
        customer = self.add_message(conversation, "customer", "Giá gói Pro?")
        with self.assertRaises(rt.AgentRunError):
            self.service.reply_to_customer(self.bot, conversation, customer)
        self.db.session.rollback()
        self.assertEqual(Message.query.filter_by(sender="bot").count(), 0)
        self.assertEqual(Message.query.filter_by(sender="customer").count(), 1)
        (row,) = self.executions()
        self.assertEqual((row.job_id, row.status, row.message_id, row.iterations_used, row.stop_reason), ("job-fail", "timeout", None, 2, "timeout"))
        self.assertIn("AI Agent timeout", row.error_message)
        self.assertEqual(ConversationState.query.count(), 0, "phần ghi dở của lượt bị bỏ")

    def test_unavailable_worker_propagates_without_a_fake_answer(self):
        self.failing(rt.AgentUnavailableError("chưa có worker"))
        conversation = self.conversation()
        customer = self.add_message(conversation, "customer", "hi")
        with self.assertRaises(rt.AgentUnavailableError):
            self.service.reply_to_customer(self.bot, conversation, customer)
        self.db.session.rollback()
        self.assertEqual(Message.query.filter_by(sender="bot").count(), 0)
        self.assertEqual(AgentExecution.query.count(), 0)

    def test_widget_route_answers_502_and_keeps_the_customer_message(self):
        from app.models import BotDomain

        self.db.session.add(BotDomain(bot_id=self.bot.id, domain="shopabc.vn"))
        self.db.session.commit()
        self.failing(rt.AgentUnavailableError("chưa có worker"))
        client = self.app.test_client()
        response = client.post(f"/widget/api/{self.bot.public_id}/messages", json={"message": "Giá gói Pro?", "visitor_id": "v1"},
                               headers={"Origin": "https://shopabc.vn"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("worker", response.get_json()["error"], "không lộ chi tiết hạ tầng cho khách")
        self.assertEqual(Message.query.filter_by(sender="customer").count(), 1)
        self.assertEqual(Message.query.filter_by(sender="bot").count(), 0)


class StateAndCustomer(AgentFlowCase):
    def test_agent_slots_are_saved_and_contact_slots_create_the_customer(self):
        self.use_agent(FakeAgentRunner(("finish_answer", {
            "answer": "Cảm ơn anh Nam", "intent": "leave_contact", "intent_confidence": 0.95,
            "slots": {"contact_name": "Nam", "contact_phone": "0912 345 678"}})))
        conversation, _ = self.ask("Mình tên Nam, sdt 0912 345 678")
        state = ConversationState.query.filter_by(conversation_id=conversation.id).one()
        self.assertEqual((state.current_intent, state.slots["contact_name"]), ("leave_contact", "Nam"))
        (customer,) = Customer.query.all()
        self.assertEqual((customer.name, customer.phone), ("Nam", "0912345678"))
        self.assertEqual(self.db.session.get(Conversation, conversation.id).customer_id, customer.id)

    def test_clarification_counter_still_advances_in_agent_mode(self):
        self.use_agent(FakeAgentRunner(("ask_clarification", {"question": "Rõ hơn?", "intent_confidence": 0.9})))
        conversation, _ = self.ask("a")
        self.ask("b", conversation)
        self.assertEqual(ConversationState.query.filter_by(conversation_id=conversation.id).one().clarification_turns_used, 2)


class PreviewAndSwitch(AgentFlowCase):
    def test_preview_uses_the_agent_and_stores_nothing(self):
        self.use_agent(FakeAgentRunner(("finish_answer", {"answer": "Gói Pro 500k", "intent_confidence": 0.9})))
        result = self.service.preview_reply(self.bot, "Giá gói Pro?", [])
        self.assertEqual(result, {"reply": "Gói Pro 500k", "decision": "answer"})
        for model in (Message, ConversationState, Conversation, AgentExecution):
            self.assertEqual(model.query.count(), 0, model.__name__)

    def test_make_agent_runner_follows_the_config_flag(self):
        with mock.patch.object(Config, "AGENT_ENABLED", False):
            self.assertIsNone(self.service.make_agent_runner())
        with mock.patch.object(Config, "AGENT_ENABLED", True):
            runner = self.service.make_agent_runner()
        self.assertIsInstance(runner, rt.AgentRunner)

    def test_defaults_keep_the_agent_off_and_the_process_count_low(self):
        # Mặc định TRONG MÃ (không phải giá trị .env của máy đang chạy — .env có thể bật agent): chạy tiến trình sạch, không nạp .env.
        import subprocess
        import sys

        env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_") and k != "EMBEDDED_AGENT_WORKER"}
        code = "import dotenv; dotenv.load_dotenv = lambda *a, **k: False; from config import Config; print(Config.AGENT_ENABLED, Config.AGENT_MAX_PROCESSES)"
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(out.stdout.split(), ["False", "1"], out.stderr[-400:])


class ModelShape(AgentFlowCase):
    def test_job_id_is_unique(self):
        from sqlalchemy.exc import IntegrityError

        info = {"execution_id": "dup", "status": "completed", "started_at": 1.0}
        self.service._record_agent_execution(self.bot, None, info)
        self.db.session.commit()
        with self.assertRaises(IntegrityError):  # _record_agent_execution đã flush (Phase D cần id để quyết toán Credit)
            self.service._record_agent_execution(self.bot, None, info)
            self.db.session.commit()
        self.db.session.rollback()

    def test_row_without_conversation_or_message_is_allowed(self):
        self.service._record_agent_execution(self.bot, None, {"execution_id": "solo", "status": "failed", "started_at": 1.0, "error": "x" * 50})
        self.db.session.commit()
        row = AgentExecution.query.one()
        self.assertEqual((row.conversation_id, row.message_id, row.iterations_used), (None, None, 0))

    def test_stop_reason_is_capped_to_the_column(self):
        self.service._record_agent_execution(self.bot, None, {"execution_id": "long", "status": "failed", "started_at": 1.0, "stop_reason": "y" * 500})
        self.db.session.commit()
        self.assertEqual(len(AgentExecution.query.one().stop_reason), 100)


if __name__ == "__main__":
    import unittest

    unittest.main()
