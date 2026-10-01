"""Hợp đồng của chế độ AI Agent (core/context_engine/agent/protocol.py): token ký, việc gửi worker, tóm tắt event của dsh. Không cần DB/mạng."""
import json
import unittest

from core.context_engine.agent import protocol as p

SECRET = "khoa-bi-mat-cho-test"


def assistant(miss=210, hit=384, out=118, reasoning=21):
    return {"type": "assistant/message", "data": {"usage": {"inputTokens": miss, "cacheReadTokens": hit, "outputTokens": out, "reasoningTokens": reasoning}}}


def call(name, arguments):
    return {"type": "tool/call", "data": {"name": name, "arguments": json.dumps(arguments) if not isinstance(arguments, str) else arguments}}


class RunToken(unittest.TestCase):
    def test_valid_token_verifies_only_for_the_same_bot_and_run(self):
        token = p.sign_run_token(SECRET, 7, "run-1", now=1000)
        self.assertTrue(p.verify_run_token(SECRET, 7, "run-1", token, now=1001))
        self.assertFalse(p.verify_run_token(SECRET, 8, "run-1", token, now=1001), "bot khác")
        self.assertFalse(p.verify_run_token(SECRET, 7, "run-2", token, now=1001), "lượt khác")
        self.assertFalse(p.verify_run_token("khoa-khac", 7, "run-1", token, now=1001), "khóa khác")

    def test_expiry(self):
        token = p.sign_run_token(SECRET, 7, "run-1", now=1000, ttl=60)
        self.assertTrue(p.verify_run_token(SECRET, 7, "run-1", token, now=1059))
        self.assertFalse(p.verify_run_token(SECRET, 7, "run-1", token, now=1061))

    def test_tampering_and_garbage_never_verify_or_raise(self):
        token = p.sign_run_token(SECRET, 7, "run-1", now=1000)
        expires, _, signature = token.partition(".")
        bad = [
            f"{int(expires) + 1000}.{signature}",   # kéo dài hạn nhưng giữ chữ ký
            f"{expires}.{signature[:-1]}0",
            f"{expires}.", ".", "", "abc", "12.zz", None, 5, ["x"], f"-5.{signature}",
        ]
        for value in bad:
            with self.subTest(value=str(value)[:20]):
                self.assertFalse(p.verify_run_token(SECRET, 7, "run-1", value, now=1001))

    def test_wrong_typed_identity_is_rejected(self):
        token = p.sign_run_token(SECRET, 7, "run-1", now=1000)
        for bot_id, run_id in (("7", "run-1"), (True, "run-1"), (7.0, "run-1"), (None, "run-1"), (7, ""), (7, None), (7, 5)):
            self.assertFalse(p.verify_run_token(SECRET, bot_id, run_id, token, now=1001), (bot_id, run_id))


class JobAndKeys(unittest.TestCase):
    def job(self, **over):
        base = dict(job_id="j1", bot_id=3, token="t", persona="Bạn là trợ lý", max_tokens=1100, language="vi", input="Xin chào",
                    internal_url="http://127.0.0.1:5000", model="deepseek-v4-flash", limits=p.AgentLimits(2, 4, 3, 20.0), deadline=123.5)
        base.update(over)
        return p.AgentJob(**base)

    def test_json_roundtrip_keeps_unicode_and_limits(self):
        job = self.job(persona="Trợ lý ✓ 中文", input="Khách: hỏi giá\nTrợ lý: ...")
        again = p.AgentJob.from_json(job.to_json())
        self.assertEqual(again, job)
        self.assertEqual(again.limits.max_runtime_seconds, 20.0)

    def test_process_key_depends_on_bot_and_static_config_only(self):
        base = self.job()
        self.assertEqual(base.process_key, self.job(job_id="khac", input="khác", token="khác", deadline=9).process_key,
                         "phần động của lượt không được đổi tiến trình")
        for changed in (dict(bot_id=4), dict(persona="khác"), dict(max_tokens=900), dict(language="en"), dict(model="m"), dict(internal_url="http://x")):
            self.assertNotEqual(base.process_key, self.job(**changed).process_key, changed)

    def test_keys_use_the_prefix(self):
        keys = p.Keys("t1")
        self.assertEqual((keys.jobs, keys.worker_alive, keys.result("a"), keys.searches("a")),
                         ("t1:jobs", "t1:worker-alive", "t1:result:a", "t1:run:a:searches"))
        self.assertNotEqual(p.Keys("a").jobs, p.Keys("b").jobs)


class SummarizeEvents(unittest.TestCase):
    def test_usage_is_read_per_llm_call_with_cache_split(self):
        s = p.summarize_events([assistant(210, 384, 118, 21), assistant(172, 640, 104, 0)])
        self.assertEqual([(x.miss, x.hit, x.output, x.reasoning) for x in s.steps], [(210, 384, 118, 21), (172, 640, 104, 0)])
        self.assertEqual(s.steps[0].prompt, 594)

    def test_missing_or_invalid_usage_counts_as_a_zero_step_not_a_crash(self):
        s = p.summarize_events([{"type": "assistant/message", "data": {}}, {"type": "assistant/message"}, assistant(-5, "x", None, True)])
        self.assertEqual(len(s.steps), 3)
        self.assertEqual([x.prompt for x in s.steps], [0, 0, 0])

    def test_tool_calls_are_parsed_and_prefix_is_removed(self):
        s = p.summarize_events([call("mcp__kb__search_knowledge_base", {"query": "giá"}), call("mcp__kb__finish_answer", {"answer": "500k"})])
        self.assertEqual([c["name"] for c in s.tool_calls], ["search_knowledge_base", "finish_answer"])
        self.assertEqual(s.search_calls, 1)
        self.assertEqual(s.terminal, {"name": "finish_answer", "arguments": {"answer": "500k"}})
        self.assertEqual(p.tool_calls_before_terminal(s), 1)

    def test_last_terminal_wins_and_is_counted(self):
        s = p.summarize_events([call("mcp__kb__finish_answer", {"answer": "a"}), call("mcp__kb__decline", {"reason": "x"})])
        self.assertEqual((s.terminal["name"], s.terminal_count), ("decline", 2))

    def test_foreign_tools_are_kept_but_never_terminal(self):
        s = p.summarize_events([call("pwsh", {"command": "whoami"}), call("mcp__other__finish_answer", {})])
        self.assertIsNone(s.terminal)
        self.assertEqual([c["name"] for c in s.tool_calls], ["pwsh", "mcp__other__finish_answer"])

    def test_malformed_arguments_become_empty_dict(self):
        for raw in ("khong-phai-json", "[1,2]", "", None, 5, '"chuỗi"'):
            s = p.summarize_events([call("mcp__kb__finish_answer", raw)])
            self.assertEqual(s.terminal["arguments"], {}, raw)

    def test_finish_reason_and_final_text(self):
        events = [{"type": "turn/end", "data": {"reason": {"kind": "completed"}}}]
        s = p.summarize_events(events, "ok")
        self.assertEqual((s.finish_reason, s.final_text), ("completed", "ok"))
        self.assertEqual(p.summarize_events(events, "", "error").finish_reason, "error")
        self.assertIsNone(p.summarize_events([{"type": "turn/end", "data": {"reason": "x"}}]).finish_reason)

    def test_garbage_events_are_ignored(self):
        s = p.summarize_events([None, 5, "x", {}, {"type": 3}, {"type": "tool/call", "data": None}, {"type": "assistant/message", "data": "x"}])
        self.assertEqual((len(s.steps), s.terminal), (1, None))

    def test_summary_dict_roundtrip(self):
        s = p.summarize_events([assistant(), call("mcp__kb__ask_clarification", {"question": "Bạn cần gì?"}), assistant(1, 2, 3, 4)], "ok", "completed")
        again = p.RunSummary.from_dict(json.loads(json.dumps(s.as_dict())))
        self.assertEqual(again.as_dict(), s.as_dict())
        self.assertEqual(again.terminal["name"], "ask_clarification")

    def test_from_dict_tolerates_missing_fields(self):
        s = p.RunSummary.from_dict({})
        self.assertEqual((s.steps, s.tool_calls, s.terminal, s.final_text), ([], [], None, ""))


if __name__ == "__main__":
    unittest.main()
