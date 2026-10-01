"""Lớp bọc Harness (core/context_engine/agent/harness_backend.py): patch an toàn của profile, dọn phiên, giới hạn lượt chạy. Dùng harness GIẢ
(không chạy dsh, không mạng) để ép từng nhánh; phần chạy dsh thật được kiểm chứng riêng (xem tests/test_agent_live.py, cần khóa API)."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from core.context_engine.agent import harness_backend as hb
from core.context_engine.agent import protocol as p


def note(session_id, event):
    return SimpleNamespace(method="session.event", payload={"sessionId": session_id, "event": event})


def step(miss=100, hit=50, out=20):
    return {"type": "assistant/message", "data": {"usage": {"inputTokens": miss, "cacheReadTokens": hit, "outputTokens": out, "reasoningTokens": 0}}}


def tool(name, **arguments):
    return {"type": "tool/call", "data": {"name": f"mcp__kb__{name}", "arguments": json.dumps(arguments)}}


def step_end():
    return {"type": "step/end", "data": {}}


def turn_end(kind="completed"):
    return {"type": "turn/end", "data": {"reason": {"kind": kind}}}


class FakeHarness:
    """Thay deepseek_harness.DeepSeekHarness. script: danh sách event sẽ được phát cho on_notification; hết script thì trả kết quả."""

    instances = []
    script: list = []
    final = "ok"
    block_seconds = 0.0
    fail_with: Exception | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.started = False
        self.client = SimpleNamespace(_proc=SimpleNamespace(poll=lambda: None if not self.closed else 1))
        FakeHarness.instances.append(self)

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def run(self, text, session_id, on_notification):
        self.last = (text, session_id)
        if FakeHarness.fail_with:
            raise FakeHarness.fail_with
        events = []
        for event in FakeHarness.script:
            events.append(event)
            on_notification(note(session_id, event))
        deadline = time.monotonic() + FakeHarness.block_seconds
        while time.monotonic() < deadline:
            if self.closed:
                raise RuntimeError("DeepSeek Harness runtime closed")
            time.sleep(0.01)
        return SimpleNamespace(events=events, final_response=FakeHarness.final, finish_reason="completed")


class BackendCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.paths = hb.AgentPaths(Path(self.dir.name))
        self.paths.ensure()
        FakeHarness.instances, FakeHarness.script, FakeHarness.final, FakeHarness.block_seconds, FakeHarness.fail_with = [], [], "ok", 0.0, None
        patcher = mock.patch("deepseek_harness.DeepSeekHarness", FakeHarness)
        patcher.start()
        self.addCleanup(patcher.stop)

    def job(self, **over):
        base = dict(job_id="abc123", bot_id=9, token="tok", persona="Trợ lý", max_tokens=1100, language="vi", input="Khách hỏi giá",
                    internal_url="http://127.0.0.1:5000", model="deepseek-v4-flash", limits=p.AgentLimits(3, 5, 4, 5.0))
        base.update(over)
        return p.AgentJob(**base)

    def runner(self, job=None):
        job = job or self.job()
        return hb.HarnessRunner(job, self.paths, api_key="sk-test")


class ProfilePatch(unittest.TestCase):
    def test_patch_removes_every_shell_and_the_data_uploaders(self):
        text = hb.patch_yaml("C:/py/python.exe", "C:/x/kb_mcp_server.py")
        for row in ("terminal-pwsh", "terminal-bash", "persistent-pwsh", "persistent-bash", "session-log-deepseek",
                    "plugin-package-inventory-deepseek"):
            self.assertRegex(text, rf"- id: {row}\n  disabled: true", row)

    def test_new_rows_are_inside_insert_and_mcp_is_stdio_kb(self):
        text = hb.patch_yaml("C:/py/python.exe", "C:/x/kb_mcp_server.py")
        insert_at = text.index("- insert:")
        self.assertGreater(text.index("mcp-kb"), insert_at, "hàng mới phải nằm trong insert: (nếu không dsh bỏ qua)")
        self.assertIn("name: '@deepseek-ai/dsh-mcp-client'", text)
        self.assertIn(f"serverName: {p.SERVER_NAME}", text)
        self.assertIn("transport: stdio", text)
        self.assertIn("command: 'C:/py/python.exe'", text)
        self.assertIn("args: ['C:/x/kb_mcp_server.py']", text)
        self.assertIn("failOnStartupError: true", text)

    def test_identity_comes_from_environment_expressions_not_literals(self):
        text = hb.patch_yaml()
        for name in ("KB_BOT_ID", "KB_INTERNAL_URL", "KB_RUN_FILE", "KB_LANGUAGE"):
            self.assertIn(f"{name}: !!js process.env.{name}", text)
        self.assertNotIn("\\", text, "đường dẫn Windows phải đổi sang dấu /")

    def test_default_paths_point_at_this_interpreter_and_the_real_script(self):
        import sys

        text = hb.patch_yaml()
        self.assertIn(sys.executable.replace("\\", "/"), text)
        self.assertIn("kb_mcp_server.py", text)
        self.assertTrue(Path(hb._MCP_SCRIPT).is_file())

    def test_patch_is_valid_yaml_list(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML không có trong môi trường (không thêm dependency chỉ để test)")
        data = yaml.safe_load(hb.patch_yaml("C:/py/python.exe", "C:/x/kb.py").replace("!!js ", ""))
        self.assertIsInstance(data, list)


class Paths(BackendCase):
    def test_ensure_creates_directories_and_is_idempotent(self):
        for directory in (self.paths.dsh_home, self.paths.workspace, self.paths.runs):
            self.assertTrue(directory.is_dir())
        first = self.paths.patch.read_text(encoding="utf-8")
        self.paths.ensure()
        self.assertEqual(self.paths.patch.read_text(encoding="utf-8"), first)

    def test_a_stale_patch_is_rewritten(self):
        self.paths.patch.write_text("cũ", encoding="utf-8")
        self.paths.ensure()
        self.assertEqual(self.paths.patch.read_text(encoding="utf-8"), hb.patch_yaml())

    def test_run_file_is_per_process_key(self):
        self.assertNotEqual(self.paths.run_file("a"), self.paths.run_file("b"))
        self.assertEqual(self.paths.run_file("a").parent, self.paths.runs)


class PurgeSession(BackendCase):
    def make(self, workspace, session_id):
        target = self.paths.dsh_home / "sessions" / workspace / session_id
        target.mkdir(parents=True)
        (target / "session.v3.jsonl").write_text("nội dung khách", encoding="utf-8")
        return target

    def test_removes_only_the_named_session_everywhere(self):
        keep = self.make("--w1--", "j-keep")
        gone1, gone2 = self.make("--w1--", "j-gone"), self.make("--w2--", "j-gone")
        self.assertEqual(hb.purge_session(self.paths, "j-gone"), 2)
        self.assertFalse(gone1.exists() or gone2.exists())
        self.assertTrue(keep.exists())

    def test_hostile_ids_never_delete_anything(self):
        victim = self.make("--w1--", "j-victim")
        outside = Path(self.dir.name) / "ngoai"
        outside.mkdir()
        for bad in ("", "..", "../ngoai", "..\\ngoai", "a/b", "j-victim/..", "*", "j-victim ", None):
            self.assertEqual(hb.purge_session(self.paths, bad), 0, bad)
        self.assertTrue(victim.exists() and outside.exists())

    def test_missing_sessions_dir_is_fine(self):
        self.assertEqual(hb.purge_session(self.paths, "j-x"), 0)


class RunnerBehaviour(BackendCase):
    def test_normal_run_returns_summary_and_writes_trusted_context(self):
        FakeHarness.script = [step(), tool("search_knowledge_base", query="giá"), step(), tool("finish_answer", answer="500k"), step(50, 100, 5), turn_end()]
        runner = self.runner()
        out = runner.run(self.job())
        self.assertEqual(out.status, p.STATUS_COMPLETED)
        self.assertFalse(out.discard_process)
        self.assertEqual((len(out.summary.steps), out.summary.terminal["name"], out.summary.search_calls), (3, "finish_answer", 1))
        self.assertEqual(out.summary.finish_reason, "completed")
        run_file = json.loads(self.paths.run_file(runner.process_key).read_text(encoding="utf-8"))
        self.assertEqual(run_file, {"run_id": "abc123", "token": "tok", "max_search_calls": 3, "allow_summarize": False, "allow_actions": False, "max_action_calls": 2, "action_wait_seconds": 8.0})

    def test_harness_is_started_once_with_isolated_env_and_no_shell_profile_args(self):
        runner = self.runner()
        runner.run(self.job())
        runner.run(self.job(job_id="def456"))
        self.assertEqual(len(FakeHarness.instances), 1, "tiến trình dsh được tái dùng giữa các lượt")
        kwargs = FakeHarness.instances[0].kwargs
        self.assertEqual((kwargs["profile"], kwargs["provider"], kwargs["model"], kwargs["max_tokens"]), ("sdk-minimal", "deepseek-official", "deepseek-v4-flash", 1100))
        self.assertEqual(kwargs["patches"], (str(self.paths.patch),))
        self.assertEqual(kwargs["dsh_home"], str(self.paths.dsh_home))
        self.assertEqual(kwargs["cwd"], str(self.paths.workspace))
        env = kwargs["env"]
        self.assertEqual((env["KB_BOT_ID"], env["DSH_SYSTEM_PROMPT"], env["KB_LANGUAGE"]), ("9", "Trợ lý", "vi"))
        self.assertEqual(env["KB_RUN_FILE"], str(self.paths.run_file(runner.process_key)))
        self.assertEqual(env["DSH_TELEMETRY_DISABLED"], "1")
        self.assertEqual(kwargs["api_key"], "sk-test")

    def test_each_run_uses_a_fresh_session_id_and_purges_it(self):
        runner = self.runner()
        with mock.patch.object(hb, "purge_session") as purge:
            runner.run(self.job(job_id="aaa"))
            runner.run(self.job(job_id="bbb"))
        self.assertEqual([c.args[1] for c in purge.call_args_list], ["j-aaa", "j-bbb"])
        self.assertEqual(FakeHarness.instances[0].last[1], "j-bbb")

    def test_session_is_purged_even_when_the_run_fails(self):
        FakeHarness.fail_with = RuntimeError("nổ")
        with mock.patch.object(hb, "purge_session") as purge:
            self.runner().run(self.job())
        purge.assert_called_once()

    def test_input_text_is_passed_verbatim(self):
        runner = self.runner()
        runner.run(self.job(input="Khách: xin chào\nCâu hỏi"))
        self.assertEqual(FakeHarness.instances[0].last[0], "Khách: xin chào\nCâu hỏi")

    def test_iteration_limit_stops_a_run_that_never_concludes(self):
        FakeHarness.script = [step(), tool("search_knowledge_base", query="a"), step_end(), step(), tool("search_knowledge_base", query="b"), step_end(),
                              step(), step_end(), step(), step_end(), step(), step_end()]
        out = self.runner().run(self.job(limits=p.AgentLimits(3, 9, 4, 5.0)))
        self.assertEqual(out.status, p.STATUS_MAX_ITERATIONS)
        self.assertTrue(out.discard_process)
        self.assertTrue(FakeHarness.instances[0].closed, "agent có thể còn chạy -> đóng tiến trình")
        self.assertEqual(len(out.summary.steps), 4, "dừng đúng khi lượt thứ 4 kết thúc mà chưa kết luận, không chạy tiếp")

    def test_a_terminal_call_in_the_last_allowed_step_is_not_cut(self):
        # event assistant/message của lượt 4 đến TRƯỚC tool/call finish_answer của chính lượt đó
        FakeHarness.script = [step(), step_end(), step(), step_end(), step(), step_end(), step(), tool("finish_answer", answer="x"), step_end(), step(), step_end()]
        out = self.runner().run(self.job(limits=p.AgentLimits(3, 9, 4, 5.0)))
        self.assertEqual(out.status, p.STATUS_COMPLETED)
        self.assertEqual(out.summary.terminal["name"], "finish_answer")

    def test_iteration_limit_does_not_fire_when_a_terminal_tool_was_called(self):
        FakeHarness.script = [step(), step_end(), step(), step_end(), step(), tool("finish_answer", answer="x"), step_end(), step(), step_end()]
        out = self.runner().run(self.job(limits=p.AgentLimits(3, 9, 3, 5.0)))
        self.assertEqual(out.status, p.STATUS_COMPLETED)

    def test_tool_call_limit(self):
        FakeHarness.script = [step()] + [tool("search_knowledge_base", query=str(i)) for i in range(8)]
        out = self.runner().run(self.job(limits=p.AgentLimits(2, 4, 99, 5.0)))
        self.assertEqual(out.status, p.STATUS_MAX_TOOL_CALLS)
        self.assertTrue(out.discard_process)
        self.assertEqual(p.tool_calls_before_terminal(out.summary), 5, "dừng ngay khi vượt trần (4) chứ không chạy tiếp")

    def test_terminal_calls_do_not_count_toward_the_tool_limit(self):
        FakeHarness.script = [step(), tool("search_knowledge_base", query="a"), tool("finish_answer", answer="x")]
        out = self.runner().run(self.job(limits=p.AgentLimits(1, 1, 99, 5.0)))
        self.assertEqual(out.status, p.STATUS_COMPLETED)

    def test_foreign_tool_calls_count_and_never_become_a_decision(self):
        FakeHarness.script = [{"type": "tool/call", "data": {"name": "pwsh", "arguments": '{"command": "whoami"}'}} for _ in range(5)]
        out = self.runner().run(self.job(limits=p.AgentLimits(1, 2, 99, 5.0)))
        self.assertEqual(out.status, p.STATUS_MAX_TOOL_CALLS)
        self.assertIsNone(out.summary.terminal)

    def test_timeout_kills_a_run_that_hangs(self):
        FakeHarness.block_seconds = 30.0
        started = time.monotonic()
        out = self.runner().run(self.job(limits=p.AgentLimits(3, 5, 4, 0.3)))
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(out.status, p.STATUS_TIMEOUT)
        self.assertTrue(out.discard_process)
        self.assertTrue(FakeHarness.instances[0].closed)

    def test_unexpected_error_is_reported_as_failed_not_swallowed(self):
        FakeHarness.fail_with = ValueError("hỏng")
        out = self.runner().run(self.job())
        self.assertEqual(out.status, p.STATUS_FAILED)
        self.assertIn("ValueError: hỏng", out.error)
        self.assertTrue(out.discard_process)

    def test_startup_failure_is_reported_as_failed(self):
        with mock.patch.object(FakeHarness, "start", side_effect=OSError("không chạy được dsh")):
            out = self.runner().run(self.job())
        self.assertEqual(out.status, p.STATUS_FAILED)
        self.assertIn("không chạy được dsh", out.error)

    def test_a_dead_process_is_restarted_for_the_next_run(self):
        runner = self.runner()
        runner.run(self.job())
        FakeHarness.instances[0].closed = True  # tiến trình chết giữa 2 lượt
        runner.run(self.job(job_id="next"))
        self.assertEqual(len(FakeHarness.instances), 2)

    def test_timer_is_cancelled_after_a_normal_run(self):
        before = threading.active_count()
        self.runner().run(self.job(limits=p.AgentLimits(3, 5, 4, 30.0)))
        time.sleep(0.05)
        self.assertLessEqual(threading.active_count(), before, "không để lại Timer treo")

    def test_run_output_dict_is_json_serialisable(self):
        FakeHarness.script = [step(), tool("finish_answer", answer="x")]
        out = self.runner().run(self.job())
        data = json.loads(json.dumps(out.as_dict()))
        self.assertEqual(data["status"], p.STATUS_COMPLETED)
        self.assertEqual(p.RunSummary.from_dict(data["summary"]).terminal["name"], "finish_answer")


if __name__ == "__main__":
    unittest.main()
