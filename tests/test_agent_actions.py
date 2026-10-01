"""Website Action Engine (Phase M3) phía AGENT: công cụ hành động trong máy chủ MCP (danh sách, gọi, chuyển kết quả THẬT, không bịa "đã xong"), hợp đồng
worker (process_key, run file, env), persona, AgentRunner nhận danh sách hành động từ callback. Không cần DB/mạng (Redis thật cho phần AgentRunner).
Đây là kiểm tra hồi quy ở mức mã — KHÔNG thay cho kiểm thử chức năng thực tế."""
import json
import threading
import time
import unittest
import urllib.error
import uuid
from unittest import mock

import redis

from config import Config
from core.context_engine.agent import harness_backend as hb
from core.context_engine.agent import kb_mcp_server as kb
from core.context_engine.agent import protocol as p
from core.context_engine.agent import runtime as rt
from core.context_engine.cost import LLMUsageTracker
from tests.helpers import settings
from tests.test_agent_mcp import McpCase, Recorder
from tests.test_agent_runtime import plan_for, summary

ACTIONS = [
    {"name": "doc_gia", "description": "Dùng khi khách hỏi giá sản phẩm đang xem.", "params": [], "confirm": False},
    {"name": "them_gio", "description": "Dùng khi khách muốn thêm vào giỏ.", "params": [{"name": "so_luong"}], "confirm": False},
    {"name": "dat_hang", "description": "Dùng khi khách chốt đặt hàng.", "params": [{"name": "ho_ten"}, {"name": "sdt"}], "confirm": True},
]


class ActionMcpCase(McpCase):
    def write_run(self, run_id, token, max_search=3, allow_actions=True, max_actions=2, wait=8.0):
        with open(self.run_file, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "token": token, "max_search_calls": max_search, "allow_actions": allow_actions,
                       "max_action_calls": max_actions, "action_wait_seconds": wait}, f)

    def server(self, opener=None, language="vi", bot="21", actions=ACTIONS):
        env = {"KB_BOT_ID": bot, "KB_INTERNAL_URL": "http://127.0.0.1:5000/", "KB_RUN_FILE": self.run_file, "KB_LANGUAGE": language,
               "KB_ACTIONS": json.dumps(actions) if not isinstance(actions, str) else actions}
        return kb.Server(env, opener=opener or Recorder())


class ToolList(ActionMcpCase):
    def tools(self, **kw):
        return self.server(**kw).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]

    def test_action_tools_follow_the_system_tools_with_description_and_string_params(self):
        tools = self.tools()
        self.assertEqual([t["name"] for t in tools], [*p.MODEL_TOOL_NAMES, "doc_gia", "them_gio", "dat_hang"])
        by_name = {t["name"]: t for t in tools}
        self.assertIn("giá sản phẩm", by_name["doc_gia"]["description"])
        self.assertEqual(by_name["them_gio"]["inputSchema"], {"type": "object", "properties": {"so_luong": {"type": "string", "description": "Giá trị do khách cung cấp"}}})
        self.assertEqual(set(by_name["dat_hang"]["inputSchema"]["properties"]), {"ho_ten", "sdt"})
        self.assertIn("xác nhận", by_name["dat_hang"]["description"], "tool thanh toán báo trước là khách sẽ được hỏi xác nhận")
        self.assertNotIn("xác nhận", by_name["doc_gia"]["description"])
        for tool in tools:
            self.assertNotIn("token", tool["inputSchema"]["properties"])
            self.assertNotIn("bot_id", tool["inputSchema"]["properties"])

    def test_no_actions_means_exactly_the_old_tool_list(self):
        for actions in ([], "", "not json", "{}", '"x"', "[1, 2]", '[{"name": ""}]', '[{"description": "thiếu tên"}]'):
            with self.subTest(actions=actions):
                self.assertEqual([t["name"] for t in self.tools(actions=actions)], list(p.MODEL_TOOL_NAMES))

    def test_english_confirm_note(self):
        by_name = {t["name"]: t for t in self.tools(language="en")}
        self.assertIn("confirm", by_name["dat_hang"]["description"])


class ActionCall(ActionMcpCase):
    def opener(self, response):
        return Recorder(response=response)

    def act(self, response, name="them_gio", args=None, language="vi"):
        recorder = self.opener(response)
        result = self.call(self.server(recorder, language=language), name, args if args is not None else {"so_luong": "2"})
        return result, recorder

    def test_request_carries_only_the_signed_context_and_the_models_params(self):
        result, recorder = self.act({"status": "done"}, args={"so_luong": 2, "khong_khai_bao": {"x": 1}, "bot_id": 999, "cờ": True})
        (body,) = recorder.bodies()
        self.assertEqual(body, {"bot_id": 21, "run_id": "run-1", "token": "tok-1", "tool": "them_gio", "params": {"so_luong": 2}})
        self.assertTrue(recorder.requests[0].full_url.endswith("/internal/actions/dispatch"))

    def test_done_is_reported_with_page_data_only_when_the_widget_reported_success(self):
        result, _ = self.act({"status": "done", "data": "199.000đ"})
        self.assertFalse(result["isError"])
        text = self.text(result)
        self.assertIn("Hoàn tất", text)
        self.assertIn("199.000đ", text)
        plain, _ = self.act({"status": "done"})
        self.assertNotIn("Nội dung đọc được", self.text(plain))

    def test_every_non_success_outcome_tells_the_model_not_to_claim_success(self):
        expectations = {
            "failed": ({"status": "failed", "reason": "element_not_found"}, "element_not_found", True),
            "timeout": ({"status": "timeout"}, "KHÔNG biết", True),
            "unknown status": ({"status": "wat"}, "KHÔNG biết", True),
            "garbage": ({"tra": "gi"}, "KHÔNG biết", True),
            "awaiting": ({"status": "awaiting_confirmation"}, "CHỈ chạy khi khách bấm Đồng ý", False),
            "unavailable": ({"status": "unavailable", "reason": "customer_widget_not_connected"}, "customer_widget_not_connected", True),
            "invalid": ({"status": "invalid", "reason": "missing: so_luong"}, "missing: so_luong", True),
            "limit": ({"status": "limit"}, "finish_answer", False),
        }
        for label, (response, needle, is_error) in expectations.items():
            with self.subTest(label=label):
                result, _ = self.act(response)
                self.assertIn(needle, self.text(result))
                self.assertEqual(result["isError"], is_error)
                self.assertNotIn("Hoàn tất", self.text(result))

    def test_network_errors_and_bad_json_are_unknown_outcomes_not_success(self):
        for error in (urllib.error.URLError("refused"), TimeoutError("chậm"), OSError("hỏng")):
            with self.subTest(error=type(error).__name__):
                result = self.call(self.server(Recorder(error=error)), "them_gio", {})
                self.assertTrue(result["isError"])
                self.assertIn("KHÔNG biết", self.text(result))
        bad = self.call(self.server(Recorder(response=b"<html>")), "them_gio", {})
        self.assertTrue(bad["isError"])

    def test_waits_for_the_widget_longer_than_flask_does(self):
        seen = []

        def opener(request, timeout=None):
            seen.append(timeout)
            return Recorder(response={"status": "done"})(request, timeout)

        self.write_run("run-1", "tok-1", wait=8.0)
        self.call(self.server(opener), "them_gio", {})
        self.assertEqual(seen, [8.0 + kb.ACTION_TIMEOUT_MARGIN_SECONDS])

    def test_chat_preview_or_no_session_never_reaches_the_network(self):
        self.write_run("run-1", "tok-1", allow_actions=False)
        recorder = Recorder(error=AssertionError("không được gọi mạng"))
        result = self.call(self.server(recorder), "them_gio", {})
        self.assertTrue(result["isError"])
        self.assertIn("không dùng được", self.text(result))
        self.assertEqual(recorder.requests, [])

    def test_call_budget_is_per_run_and_resets_on_a_new_run(self):
        recorder = Recorder(response={"status": "done"})
        server = self.server(recorder)
        outcomes = [self.text(self.call(server, "doc_gia", {})) for _ in range(3)]
        self.assertIn("Hoàn tất", outcomes[0] + outcomes[1])
        self.assertIn("hết số lần", outcomes[2])
        self.assertEqual(len(recorder.requests), 2, "lần vượt trần không gọi mạng")
        self.write_run("run-2", "tok-2")
        self.assertIn("Hoàn tất", self.text(self.call(server, "doc_gia", {})))
        self.assertEqual(len(recorder.requests), 3)

    def test_unknown_action_name_is_an_error(self):
        self.assertTrue(self.call(self.server(), "khong_co", {})["isError"])

    def test_the_run_file_is_read_per_call_so_a_new_token_is_used(self):
        recorder = Recorder(response={"status": "done"})
        server = self.server(recorder)
        self.call(server, "doc_gia", {})
        self.write_run("run-9", "tok-9")
        self.call(server, "doc_gia", {})
        self.assertEqual([b["token"] for b in recorder.bodies()], ["tok-1", "tok-9"])


class ProtocolContract(unittest.TestCase):
    def job(self, **kw):
        base = dict(job_id="j1", bot_id=9, token="t", persona="Trợ lý", max_tokens=1100, language="vi", input="hỏi", internal_url="http://x", model="m")
        base.update(kw)
        return p.AgentJob(**base)

    def test_action_list_is_part_of_the_process_key_and_bots_without_actions_keep_their_old_key(self):
        plain, with_actions = self.job(), self.job(actions=ACTIONS)
        self.assertNotEqual(plain.process_key, with_actions.process_key, "đổi danh sách tool => tiến trình dsh mới")
        self.assertEqual(with_actions.process_key, self.job(actions=[dict(a) for a in ACTIONS]).process_key)
        self.assertNotEqual(with_actions.process_key, self.job(actions=ACTIONS[:2]).process_key)
        reordered = [{"description": a["description"], "name": a["name"], "params": a["params"], "confirm": a["confirm"]} for a in ACTIONS]
        self.assertEqual(with_actions.process_key, self.job(actions=reordered).process_key, "thứ tự khoá trong dict không đổi khoá")
        # khoá của bot không dùng module giữ nguyên như trước khi có Phase M
        import hashlib

        legacy = hashlib.sha256(json.dumps([9, "Trợ lý", 1100, "vi", "m", "http://x"], ensure_ascii=False).encode("utf-8")).hexdigest()[:20]
        self.assertEqual(plain.process_key, legacy)

    def test_job_roundtrip_keeps_actions_flag_and_limits_and_old_jobs_still_parse(self):
        job = self.job(actions=ACTIONS, allow_actions=True, limits=p.AgentLimits(max_action_calls=3, action_wait_seconds=6.5))
        again = p.AgentJob.from_json(job.to_json())
        self.assertEqual((again.actions, again.allow_actions, again.limits.max_action_calls, again.limits.action_wait_seconds), (ACTIONS, True, 3, 6.5))
        old = json.loads(self.job().to_json())
        for key in ("actions", "allow_actions"):
            old.pop(key)
        for key in ("max_action_calls", "action_wait_seconds"):
            old["limits"].pop(key)
        parsed = p.AgentJob.from_json(json.dumps(old))
        self.assertEqual((parsed.actions, parsed.allow_actions, parsed.limits.max_action_calls), ([], False, 2))

    def test_action_calls_count_towards_the_tool_budget_but_are_never_terminal(self):
        events = []
        for name in ("search_knowledge_base", "them_gio", "doc_gia"):
            events.append({"type": "tool/call", "data": {"name": p.MCP_PREFIX + name, "arguments": "{}"}})
        summ = p.summarize_events(events)
        self.assertEqual([c["name"] for c in summ.tool_calls], ["search_knowledge_base", "them_gio", "doc_gia"])
        self.assertIsNone(summ.terminal)
        self.assertEqual(p.tool_calls_before_terminal(summ), 3)

    def test_action_counter_key_is_namespaced_by_prefix_and_run(self):
        self.assertEqual(p.Keys("agent").actions("r1"), "agent:run:r1:actions")


class HarnessWiring(unittest.TestCase):
    def test_patch_yaml_forwards_the_action_list_through_the_environment(self):
        self.assertIn("KB_ACTIONS: !!js process.env.KB_ACTIONS", hb.patch_yaml())

    def test_process_environment_and_run_file_carry_actions_and_the_per_run_flag(self):
        import tempfile
        from pathlib import Path
        from tests.test_agent_backend import FakeHarness

        FakeHarness.instances, FakeHarness.script = [], []
        with tempfile.TemporaryDirectory() as tmp, mock.patch("deepseek_harness.DeepSeekHarness", FakeHarness, create=True):
            paths = hb.AgentPaths(Path(tmp))
            paths.ensure()
            job = p.AgentJob("j1", 9, "tok", "Trợ lý", 1100, "vi", "hỏi", "http://x", "deepseek-v4-flash", actions=ACTIONS, allow_actions=True,
                             limits=p.AgentLimits(max_action_calls=2, action_wait_seconds=7.0))
            runner = hb.HarnessRunner(job, paths, "key")
            runner.run(job)
            env = FakeHarness.instances[0].kwargs["env"]
            self.assertEqual(json.loads(env["KB_ACTIONS"]), ACTIONS)
            run_file = json.loads(paths.run_file(runner.process_key).read_text(encoding="utf-8"))
            self.assertEqual((run_file["allow_actions"], run_file["max_action_calls"], run_file["action_wait_seconds"]), (True, 2, 7.0))
            runner.close()


class PersonaAndRunner(unittest.TestCase):
    def test_persona_lists_the_action_tools_with_their_descriptions_and_the_honesty_rule(self):
        persona = rt.render_persona(settings(instructions="Bạn là trợ lý An Phát."), [], ACTIONS)
        self.assertIn("Công cụ hành động trên website", persona)
        for action in ACTIONS:
            self.assertIn(f"- {action['name']}: {action['description']}", persona)
        self.assertIn("Kết quả công cụ là sự thật duy nhất", persona)
        english = rt.render_persona(settings(language="en"), [], ACTIONS)
        self.assertIn("only truth", english)

    def test_persona_is_unchanged_for_bots_without_actions(self):
        base = rt.render_persona(settings(instructions="X"), [])
        self.assertEqual(rt.render_persona(settings(instructions="X"), [], None), base)
        self.assertEqual(rt.render_persona(settings(instructions="X"), [], []), base)
        self.assertNotIn("Công cụ hành động", base)


class RunnerWithActions(unittest.TestCase):
    """AgentRunner qua Redis thật; worker mô phỏng bằng luồng."""

    def setUp(self):
        self.redis = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)
        self.keys = p.Keys(f"test-agent-{uuid.uuid4().hex[:10]}")
        self.addCleanup(lambda: [self.redis.delete(k) for k in self.redis.scan_iter(f"{self.keys.prefix}:*")])
        allowance = mock.patch.object(rt, "WORKER_START_ALLOWANCE_SECONDS", 0)
        allowance.start()
        self.addCleanup(allowance.stop)
        self.runner = rt.AgentRunner(self.redis, self.keys, "khoa", model="m", internal_url="http://127.0.0.1:5000",
                                     limits=p.AgentLimits(3, 5, 4, 2.0, max_action_calls=2, action_wait_seconds=8.0), id_factory=lambda: "job-" + uuid.uuid4().hex[:8])
        self.redis.set(self.keys.worker_alive, "1", ex=30)

    def run_with(self, provider, conversation_id=5):
        seen = {}

        def work():
            popped = self.redis.blpop(self.keys.jobs, timeout=5)
            job = p.AgentJob.from_json(popped[1])
            seen["job"] = job
            reply = {"status": "completed", "summary": summary(("finish_answer", {"answer": "ok"})).as_dict(), "runtime_seconds": 1.0, "error": None, "stop_reason": ""}
            self.redis.rpush(self.keys.result(job.job_id), json.dumps({"job_id": job.job_id, **reply}))

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        self.runner.action_provider = provider
        self.runner.run(bot_id=7, settings=settings(), intents=[], plan=plan_for(), tracker=LLMUsageTracker(), conversation_id=conversation_id)
        thread.join(5)
        return seen["job"]

    def test_job_gets_the_bots_actions_extended_limits_and_the_real_session_flag(self):
        asked = []
        job = self.run_with(lambda bot_id: asked.append(bot_id) or ACTIONS)
        self.assertEqual(asked, [7])
        self.assertEqual(job.actions, ACTIONS)
        self.assertTrue(job.allow_actions)
        self.assertIn("- them_gio:", job.persona)
        self.assertEqual(job.limits.max_tool_calls, 5 + 2, "trần dừng cứng cộng số lần gọi hành động")
        self.assertEqual(job.limits.max_runtime_seconds, 2.0 + 2 * (8.0 + 1), "thời gian chờ widget được cộng vào trần thời gian")
        self.assertGreater(job.deadline - time.time(), 2.0 + 2 * 9 - 1)

    def test_chat_preview_lists_the_tools_but_disallows_running_them(self):
        job = self.run_with(lambda bot_id: ACTIONS, conversation_id=None)
        self.assertEqual(job.actions, ACTIONS, "cùng danh sách => cùng tiến trình dsh với chat thật (không tốn thêm RAM)")
        self.assertFalse(job.allow_actions)

    def test_bot_without_actions_is_untouched(self):
        for provider in (None, lambda bot_id: []):
            job = self.run_with(provider)
            self.assertEqual((job.actions, job.allow_actions, job.limits.max_tool_calls, job.limits.max_runtime_seconds), ([], False, 5, 2.0))
            self.assertNotIn("Công cụ hành động", job.persona)


if __name__ == "__main__":
    unittest.main()
