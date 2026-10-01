"""C2 — công cụ `summarize_conversation` do agent TỰ gọi khi Context Builder báo áp lực >= nén mạnh (job nền `jobs.py` vẫn là lưới an toàn).

Các lớp được kiểm chứng riêng, từ ngoài vào trong:
  giao thức (công cụ không phải công cụ kết thúc, trần dừng cứng) -> máy chủ MCP (cổng từ chối sớm, danh tính không do model truyền)
  -> route nội bộ /internal/conversation/summarize (DB + Redis thật: xác thực, ngữ cảnh do Flask giữ, 1 lần/lượt, khóa, lỗi không bị nuốt)
  -> AgentRunner (đăng ký ngữ cảnh, gợi ý trong prompt, usage vào giá vốn) -> engine/service (truyền mức áp lực) -> job nền (khóa chung).
Cần DB *_test và Redis cho các lớp có DB (xem tests/README.md)."""
import dataclasses
import json
import threading
import time
import unittest
import urllib.error
import uuid
from unittest import mock

import redis

from app.models import ConversationState
from config import Config
from core.context_engine import jobs, prompts
from core.context_engine.agent import kb_mcp_server as kb
from core.context_engine.agent import protocol as p
from core.context_engine.agent import runtime as rt
from core.context_engine.cost import LLMUsageTracker
from core.context_engine.structured import LLMReply
from tests.db_case import DbCase
from tests.helpers import settings, usage
from tests.test_agent_internal import InternalCase
from tests.test_agent_mcp import McpCase, Recorder
from tests.test_agent_runtime import plan_for, summary
from tests.test_settings_effect_agent import AgentEffectCase


# ---------------------------------------------------------------- giao thức

class Protocol(unittest.TestCase):
    def test_summarize_is_a_model_tool_but_not_an_ending_tool(self):
        self.assertIn(p.SUMMARIZE_TOOL, p.MODEL_TOOL_NAMES)
        self.assertNotIn(p.SUMMARIZE_TOOL, p.TERMINAL_TOOLS)

    def test_calling_it_never_ends_the_turn_and_is_counted_apart_from_searches(self):
        s = p.RunSummary()
        p.apply_event(s, {"type": "tool/call", "data": {"name": "mcp__kb__summarize_conversation", "arguments": "{}"}})
        self.assertIsNone(s.terminal)
        self.assertEqual((s.summarize_calls, s.search_calls, p.tool_calls_before_terminal(s)), (1, 0, 1))

    def test_hard_stop_allows_one_summarize_on_top_of_the_search_budget(self):
        limits = rt.AgentRunner.from_config(Config).limits
        self.assertEqual(limits.max_tool_calls, limits.max_search_calls + rt.HARD_TOOL_MARGIN + p.SUMMARIZE_TOOL_ALLOWANCE)

    def job(self, **over):
        base = dict(job_id="j", bot_id=1, token="t", persona="P", max_tokens=100, language="vi", input="i", internal_url="http://x", model="m")
        base.update(over)
        return p.AgentJob(**base)

    def test_job_carries_the_per_turn_permission_without_changing_the_process_key(self):
        allowed, denied = self.job(allow_summarize=True), self.job(allow_summarize=False)
        self.assertEqual(allowed.process_key, denied.process_key, "quyền theo lượt không được đẻ thêm tiến trình dsh")
        self.assertTrue(p.AgentJob.from_json(allowed.to_json()).allow_summarize)

    def test_a_job_queued_before_this_change_still_loads_and_defaults_to_denied(self):
        data = json.loads(self.job().to_json())
        data.pop("allow_summarize")
        self.assertFalse(p.AgentJob.from_json(json.dumps(data)).allow_summarize)

    def test_redis_keys_are_per_run(self):
        keys = p.Keys("pre")
        names = {keys.run_context("a"), keys.summary_used("a"), keys.summaries("a"), keys.searches("a")}
        self.assertEqual(len(names), 4)
        self.assertNotEqual(keys.run_context("a"), keys.run_context("b"))


# ---------------------------------------------------------------- máy chủ MCP

class McpTool(McpCase):
    def allow(self, allowed=True, run_id="run-1", token="tok-1"):
        with open(self.run_file, "w", encoding="utf-8") as handle:
            json.dump({"run_id": run_id, "token": token, "max_search_calls": 3, "allow_summarize": allowed}, handle)

    def test_tool_is_listed_with_no_arguments_in_both_languages(self):
        for language in ("vi", "en"):
            tools = {t["name"]: t for t in self.server(language=language).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
            self.assertEqual(set(tools), set(p.MODEL_TOOL_NAMES))
            self.assertEqual(tools[p.SUMMARIZE_TOOL]["inputSchema"], {"type": "object", "properties": {}})
            self.assertTrue(tools[p.SUMMARIZE_TOOL]["description"])

    def test_refused_early_without_any_http_call_when_the_turn_is_not_allowed(self):
        for state in (False, None):
            recorder = Recorder()
            self.allow(state) if state is not None else self.write_run("run-1", "tok-1")
            result = self.call(self.server(recorder), p.SUMMARIZE_TOOL, {})
            self.assertFalse(result["isError"])
            self.assertIn("không cần tóm tắt", self.text(result))
            self.assertEqual(recorder.requests, [], f"allow_summarize={state}")

    def test_missing_run_file_is_treated_as_not_allowed(self):
        recorder = Recorder()
        server = self.server(recorder)
        server.run_file = self.run_file + ".khong-co"
        self.assertFalse(self.call(server, p.SUMMARIZE_TOOL, {})["isError"])
        self.assertEqual(recorder.requests, [])

    def test_identity_comes_from_the_run_file_never_from_the_model(self):
        self.allow()
        recorder = Recorder({"status": "summarized", "summary": "Khách hỏi gói Pro."})
        self.call(self.server(recorder), p.SUMMARIZE_TOOL, {"conversation_id": 999, "bot_id": 5, "run_id": "khac", "token": "gia"})
        (request,) = recorder.requests
        self.assertEqual(request.full_url, "http://127.0.0.1:5000/internal/conversation/summarize")
        self.assertEqual(recorder.bodies()[0], {"bot_id": 21, "run_id": "run-1", "token": "tok-1"})

    def test_summarized_returns_the_summary_text_to_the_agent(self):
        self.allow()
        result = self.call(self.server(Recorder({"status": "summarized", "summary": "  Khách hỏi gói Pro.  "})), p.SUMMARIZE_TOOL, {})
        self.assertFalse(result["isError"])
        self.assertIn("Tóm tắt phần đầu hội thoại", self.text(result))
        self.assertIn("Khách hỏi gói Pro.", self.text(result))

    def test_english_labels_follow_the_bot_language(self):
        self.allow()
        result = self.call(self.server(Recorder({"status": "summarized", "summary": "Asked about Pro."}, ), language="en"), p.SUMMARIZE_TOOL, {})
        self.assertIn("Summary of the early conversation", self.text(result))

    def test_nothing_to_summarize_still_passes_on_the_existing_summary(self):
        self.allow()
        result = self.call(self.server(Recorder({"status": "nothing_to_summarize", "summary": "Đã tóm tắt trước đó."})), p.SUMMARIZE_TOOL, {})
        self.assertFalse(result["isError"])
        self.assertIn("Đã tóm tắt trước đó.", self.text(result))

    def test_benign_statuses_are_not_errors_and_tell_the_agent_to_carry_on(self):
        self.allow()
        for status in ("busy", "not_allowed", "already_used", "nothing_to_summarize"):
            result = self.call(self.server(Recorder({"status": status, "summary": None})), p.SUMMARIZE_TOOL, {})
            self.assertFalse(result["isError"], status)
            self.assertIn("câu hỏi của khách", self.text(result), status)

    def test_failures_are_reported_as_errors_and_never_invent_a_summary(self):
        self.allow()
        failing = (
            Recorder(error=urllib.error.URLError("refused")),
            Recorder(error=TimeoutError()),
            Recorder(b"khong-phai-json"),
            Recorder({"status": "error", "summary": None}),
            Recorder({"status": "summarized", "summary": "   "}),   # nói đã tóm tắt nhưng rỗng: không được coi là thành công
            Recorder({"loi": True}),
        )
        for recorder in failing:
            result = self.call(self.server(recorder), p.SUMMARIZE_TOOL, {})
            self.assertTrue(result["isError"])
            self.assertIn("không đoán phần hội thoại cũ", self.text(result))


# ---------------------------------------------------------------- route nội bộ (DB + Redis thật)

class SummarizeCase(InternalCase):
    """Hội thoại có 10 tin, cửa sổ gần đây 4 -> 6 tin cũ đủ điều kiện tóm tắt."""

    def setUp(self):
        super().setUp()
        self.set_settings(recent_message_limit=4, summary_max_tokens=500)
        self.conv = self.conversation()
        self.rows = [self.add_message(self.conv, "customer" if i % 2 == 0 else "bot", f"tin so {i}") for i in range(10)]
        self.state = ConversationState(conversation_id=self.conv.id, bot_id=self.bot.id, slots={})
        self.db.session.add(self.state)
        self.db.session.commit()
        self.llm_calls = []
        self.llm_reply = lambda messages: LLMReply("Khách hỏi về gói Pro và Basic.", usage())

        def factory(max_tokens):
            def call(messages):
                self.llm_calls.append(messages)
                return self.llm_reply(messages)
            return call

        patcher = mock.patch("core.context_engine.jobs.summary_llm_call", factory)
        patcher.start()
        self.addCleanup(patcher.stop)

    def context(self, run_id=None, *, bot=None, conversation=None, allowed=True):
        self.redis.set(p.Keys(self.prefix).run_context(run_id or self.run_id), json.dumps({
            "bot_id": (bot or self.bot).id, "conversation_id": (conversation or self.conv).id, "summarize_allowed": allowed,
        }), ex=60)

    def summarize(self, bot=None, token=None, run_id=None, remote="127.0.0.1", **extra):
        body = {"bot_id": (bot or self.bot).id, "run_id": run_id or self.run_id, "token": token if token is not None else self.token(bot, run_id)}
        body.update(extra)
        return self.client.post("/internal/conversation/summarize", json=body, environ_overrides={"REMOTE_ADDR": remote})

    def saved(self):
        self.db.session.expire_all()
        return ConversationState.query.filter_by(conversation_id=self.conv.id).one()

    def attempts(self, run_id=None):
        return [json.loads(x) for x in self.redis.lrange(p.Keys(self.prefix).summaries(run_id or self.run_id), 0, -1)]


class RouteAuthentication(SummarizeCase):
    def test_only_loopback(self):
        self.context()
        self.assertEqual(self.summarize(remote="10.0.0.5").status_code, 403)
        self.assertEqual(self.summarize(remote="203.0.113.9").status_code, 403)
        self.assertEqual(self.llm_calls, [])

    def test_forwarded_header_cannot_fake_loopback(self):
        self.context()
        response = self.client.post("/internal/conversation/summarize", json={"bot_id": self.bot.id, "run_id": self.run_id, "token": self.token()},
                                    environ_overrides={"REMOTE_ADDR": "10.0.0.5"}, headers={"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(response.status_code, 403)

    def test_token_must_be_valid_for_this_bot_and_run(self):
        self.context()
        other_bot = self.service.create_bot(self.team.id, "Bot B")
        for token in ("", "abc", self.token(run_id="run-khac"), self.token(other_bot), self.token(now=time.time() - 10_000)):
            self.assertEqual(self.summarize(token=token).status_code, 403, token)
        self.assertEqual(self.llm_calls, [])

    def test_bad_bodies_are_rejected(self):
        self.assertEqual(self.client.post("/internal/conversation/summarize", data="khong-phai-json", content_type="text/plain",
                                          environ_overrides={"REMOTE_ADDR": "127.0.0.1"}).status_code, 400)
        response = self.client.post("/internal/conversation/summarize", json=[1, 2], environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
        self.assertEqual(response.status_code, 400)

    def test_rag_route_still_works_after_the_auth_helper_was_shared(self):
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.post(remote="10.0.0.5").status_code, 403)
        self.assertEqual(self.post(token="x").status_code, 403)
        self.assertEqual(self.post(query="  ").status_code, 400)


class RouteContextIsHeldByFlask(SummarizeCase):
    def status(self, response):
        self.assertEqual(response.status_code, 200)
        return response.get_json()["status"]

    def test_no_context_means_not_allowed_even_with_a_valid_token(self):
        self.assertEqual(self.status(self.summarize()), p.SUMMARY_NOT_ALLOWED)
        self.assertEqual(self.llm_calls, [])

    def test_context_for_a_turn_that_is_not_allowed(self):
        self.context(allowed=False)
        self.assertEqual(self.status(self.summarize()), p.SUMMARY_NOT_ALLOWED)
        self.assertEqual(self.llm_calls, [])

    def test_context_registered_for_another_bot_is_useless(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.context(bot=other)
        self.assertEqual(self.status(self.summarize()), p.SUMMARY_NOT_ALLOWED)
        self.assertEqual(self.llm_calls, [])

    def test_a_conversation_of_another_bot_can_never_be_summarized(self):
        other_team = self.make_team("Team B")
        other_bot = self.service.create_bot(other_team.id, "Bot B")
        foreign = self.conversation(other_bot)
        for i in range(10):
            self.add_message(foreign, "customer", f"bi mat {i}")
        self.context(conversation=foreign)  # ngữ cảnh (giả sử) trỏ nhầm hội thoại của bot khác
        self.assertEqual(self.status(self.summarize()), p.SUMMARY_NOT_ALLOWED)
        self.assertEqual(self.llm_calls, [])

    def test_malformed_context_is_not_allowed(self):
        key = p.Keys(self.prefix).run_context(self.run_id)
        for raw in ("khong-json", "[]", json.dumps({"bot_id": self.bot.id, "summarize_allowed": True}),
                    json.dumps({"bot_id": self.bot.id, "conversation_id": True, "summarize_allowed": True}),
                    json.dumps({"bot_id": self.bot.id, "conversation_id": "1", "summarize_allowed": True}),
                    json.dumps({"bot_id": self.bot.id, "conversation_id": self.conv.id, "summarize_allowed": "yes"})):
            self.redis.set(key, raw, ex=60)
            self.redis.delete(p.Keys(self.prefix).summary_used(self.run_id))
            self.assertEqual(self.status(self.summarize()), p.SUMMARY_NOT_ALLOWED, raw)
        self.assertEqual(self.llm_calls, [])

    def test_request_body_cannot_choose_the_conversation(self):
        other = self.conversation()
        for i in range(10):
            self.add_message(other, "customer", f"cua khach khac {i}")
        self.context()
        self.summarize(conversation_id=other.id)
        self.db.session.expire_all()
        untouched = ConversationState.query.filter_by(conversation_id=other.id).first()
        self.assertTrue(untouched is None or untouched.summary is None)
        self.assertTrue(all("cua khach khac" not in m["content"] for call in self.llm_calls for m in call))


class RouteSummarizes(SummarizeCase):
    def test_summarizes_the_old_messages_and_stores_the_result(self):
        self.context()
        response = self.summarize()
        self.assertEqual(response.get_json(), {"status": p.SUMMARY_SUMMARIZED, "summary": "Khách hỏi về gói Pro và Basic."})
        state = self.saved()
        self.assertEqual(state.summary, "Khách hỏi về gói Pro và Basic.")
        self.assertEqual(state.last_summarized_message_id, self.rows[5].id, "6 tin cũ nhất; 4 tin gần đây giữ nguyên văn")
        self.assertFalse(state.summary_pending)
        self.assertIsNotNone(state.summary_updated_at)

    def test_uses_the_same_prompt_as_the_background_job(self):
        self.context()
        self.summarize()
        (messages,) = self.llm_calls
        self.assertIn("tin so 0", messages[1]["content"])
        self.assertIn("tin so 5", messages[1]["content"])
        self.assertNotIn("tin so 6", messages[1]["content"], "cửa sổ gần đây không bị tóm tắt")

    def test_builds_on_the_previous_summary(self):
        self.state.summary, self.state.last_summarized_message_id = "Tóm tắt cũ.", self.rows[2].id
        self.db.session.commit()
        self.context()
        self.summarize()
        (messages,) = self.llm_calls
        self.assertIn("Tóm tắt cũ.", messages[1]["content"])
        self.assertNotIn("tin so 2", messages[1]["content"], "tin đã nằm trong summary không bị tóm tắt lại")
        self.assertIn("tin so 3", messages[1]["content"])

    def test_only_one_call_per_turn(self):
        self.context()
        self.assertEqual(self.summarize().get_json()["status"], p.SUMMARY_SUMMARIZED)
        second = self.summarize().get_json()
        self.assertEqual((second["status"], second["summary"]), (p.SUMMARY_ALREADY_USED, None))
        self.assertEqual(len(self.llm_calls), 1)

    def test_the_next_turn_gets_its_own_allowance(self):
        self.context()
        self.summarize()
        self.context("run-luot-2")
        self.assertEqual(self.summarize(run_id="run-luot-2").get_json()["status"], p.SUMMARY_NOTHING, "không còn tin cũ -> không gọi LLM nữa")
        self.assertEqual(len(self.llm_calls), 1)

    def test_nothing_older_than_the_recent_window_calls_no_llm(self):
        self.set_settings(recent_message_limit=20)
        self.context()
        data = self.summarize().get_json()
        self.assertEqual((data["status"], data["summary"]), (p.SUMMARY_NOTHING, None))
        self.assertEqual(self.llm_calls, [])
        self.assertIsNone(self.saved().summary)

    def test_nothing_to_summarize_returns_the_existing_summary(self):
        self.set_settings(recent_message_limit=20)
        self.state.summary = "Đã có từ trước."
        self.db.session.commit()
        self.context()
        self.assertEqual(self.summarize().get_json(), {"status": p.SUMMARY_NOTHING, "summary": "Đã có từ trước."})

    def test_a_conversation_without_saved_state_has_nothing_to_summarize(self):
        fresh = self.conversation()  # lượt đầu của hội thoại: state tạo lười, chưa commit
        self.context(conversation=fresh)
        self.assertEqual(self.summarize().get_json()["status"], p.SUMMARY_NOTHING)
        self.assertEqual(self.llm_calls, [])
        self.assertEqual(ConversationState.query.filter_by(conversation_id=fresh.id).count(), 0)

    def test_staff_messages_are_not_counted_in_the_window_like_the_background_job(self):
        self.add_message(self.conv, "staff", "nhan vien nhac")
        self.context()
        self.assertEqual(self.summarize().get_json()["status"], p.SUMMARY_SUMMARIZED)
        self.assertEqual(self.saved().last_summarized_message_id, self.rows[5].id)

    def test_the_lock_is_released_afterwards(self):
        self.context()
        self.summarize()
        token = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.assertIsNotNone(token)
        jobs.release_summary_lock(self.redis, self.conv.id, token)

    def test_llm_usage_is_recorded_for_the_turn_cost(self):
        self.llm_reply = lambda messages: LLMReply("Tóm tắt.", {"prompt_tokens": 800, "completion_tokens": 90, "total_tokens": 890,
                                                              "prompt_cache_hit_tokens": 512, "prompt_cache_miss_tokens": 288})
        self.context()
        self.summarize()
        (attempt,) = self.attempts()
        self.assertEqual(attempt["status"], p.SUMMARY_SUMMARIZED)
        (call,) = attempt["calls"]
        self.assertEqual((call["kind"], call["prompt_tokens"], call["completion_tokens"], call["cache_hit_tokens"], call["conversation_id"]),
                         ("summary", 800, 90, 512, self.conv.id))
        self.assertTrue(0 < self.redis.ttl(p.Keys(self.prefix).summaries(self.run_id)) <= 300)


class RouteDoesNotHideFailures(SummarizeCase):
    def test_llm_failure_is_reported_writes_nothing_and_releases_the_lock(self):
        def broken(messages):
            raise ConnectionError("mạng đứt")

        self.llm_reply = broken
        self.context()
        with self.assertLogs("internal", level="ERROR"):
            data = self.summarize().get_json()
        self.assertEqual((data["status"], data["summary"]), (p.SUMMARY_ERROR, None))
        state = self.saved()
        self.assertEqual((state.summary, state.last_summarized_message_id), (None, None))
        self.assertEqual([a["status"] for a in self.attempts()], [p.SUMMARY_ERROR])
        token = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.assertIsNotNone(token, "khóa phải được nhả kể cả khi lỗi")
        jobs.release_summary_lock(self.redis, self.conv.id, token)

    def test_an_empty_llm_answer_is_an_error_not_an_empty_summary(self):
        self.llm_reply = lambda messages: LLMReply("   ", usage())
        self.context()
        with self.assertLogs("internal", level="ERROR"):
            self.assertEqual(self.summarize().get_json()["status"], p.SUMMARY_ERROR)
        self.assertIsNone(self.saved().summary)

    def test_a_failed_attempt_still_counts_as_the_turns_one_call(self):
        self.llm_reply = lambda messages: (_ for _ in ()).throw(ConnectionError("x"))
        self.context()
        with self.assertLogs("internal", level="ERROR"):
            self.summarize()
        self.assertEqual(self.summarize().get_json()["status"], p.SUMMARY_ALREADY_USED, "không thử lại vô hạn/không tốn thêm tiền")
        self.assertEqual(len(self.llm_calls), 1)


class RouteAndBackgroundJobShareOneLock(SummarizeCase):
    def test_busy_when_the_background_job_holds_the_lock(self):
        held = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.addCleanup(jobs.release_summary_lock, self.redis, self.conv.id, held)
        self.context()
        data = self.summarize().get_json()
        self.assertEqual((data["status"], data["summary"]), (p.SUMMARY_BUSY, None))
        self.assertEqual(self.llm_calls, [])
        self.assertEqual([a["status"] for a in self.attempts()], [p.SUMMARY_BUSY])

    def test_lock_is_per_conversation(self):
        other = self.conversation()
        held = jobs.acquire_summary_lock(self.redis, other.id)
        self.addCleanup(jobs.release_summary_lock, self.redis, other.id, held)
        self.context()
        self.assertEqual(self.summarize().get_json()["status"], p.SUMMARY_SUMMARIZED)

    def test_only_the_owner_can_release_a_lock(self):
        held = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.assertIsNone(jobs.acquire_summary_lock(self.redis, self.conv.id))
        jobs.release_summary_lock(self.redis, self.conv.id, "token-cua-nguoi-khac")
        self.assertIsNone(jobs.acquire_summary_lock(self.redis, self.conv.id), "nhả bằng token lạ không được mở khóa")
        jobs.release_summary_lock(self.redis, self.conv.id, held)
        again = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.assertIsNotNone(again)
        jobs.release_summary_lock(self.redis, self.conv.id, again)

    def test_lock_has_a_ttl_so_a_crashed_holder_cannot_block_forever(self):
        held = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.addCleanup(jobs.release_summary_lock, self.redis, self.conv.id, held)
        self.assertTrue(0 < self.redis.ttl(jobs.summary_lock_key(self.conv.id)) <= jobs.SUMMARY_LOCK_TTL_SECONDS)

    def test_the_background_job_skips_a_locked_conversation_and_keeps_the_flag(self):
        from workers import context_jobs

        self.state.summary_pending = True
        self.db.session.commit()
        self.redis.delete(context_jobs._backoff_key(self.state.id))
        held = jobs.acquire_summary_lock(self.redis, self.conv.id)
        self.assertEqual(context_jobs.process_summaries_once(), 0)
        self.assertEqual(self.llm_calls, [])
        self.assertTrue(self.saved().summary_pending, "cờ giữ nguyên để vòng sau làm")
        jobs.release_summary_lock(self.redis, self.conv.id, held)
        self.assertEqual(context_jobs.process_summaries_once(), 1)
        self.assertFalse(self.saved().summary_pending)

    def test_the_background_job_does_not_redo_what_the_agent_just_did(self):
        from workers import context_jobs

        self.state.summary_pending = True
        self.db.session.commit()
        self.redis.delete(context_jobs._backoff_key(self.state.id))
        pending = ConversationState.query.filter_by(summary_pending=True).all()  # job nền đã "thấy" cờ...
        self.assertEqual(len(pending), 1)
        self.context()
        self.summarize()  # ...rồi agent tóm tắt xong và xóa cờ trước khi job kịp xử lý
        self.llm_calls.clear()
        self.assertEqual(context_jobs.process_summaries_once(), 0)
        self.assertEqual(self.llm_calls, [], "không gọi LLM lần 2 cho cùng phần tin")

    def test_concurrent_agent_and_job_summarize_at_most_once(self):
        from workers import context_jobs

        self.state.summary_pending = True
        self.db.session.commit()
        self.redis.delete(context_jobs._backoff_key(self.state.id))
        self.context()
        gate = threading.Event()
        original = self.llm_reply

        def slow(messages):
            gate.wait(5)  # giữ khóa trong lúc bên kia thử
            return original(messages)

        self.llm_reply = slow
        results = {}
        worker = threading.Thread(target=lambda: results.setdefault("route", self._route_in_app_context()), daemon=True)
        worker.start()
        deadline = time.time() + 5
        while not self.llm_calls and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(context_jobs.process_summaries_once(), 0, "job nền thấy khóa đang bị giữ")
        gate.set()
        worker.join(10)
        self.assertEqual(results["route"], p.SUMMARY_SUMMARIZED)
        self.assertEqual(len(self.llm_calls), 1)

    def _route_in_app_context(self):
        with self.app.app_context():
            return self.summarize().get_json()["status"]


# ---------------------------------------------------------------- AgentRunner (Redis thật, worker mô phỏng)

class RunnerWithSummarize(unittest.TestCase):
    def setUp(self):
        self.redis = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)
        self.keys = p.Keys(f"test-agent-{uuid.uuid4().hex[:10]}")
        self.addCleanup(lambda: [self.redis.delete(k) for k in self.redis.scan_iter(f"{self.keys.prefix}:*")])
        self.redis.set(self.keys.worker_alive, "1", ex=30)
        allowance = mock.patch.object(rt, "WORKER_START_ALLOWANCE_SECONDS", 0)
        allowance.start()
        self.addCleanup(allowance.stop)
        self.runner = rt.AgentRunner(self.redis, self.keys, "khoa", model="deepseek-v4-flash", internal_url="http://127.0.0.1:5000",
                                     limits=p.AgentLimits(3, 6, 4, 2.0), id_factory=lambda: "job-" + uuid.uuid4().hex[:8])
        self.threads = []

    def fake_worker(self, attempts=(), status=p.STATUS_COMPLETED, terminal=("finish_answer", {"answer": "ok"})):
        """Lấy 1 việc, chụp lại ngữ cảnh Flask đã đặt, giả lập route nội bộ ghi các lần tóm tắt, rồi trả kết quả."""
        seen = {}

        def work():
            popped = self.redis.blpop(self.keys.jobs, timeout=5)
            if not popped:
                return
            job = p.AgentJob.from_json(popped[1])
            seen["job"] = job
            seen["context"] = self.redis.get(self.keys.run_context(job.job_id))
            for item in attempts:
                self.redis.rpush(self.keys.summaries(job.job_id), json.dumps(item))
            self.redis.rpush(self.keys.result(job.job_id), json.dumps({
                "job_id": job.job_id, "status": status, "summary": summary(terminal).as_dict(), "runtime_seconds": 1.0, "error": None, "stop_reason": "",
            }))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.threads.append(thread)
        return seen

    def run_agent(self, *, conversation_id=7, pressure_level="strong", tracker=None, **plan_overrides):
        cfg = settings(**plan_overrides)
        return self.runner.run(bot_id=3, settings=cfg, intents=[], plan=plan_for(**plan_overrides), tracker=tracker or LLMUsageTracker(),
                               conversation_id=conversation_id, pressure_level=pressure_level)

    def test_strong_and_hard_pressure_open_the_tool(self):
        for level in ("strong", "hard"):
            seen = self.fake_worker()
            self.run_agent(pressure_level=level)
            self.assertTrue(seen["job"].allow_summarize, level)
            self.assertEqual(json.loads(seen["context"]), {"bot_id": 3, "conversation_id": 7, "summarize_allowed": True})

    def test_lower_pressure_or_no_saved_conversation_keep_the_tool_closed(self):
        for level, conversation_id in (("normal", 7), ("light", 7), ("strong", None), ("hard", None)):
            seen = self.fake_worker()
            self.run_agent(pressure_level=level, conversation_id=conversation_id)
            self.assertFalse(seen["job"].allow_summarize, (level, conversation_id))
            self.assertFalse(json.loads(seen["context"])["summarize_allowed"])

    def test_the_context_is_removed_when_the_turn_ends(self):
        seen = self.fake_worker()
        self.run_agent()
        job_id = seen["job"].job_id
        self.assertEqual((self.redis.exists(self.keys.run_context(job_id)), self.redis.exists(self.keys.summary_used(job_id))), (0, 0))

    def test_the_context_is_removed_even_when_the_run_fails(self):
        seen = self.fake_worker(status=p.STATUS_FAILED)
        with self.assertRaises(rt.AgentRunError):
            self.run_agent()
        self.assertEqual(self.redis.exists(self.keys.run_context(seen["job"].job_id)), 0)

    def test_the_prompt_mentions_the_tool_only_when_it_is_allowed(self):
        hint_vi = prompts.texts("vi")["agent_summarize_hint"]
        seen = self.fake_worker()
        self.run_agent(pressure_level="strong")
        self.assertIn(hint_vi, seen["job"].input)
        seen = self.fake_worker()
        self.run_agent(pressure_level="normal")
        self.assertNotIn(hint_vi, seen["job"].input)
        self.assertNotIn("summarize_conversation", seen["job"].input)

    def test_the_persona_does_not_depend_on_the_permission(self):
        first, second = self.fake_worker(), None
        self.run_agent(pressure_level="strong")
        second = self.fake_worker()
        self.run_agent(pressure_level="normal")
        self.assertEqual(first["job"].persona, second["job"].persona)
        self.assertEqual(first["job"].process_key, second["job"].process_key)

    def test_summary_usage_is_added_to_the_turn_cost_as_an_extra_call(self):
        call = {"kind": "summary", "prompt_tokens": 800, "completion_tokens": 90, "total_tokens": 890, "cache_hit_tokens": 512,
                "cache_miss_tokens": 288, "conversation_id": 7, "bot_id": 3}
        self.fake_worker(attempts=[{"status": p.SUMMARY_SUMMARIZED, "calls": [call]}])
        tracker = LLMUsageTracker()
        result = self.run_agent(tracker=tracker)
        self.assertEqual(result.info["summaries"], [p.SUMMARY_SUMMARIZED])
        (extra,) = tracker.extra_calls
        self.assertEqual((extra["kind"], extra["prompt_tokens"], extra["completion_tokens"], extra["cache_hit_tokens"], extra["conversation_id"]),
                         ("summary", 800, 90, 512, 7))
        self.assertEqual(tracker.main_call_count, 2, "usage tóm tắt không lẫn vào lệnh gọi chính của agent (Message.usage_*)")
        self.assertEqual(self.redis.exists(self.keys.summaries(result.info["execution_id"])), 0)

    def test_summary_cost_is_not_lost_when_the_run_then_fails(self):
        call = {"kind": "summary", "prompt_tokens": 800, "completion_tokens": 90, "total_tokens": 890, "cache_hit_tokens": 0, "cache_miss_tokens": 800}
        self.fake_worker(attempts=[{"status": p.SUMMARY_SUMMARIZED, "calls": [call]}], status=p.STATUS_TIMEOUT)
        tracker = LLMUsageTracker()
        with self.assertRaises(rt.AgentRunError) as caught:
            self.run_agent(tracker=tracker)
        self.assertEqual(caught.exception.info["summaries"], [p.SUMMARY_SUMMARIZED])
        self.assertEqual(len(tracker.extra_calls), 1)

    def test_no_attempt_means_an_empty_list(self):
        self.fake_worker()
        self.assertEqual(self.run_agent().info["summaries"], [])

    def test_garbage_in_the_attempt_list_is_skipped_not_fatal(self):
        self.redis.rpush(self.keys.summaries("job-rac"), "khong-json", json.dumps([1]), json.dumps({"status": p.SUMMARY_BUSY, "calls": ["x", 3]}))
        tracker = LLMUsageTracker()
        self.assertEqual(self.runner._take_summaries("job-rac", tracker), [p.SUMMARY_BUSY])
        self.assertEqual(tracker.calls, [])


class RenderingAndPermission(unittest.TestCase):
    def test_permission_matrix(self):
        cfg = settings()
        cases = [
            (7, "normal", False), (7, "light", False), (7, "strong", True), (7, "hard", True),
            (None, "strong", False), (None, "hard", False),
        ]
        for conversation_id, level, expected in cases:
            self.assertEqual(rt.summarize_allowed(cfg, conversation_id, level), expected, (conversation_id, level))

    def test_summary_disabled_never_allows_it(self):
        cfg = dataclasses.replace(settings(), summary_enabled=False)
        self.assertFalse(rt.summarize_allowed(cfg, 7, "hard"))

    def test_unknown_pressure_level_is_denied(self):
        self.assertFalse(rt.summarize_allowed(settings(), 7, "extreme"))

    def test_hint_is_rendered_only_on_request_and_in_the_bot_language(self):
        plan = plan_for()
        self.assertNotIn("summarize_conversation", rt.render_input(plan))
        self.assertIn(prompts.texts("vi")["agent_summarize_hint"], rt.render_input(plan, summarize_hint=True))
        english = plan_for(language="en")
        self.assertIn(prompts.texts("en")["agent_summarize_hint"], rt.render_input(english, summarize_hint=True))
        self.assertIn("summarize_conversation", prompts.texts("vi")["agent_summarize_hint"])
        self.assertIn("summarize_conversation", prompts.texts("en")["agent_summarize_hint"])

    def test_hint_comes_before_the_question_so_the_question_stays_last_before_the_reminder(self):
        text = rt.render_input(plan_for(question="Câu hỏi hiện tại XYZ"), summarize_hint=True)
        self.assertLess(text.index("summarize_conversation"), text.index("Câu hỏi hiện tại XYZ"))


# ---------------------------------------------------------------- engine/service truyền mức áp lực

class EngineTellsTheRunnerThePressure(AgentEffectCase):
    def test_normal_context_is_reported_as_normal_with_the_conversation(self):
        conversation = self.conversation()
        self.ask("Giá gói Pro?", conversation)
        call = self.call()
        self.assertEqual((call["pressure_level"], call["conversation_id"]), ("normal", conversation.id))

    def test_a_long_conversation_over_a_small_budget_is_reported_as_strong_or_hard(self):
        self.set_settings(config_tier="advanced", recent_message_limit=30, recent_token_limit=8000)  # ngân sách ngữ cảnh cố định: tăng độ dài hội thoại
        conversation = self.conversation()
        self.fill_history(conversation, 30, words=120)
        message = self.ask("Giá gói Pro?", conversation)
        self.assertEqual(self.call()["pressure_level"], message.decision_trace["pressure_level"])
        self.assertIn(self.call()["pressure_level"], rt.SUMMARIZE_LEVELS)

    def test_the_chat_preview_has_no_saved_conversation_so_it_can_never_summarize(self):
        from app.dashboard import service

        service.preview_reply(self.bot, "Giá gói Pro?", [])
        self.assertIsNone(self.call()["conversation_id"])
        self.assertFalse(rt.summarize_allowed(settings(), self.call()["conversation_id"], "hard"))

    def test_the_trace_records_what_the_agent_did_with_the_tool(self):
        # runner giả có thể mang info["summaries"] -> decision_trace["agent"] (để trang Lịch sử chat/audit thấy)
        original = self.runner.run

        def with_summaries(**kwargs):
            result = original(**kwargs)
            result.info["summaries"] = [p.SUMMARY_SUMMARIZED]
            return result

        self.runner.run = with_summaries
        message = self.ask()
        self.assertEqual(message.decision_trace["agent"]["summaries"], [p.SUMMARY_SUMMARIZED])


if __name__ == "__main__":
    unittest.main()
