"""Phía Flask của AI Agent (core/context_engine/agent/runtime.py) + tích hợp engine: dựng nội dung, đổi đầu ra agent về StructuredOutput,
gộp tín hiệu tra cứu, giao việc qua Redis, và — quan trọng nhất — các chốt an toàn của cây quyết định VẪN áp lên đầu ra của agent.
Không cần DB; Redis thật (prefix riêng), worker được mô phỏng bằng 1 luồng."""
import json
import threading
import time
import unittest
import uuid
from unittest import mock

import redis

from config import Config
from core import rag_engine
from core.context_engine import builder
from core.context_engine.agent import protocol as p
from core.context_engine.agent import runtime as rt
from core.context_engine.cost import LLMUsageTracker
from core.context_engine.decision import Decision
from core.context_engine.engine import TurnRequest, run_turn
from core.context_engine.state import ConversationSnapshot, IntentSpec, MemoryItem
from tests.helpers import settings
from tests.test_engine import PRICE_INTENT, passage, retrieval


def summary(terminal=None, final="", steps=2, tool_calls=None):
    s = p.RunSummary(steps=[p.LlmStep(200, 300, 40, 5) for _ in range(steps)], final_text=final, finish_reason="completed")
    s.tool_calls = list(tool_calls or [])
    if terminal:
        call = {"name": terminal[0], "arguments": terminal[1]}
        s.tool_calls.append(call)
        s.terminal, s.terminal_count = call, 1
    return s


def plan_for(question="Giá gói Pro?", recent=(), passages=None, snapshot=None, **overrides):
    cfg = settings(**overrides)
    turns = [builder.ChatTurn(role, text, 5) for role, text in recent]
    return builder.build_plan(cfg, [], snapshot or ConversationSnapshot(), turns, passages if passages is not None else [passage()], question)


class Rendering(unittest.TestCase):
    def test_persona_has_instructions_rules_and_agent_guidance_but_no_turn_data(self):
        cfg = settings(instructions="Bạn là trợ lý An Phát.")
        text = rt.render_persona(cfg, [])
        self.assertIn("Bạn là trợ lý An Phát.", text)
        self.assertIn("Quy tắc trả lời", text)
        self.assertIn("finish_answer", text)
        self.assertIn("ask_clarification", text)
        self.assertIn("decline", text)
        self.assertNotIn("proposed_answer", text, "hợp đồng JSON cũ không áp dụng cho agent")
        self.assertNotIn("Câu hỏi của khách", text)

    def test_persona_lists_intents_and_is_stable_for_the_same_config(self):
        intents = [IntentSpec("ask_price", "Hỏi giá", required=("product",))]
        cfg = settings()
        self.assertIn("ask_price: Hỏi giá", rt.render_persona(cfg, intents))
        self.assertEqual(rt.render_persona(cfg, intents), rt.render_persona(cfg, intents))

    def test_persona_follows_the_language(self):
        text = rt.render_persona(settings(language="en"), [])
        self.assertIn("Answering rules", text)
        self.assertIn("How you work", text)

    def test_input_orders_summary_memory_recent_initial_context_question(self):
        snapshot = ConversationSnapshot(summary="Khách cần 3 máy", memory=[MemoryItem("entity", "tên", "Nam", 0.9)])
        plan = plan_for("Giá bao nhiêu?", recent=[("user", "Xin chào"), ("assistant", "Chào anh")], snapshot=snapshot, structured_memory_enabled=True)
        text = rt.render_input(plan)
        order = [text.index(x) for x in ("Khách cần 3 máy", "[entity] tên: Nam", "Khách: Xin chào", "Trợ lý: Chào anh",
                                          "Gói Pro giá 500.000đ/tháng", "Giá bao nhiêu?", "kết thúc bằng một công cụ")]
        self.assertEqual(order, sorted(order))

    def test_input_without_passages_says_no_context(self):
        text = rt.render_input(plan_for(passages=[]))
        self.assertIn("Không tìm thấy thông tin liên quan", text)

    def test_input_without_history_has_no_history_section(self):
        self.assertNotIn("Hội thoại gần đây", rt.render_input(plan_for()))

    def test_scope_narrowing_note_names_the_agent_tools(self):
        plan = plan_for()
        plan.scope_narrowing = True
        text = rt.render_input(plan)
        self.assertIn("ask_clarification", text)
        self.assertNotIn("proposed_clarification_question", text)

    def test_english_input(self):
        text = rt.render_input(plan_for(recent=[("user", "hi")], language="en"))
        self.assertIn("Customer: hi", text)
        self.assertIn("Initial reference information", text)


class OutcomeMapping(unittest.TestCase):
    def test_finish_answer(self):
        out, issues = rt.outcome_to_structured(summary(("finish_answer", {
            "answer": "  Gói Pro 500k  ", "intent": "ask_price", "intent_confidence": 0.9, "slots": {"product": "Pro"}, "self_assessed_confidence": 0.8})))
        self.assertEqual((out.proposed_answer, out.proposed_clarification_question, out.intent, out.intent_confidence), ("Gói Pro 500k", "", "ask_price", 0.9))
        self.assertEqual((out.slots, out.self_assessed_confidence, out.needs_history_lookup, out.memory_updates), ({"product": "Pro"}, 0.8, False, []))
        self.assertEqual(issues, [])

    def test_ask_clarification(self):
        out, _ = rt.outcome_to_structured(summary(("ask_clarification", {"question": "Bạn cần gói nào?"})))
        self.assertEqual((out.proposed_answer, out.proposed_clarification_question), ("", "Bạn cần gói nào?"))

    def test_decline_leaves_both_empty_so_the_tree_declines(self):
        out, _ = rt.outcome_to_structured(summary(("decline", {"reason": "không có thông tin"})))
        self.assertEqual((out.proposed_answer, out.proposed_clarification_question), ("", ""))

    def test_no_terminal_tool_uses_the_final_text_and_says_so(self):
        out, issues = rt.outcome_to_structured(summary(final="Gói Pro giá 500k"))
        self.assertEqual(out.proposed_answer, "Gói Pro giá 500k")
        self.assertEqual(issues, ["no_terminal_tool"])

    def test_nothing_at_all_is_recorded_as_no_output(self):
        out, issues = rt.outcome_to_structured(summary())
        self.assertEqual((out.proposed_answer, out.proposed_clarification_question, issues), ("", "", ["no_output"]))

    def test_bad_optional_fields_are_dropped_and_reported_not_fatal(self):
        out, issues = rt.outcome_to_structured(summary(("finish_answer", {
            "answer": "ok", "intent": 5, "intent_confidence": 7, "slots": "chuỗi", "self_assessed_confidence": "cao"})))
        self.assertEqual(out.proposed_answer, "ok")
        self.assertEqual((out.intent, out.intent_confidence, out.slots, out.self_assessed_confidence), (None, None, {}, None))
        self.assertEqual(set(issues), {"invalid_intent", "invalid_intent_confidence", "invalid_slots", "invalid_self_assessed_confidence"})

    def test_non_string_answer_is_empty(self):
        for value in (None, 5, ["a"], {"x": 1}):
            out, _ = rt.outcome_to_structured(summary(("finish_answer", {"answer": value})))
            self.assertEqual(out.proposed_answer, "", value)

    def test_multiple_terminal_calls_are_flagged(self):
        s = summary(("finish_answer", {"answer": "a"}))
        s.terminal_count = 2
        self.assertIn("multiple_terminal_calls", rt.outcome_to_structured(s)[1])

    def test_slot_keys_are_stringified(self):
        out, _ = rt.outcome_to_structured(summary(("finish_answer", {"answer": "a", "slots": {"contact_phone": "0912345678"}})))
        self.assertEqual(out.slots, {"contact_phone": "0912345678"})


class MergeRetrieval(unittest.TestCase):
    def search(self, count, top=0.5, gap=None):
        return {"query": "q", "candidate_count": count, "top_distance": top, "distance_gap": gap}

    def test_no_searches_keeps_the_initial_signals(self):
        base = retrieval(count=3)
        self.assertIs(rt.merge_retrieval(base, []), base)

    def test_agent_finding_documents_when_initial_found_none(self):
        merged = rt.merge_retrieval(retrieval(count=0), [self.search(2, 0.8, 0.3)])
        self.assertEqual((merged.candidate_count, merged.top_distance, merged.distance_gap), (2, 0.8, 0.3))
        self.assertFalse(merged.knowledge_empty)

    def test_agent_finding_nothing_never_lowers_the_initial_count(self):
        base = retrieval(count=3)
        self.assertEqual(rt.merge_retrieval(base, [self.search(0, None)]).candidate_count, 3)

    def test_nothing_anywhere_stays_zero(self):
        merged = rt.merge_retrieval(retrieval(count=0), [self.search(0, None), self.search(0, None)])
        self.assertEqual(merged.candidate_count, 0)

    def test_more_candidates_wins_then_closer_distance(self):
        base = retrieval(count=2, top=0.9)
        self.assertEqual(rt.merge_retrieval(base, [self.search(4, 1.2)]).candidate_count, 4)
        self.assertEqual(rt.merge_retrieval(base, [self.search(2, 0.4)]).top_distance, 0.4)
        self.assertEqual(rt.merge_retrieval(base, [self.search(2, 1.4)]).top_distance, 0.9)

    def test_passages_of_the_initial_retrieval_are_untouched(self):
        base = retrieval(count=0)
        merged = rt.merge_retrieval(base, [self.search(2, 0.5)])
        self.assertEqual(merged.passages, base.passages)

    def test_missing_fields_in_a_search_record_do_not_crash(self):
        self.assertEqual(rt.merge_retrieval(retrieval(count=1), [{}, {"candidate_count": None}]).candidate_count, 1)


class RunnerRedis(unittest.TestCase):
    """AgentRunner qua Redis thật; worker mô phỏng bằng luồng lấy việc và trả kết quả dựng sẵn."""

    def setUp(self):
        self.redis = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)
        self.keys = p.Keys(f"test-agent-{uuid.uuid4().hex[:10]}")
        self.addCleanup(lambda: [self.redis.delete(k) for k in self.redis.scan_iter(f"{self.keys.prefix}:*")])
        self.threads = []
        allowance = mock.patch.object(rt, "WORKER_START_ALLOWANCE_SECONDS", 0)
        allowance.start()
        self.addCleanup(allowance.stop)
        self.limits = p.AgentLimits(3, 5, 4, 2.0)
        self.runner = rt.AgentRunner(self.redis, self.keys, "khoa", model="deepseek-v4-flash", internal_url="http://127.0.0.1:5000",
                                     limits=self.limits, id_factory=lambda: "job-" + uuid.uuid4().hex[:8])

    def alive(self):
        self.redis.set(self.keys.worker_alive, "1", ex=30)

    def fake_worker(self, reply: dict | None, searches=(), delay=0.0):
        """Lấy 1 việc, (tùy chọn) ghi các lần tra cứu như route nội bộ sẽ làm, trả `reply`."""
        seen = {}

        def work():
            popped = self.redis.blpop(self.keys.jobs, timeout=5)
            if not popped:
                return
            job = p.AgentJob.from_json(popped[1])
            seen["job"] = job
            time.sleep(delay)
            for s in searches:
                self.redis.rpush(self.keys.searches(job.job_id), json.dumps(s))
            if reply is not None:
                self.redis.rpush(self.keys.result(job.job_id), json.dumps({"job_id": job.job_id, **reply}))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.threads.append(thread)
        return seen

    def reply(self, status=p.STATUS_COMPLETED, summ=None, error=None, stop=""):
        return {"status": status, "summary": (summ or summary(("finish_answer", {"answer": "500k"}))).as_dict(), "runtime_seconds": 1.5,
                "error": error, "stop_reason": stop}

    def run_agent(self, plan=None, tracker=None):
        cfg = settings(instructions="Bạn là trợ lý An Phát.")
        return self.runner.run(bot_id=7, settings=cfg, intents=[], plan=plan or plan_for(instructions="Bạn là trợ lý An Phát."), tracker=tracker or LLMUsageTracker())

    def test_no_worker_fails_fast_with_a_clear_error(self):
        started = time.monotonic()
        with self.assertRaises(rt.AgentUnavailableError):
            self.run_agent()
        self.assertLess(time.monotonic() - started, 1.0, "không đợi hết thời gian khi rõ ràng chưa có worker")
        self.assertEqual(self.redis.llen(self.keys.jobs), 0, "không để việc mồ côi trong hàng đợi")

    def test_successful_run_returns_mapped_output_and_records_usage(self):
        self.alive()
        seen = self.fake_worker(self.reply())
        tracker = LLMUsageTracker()
        result = self.run_agent(tracker=tracker)
        self.assertEqual(result.output.proposed_answer, "500k")
        self.assertEqual(result.info["status"], p.STATUS_COMPLETED)
        self.assertEqual((result.info["iterations_used"], result.info["total_llm_calls"], result.info["terminal_tool"]), (2, 2, "finish_answer"))
        self.assertEqual(result.info["execution_id"], seen["job"].job_id)
        usage = tracker.main_usage
        self.assertEqual((usage.prompt_tokens, usage.cache_hit_tokens, usage.cache_miss_tokens, usage.completion_tokens), (1000, 600, 400, 80))
        self.assertEqual(tracker.main_call_count, 2)
        self.assertEqual(tracker.calls[0]["reasoning_tokens"], 5)

    def test_job_carries_static_persona_dynamic_input_limits_and_a_valid_token_for_this_bot_and_run(self):
        self.alive()
        seen = self.fake_worker(self.reply())
        self.run_agent()
        job = seen["job"]
        self.assertIn("Bạn là trợ lý An Phát.", job.persona)
        self.assertNotIn("Giá gói Pro?", job.persona)
        self.assertIn("Giá gói Pro?", job.input)
        self.assertEqual((job.bot_id, job.model, job.language, job.limits.max_runtime_seconds), (7, "deepseek-v4-flash", "vi", 2.0))
        self.assertEqual(job.max_tokens, 500 + 600)
        self.assertTrue(p.verify_run_token("khoa", 7, job.job_id, job.token))
        self.assertFalse(p.verify_run_token("khoa", 8, job.job_id, job.token), "token không dùng được cho bot khác")
        self.assertGreater(job.deadline, time.time())

    def test_agent_searches_are_returned_and_the_redis_record_is_removed(self):
        self.alive()
        self.fake_worker(self.reply(), searches=[{"query": "bảng giá", "candidate_count": 2, "top_distance": 0.7, "distance_gap": 0.2}])
        result = self.run_agent()
        self.assertEqual([s["query"] for s in result.searches], ["bảng giá"])
        self.assertEqual(result.info["searches"], [{"query": "bảng giá", "candidate_count": 2}])
        self.assertEqual(self.redis.llen(self.keys.searches(result.info["execution_id"])), 0)

    def test_timeout_when_worker_never_replies(self):
        self.alive()
        self.fake_worker(None)
        with self.assertRaises(rt.AgentRunError) as ctx:
            self.run_agent()
        self.assertEqual((ctx.exception.info["status"], ctx.exception.info["stop_reason"]), (p.STATUS_TIMEOUT, "worker_no_reply"))

    def test_failed_and_timeout_statuses_raise_with_the_execution_record(self):
        for status in (p.STATUS_FAILED, p.STATUS_TIMEOUT):
            self.alive()
            self.fake_worker(self.reply(status, summary(steps=1), error="hỏng dsh", stop="error"))
            tracker = LLMUsageTracker()
            with self.assertRaises(rt.AgentRunError) as ctx:
                self.run_agent(tracker=tracker)
            self.assertEqual(ctx.exception.info["status"], status)
            self.assertEqual(ctx.exception.info["error"], "hỏng dsh")
            self.assertEqual(tracker.main_call_count, 1, "tiền đã tốn cho lượt lỗi vẫn được ghi")

    def test_limit_statuses_do_not_raise_and_produce_an_empty_output_for_the_tree(self):
        for status in (p.STATUS_MAX_ITERATIONS, p.STATUS_MAX_TOOL_CALLS):
            self.alive()
            self.fake_worker(self.reply(status, summary(steps=4), error="vượt", stop=status))
            result = self.run_agent()
            self.assertEqual(result.info["status"], status)
            self.assertEqual((result.output.proposed_answer, result.output.proposed_clarification_question), ("", ""))
            self.assertIn("no_output", result.info["issues"])

    def test_unknown_status_from_the_worker_is_treated_as_failed(self):
        self.alive()
        self.fake_worker(self.reply("bí ẩn"))
        with self.assertRaises(rt.AgentRunError) as ctx:
            self.run_agent()
        self.assertEqual(ctx.exception.info["status"], p.STATUS_FAILED)

    def test_from_config_builds_limits_from_settings(self):
        cfg = mock.Mock(AGENT_MAX_TOOL_CALLS=2, AGENT_MAX_ITERATIONS=6, AGENT_MAX_RUNTIME_SECONDS=12.0, AGENT_REDIS_PREFIX="zz", SECRET_KEY="k",
                        AGENT_MODEL="m", AGENT_INTERNAL_URL="http://h", AGENT_MAX_ACTION_CALLS=2, AGENT_ACTION_WAIT_SECONDS=8.0)
        runner = rt.AgentRunner.from_config(cfg)
        self.assertEqual(runner.limits, p.AgentLimits(max_search_calls=2, max_tool_calls=5, max_iterations=6, max_runtime_seconds=12.0, max_action_calls=2, action_wait_seconds=8.0))
        self.assertEqual((runner.keys.prefix, runner.model, runner.internal_url), ("zz", "m", "http://h"))


class FakeAgentRunner:
    """Thay AgentRunner trong engine: trả kết quả dựng sẵn, ghi lại lần gọi."""

    def __init__(self, terminal=None, final="", searches=(), steps=2, status=p.STATUS_COMPLETED):
        self.calls = []
        self.terminal, self.final, self.searches, self.steps, self.status = terminal, final, list(searches), steps, status
        self.count = 0

    def run(self, *, bot_id, settings, intents, plan, tracker, conversation_id=None, pressure_level="normal"):
        self.calls.append({"bot_id": bot_id, "plan": plan, "intents": intents, "conversation_id": conversation_id, "pressure_level": pressure_level})
        s = summary(self.terminal, self.final, self.steps)
        for step in s.steps:
            tracker.record("main", rt.usage_of(step))
        output, issues = rt.outcome_to_structured(s)
        self.count += 1
        info = {"execution_id": "job-x" if self.count == 1 else f"job-x-{self.count}", "status": self.status, "iterations_used": self.steps, "total_llm_calls": self.steps, "tool_calls_used": 1,
                "terminal_tool": self.terminal[0] if self.terminal else None, "stop_reason": "completed", "error": None, "started_at": 1.0,
                "finished_at": 2.0, "runtime_seconds": 1.0, "issues": issues, "searches": [{"query": q.get("query"), "candidate_count": q["candidate_count"]} for q in self.searches]}
        return rt.AgentResult(output, info, self.searches)


def turn(agent, retrieved=None, question="Gói Pro giá bao nhiêu?", intents=(), **overrides):
    request = TurnRequest(bot_id=1, question=question, settings=settings(**overrides), snapshot=ConversationSnapshot(), intents=list(intents),
                          recent_rows=[], conversation_id=1)
    llm = mock.Mock(side_effect=AssertionError("chế độ agent không được gọi lệnh gọi JSON cũ"))
    result = run_turn(request, llm_call=llm, retrieve_fn=lambda *a, **k: retrieved if retrieved is not None else retrieval(),
                      history_search_fn=lambda *a, **k: [], agent_runner=agent)
    return result, llm


class EngineWithAgent(unittest.TestCase):
    def test_finish_answer_is_an_answer_and_no_old_llm_call_is_made(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "Gói Pro 500.000đ/tháng", "intent": "ask_price", "intent_confidence": 0.95}))
        result, llm = turn(agent)
        self.assertEqual((result.decision, result.reply), (Decision.ANSWER, "Gói Pro 500.000đ/tháng"))
        llm.assert_not_called()
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual(agent.calls[0]["bot_id"], 1)

    def test_agent_usage_is_summed_into_the_main_usage_and_call_count(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "x", "intent_confidence": 0.9}), steps=3)
        result, _ = turn(agent)
        self.assertEqual(result.trace["main_llm_calls"], 3)
        self.assertEqual((result.usage.prompt_tokens, result.usage.cache_hit_tokens), (1500, 900))

    def test_trace_and_result_carry_the_execution(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "x", "intent_confidence": 0.9}))
        result, _ = turn(agent)
        self.assertEqual(result.agent["execution_id"], "job-x")
        self.assertEqual(result.trace["agent"]["terminal_tool"], "finish_answer")
        self.assertNotIn("started_at", result.trace["agent"])
        json.dumps(result.trace)

    def test_ask_clarification_is_a_clarify(self):
        agent = FakeAgentRunner(("ask_clarification", {"question": "Bạn cần gói nào?", "intent_confidence": 0.9}))
        result, _ = turn(agent)
        self.assertEqual((result.decision, result.reply), (Decision.CLARIFY, "Bạn cần gói nào?"))
        self.assertEqual(result.state_update.clarification_turns_used, 1)

    def test_decline_uses_the_owners_message(self):
        agent = FakeAgentRunner(("decline", {"reason": "không có"}))
        result, _ = turn(agent, low_confidence_decline_message="Chưa có thông tin, xin liên hệ 1900.")
        self.assertEqual((result.decision, result.reply), (Decision.DECLINE, "Chưa có thông tin, xin liên hệ 1900."))

    def test_SAFETY_zero_context_still_overrides_a_confident_agent_answer(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "TỰ BỊA 1 TỶ", "intent_confidence": 0.99}))
        result, _ = turn(agent, retrieved=retrieval(count=0), low_confidence_reply_mode="decline", low_confidence_decline_message="Xin lỗi, chưa có thông tin.")
        self.assertEqual((result.decision, result.reply), (Decision.DECLINE, "Xin lỗi, chưa có thông tin."))
        self.assertNotIn("1 TỶ", result.reply)
        self.assertIn("no_relevant_context", result.trace["reasons"])

    def test_agent_that_finds_documents_by_its_own_search_is_not_refused(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "Gói Pro 500k", "intent_confidence": 0.9}),
                                searches=[{"query": "bảng giá", "candidate_count": 2, "top_distance": 0.6, "distance_gap": 0.3}])
        result, _ = turn(agent, retrieved=retrieval(count=0))
        self.assertEqual((result.decision, result.reply), (Decision.ANSWER, "Gói Pro 500k"))
        self.assertEqual(result.trace["candidate_count"], 2)

    def test_SAFETY_agent_searching_and_finding_nothing_is_still_refused(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "TỰ BỊA", "intent_confidence": 0.99}),
                                searches=[{"query": "a", "candidate_count": 0}, {"query": "b", "candidate_count": 0}])
        result, _ = turn(agent, retrieved=retrieval(count=0), low_confidence_reply_mode="decline")
        self.assertEqual(result.decision, Decision.DECLINE)

    def test_bot_without_any_knowledge_can_still_chat(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "Xin chào!", "intent_confidence": 0.9}))
        result, _ = turn(agent, retrieved=rag_engine.RetrievalResult(knowledge_empty=True))
        self.assertEqual((result.decision, result.reply), (Decision.ANSWER, "Xin chào!"))

    def test_low_intent_confidence_from_the_agent_still_triggers_clarify(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "Đây là câu trả lời", "intent_confidence": 0.2}))
        result, _ = turn(agent)
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertIn("low_intent_confidence", result.trace["reasons"])

    def test_slot_thresholds_still_apply_to_agent_slots(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "ok", "intent": "ask_price", "intent_confidence": 0.95, "slots": {"product": "Pro"}}))
        result, _ = turn(agent, intents=[PRICE_INTENT])
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertIn("missing_required_slots", result.trace["reasons"])
        self.assertEqual(result.state_update.slots, {"product": "Pro"})

    def test_agent_intent_and_slots_reach_the_state_update(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "ok", "intent": "ask_price", "intent_confidence": 0.95, "slots": {"contact_name": "Nam"}}))
        result, _ = turn(agent)
        self.assertEqual((result.state_update.current_intent, result.state_update.slots), ("ask_price", {"contact_name": "Nam"}))

    def test_limit_reached_becomes_a_decline_with_the_status_in_the_trace(self):
        agent = FakeAgentRunner(steps=4, status=p.STATUS_MAX_ITERATIONS)
        result, _ = turn(agent)
        self.assertEqual(result.decision, Decision.DECLINE)
        self.assertEqual(result.trace["agent"]["status"], p.STATUS_MAX_ITERATIONS)
        self.assertIn("empty_proposed_answer", result.trace["reasons"])

    def test_agent_error_propagates_and_is_not_turned_into_a_fake_answer(self):
        class Boom:
            def run(self, **kw):
                raise rt.AgentRunError("hỏng", {"execution_id": "j", "status": "failed"})

        with self.assertRaises(rt.AgentRunError):
            turn(Boom())

    def test_no_history_lookup_is_attempted_in_agent_mode(self):
        agent = FakeAgentRunner(("finish_answer", {"answer": "ok", "intent_confidence": 0.9}))
        request = TurnRequest(bot_id=1, question="q", settings=settings(), snapshot=ConversationSnapshot(), intents=[], recent_rows=[], conversation_id=1)
        lookups = mock.Mock(return_value=[])
        run_turn(request, retrieve_fn=lambda *a, **k: retrieval(), history_search_fn=lookups, agent_runner=agent)
        lookups.assert_not_called()

    def test_default_path_without_agent_is_unchanged(self):
        from tests.helpers import FakeLLM, llm_json

        llm = FakeLLM(llm_json())
        request = TurnRequest(bot_id=1, question="q", settings=settings(), snapshot=ConversationSnapshot(), intents=[], recent_rows=[], conversation_id=1)
        result = run_turn(request, llm_call=llm, retrieve_fn=lambda *a, **k: retrieval(), history_search_fn=lambda *a, **k: [])
        self.assertIsNone(result.agent)
        self.assertNotIn("agent", result.trace)
        self.assertEqual(len(llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
