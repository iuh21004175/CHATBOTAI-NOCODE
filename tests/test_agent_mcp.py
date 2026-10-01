"""Máy chủ MCP của agent (core/context_engine/agent/kb_mcp_server.py): giao thức, giới hạn tra cứu theo lượt, danh tính không do model truyền,
xử lý lỗi. Có 1 test chạy tiến trình con thật (đúng cách dsh khởi chạy nó). Không cần DB/mạng."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error

from core.context_engine.agent import kb_mcp_server as kb
from core.context_engine.agent.protocol import MODEL_TOOL_NAMES

SCRIPT = kb.__file__


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8") if not isinstance(body, bytes) else body

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder:
    """Thay urllib.request.urlopen: ghi lại request, trả phản hồi dựng sẵn hoặc ném lỗi."""

    def __init__(self, response=None, error=None):
        self.requests, self.response, self.error = [], response, error

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise self.error
        return FakeResponse(self.response if self.response is not None else {"text": "Gói Pro 500.000đ", "candidate_count": 1})

    def bodies(self):
        return [json.loads(r.data.decode("utf-8")) for r in self.requests]


class McpCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.run_file = os.path.join(self.dir.name, "run.json")
        self.write_run("run-1", "tok-1", 3)

    def write_run(self, run_id, token, max_search=3):
        with open(self.run_file, "w", encoding="utf-8") as f:
            json.dump({"run_id": run_id, "token": token, "max_search_calls": max_search}, f)

    def server(self, opener=None, language="vi", bot="21"):
        env = {"KB_BOT_ID": bot, "KB_INTERNAL_URL": "http://127.0.0.1:5000/", "KB_RUN_FILE": self.run_file, "KB_LANGUAGE": language}
        return kb.Server(env, opener=opener or Recorder())

    def call(self, server, name, arguments, msg_id=1):
        response = server.handle({"jsonrpc": "2.0", "id": msg_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
        return response["result"]

    def text(self, result):
        return result["content"][0]["text"]


class Protocol(McpCase):
    def test_initialize_echoes_the_clients_protocol_version(self):
        r = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}})
        self.assertEqual(r["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("tools", r["result"]["capabilities"])
        default = self.server().handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
        self.assertEqual(default["result"]["protocolVersion"], kb.PROTOCOL_FALLBACK)

    def test_tools_list_matches_the_names_the_protocol_expects(self):
        tools = self.server().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], list(MODEL_TOOL_NAMES))
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertTrue(tool["description"])
        by_name = {t["name"]: t for t in tools}
        self.assertEqual(by_name["search_knowledge_base"]["inputSchema"]["required"], ["query"])
        self.assertEqual(by_name["finish_answer"]["inputSchema"]["required"], ["answer"])
        self.assertIn("slots", by_name["finish_answer"]["inputSchema"]["properties"])
        for tool in tools:
            self.assertNotIn("bot_id", tool["inputSchema"]["properties"], "model không được truyền danh tính bot")
            self.assertNotIn("token", tool["inputSchema"]["properties"])

    def test_english_descriptions(self):
        tools = self.server(language="en").handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        self.assertIn("Search", tools[0]["description"])

    def test_notifications_get_no_response_and_unknown_methods_error(self):
        server = self.server()
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        error = server.handle({"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
        self.assertEqual(error["error"]["code"], -32601)
        self.assertEqual(server.handle({"jsonrpc": "2.0", "id": 10, "method": "ping"})["result"], {})

    def test_terminal_tools_only_acknowledge(self):
        server = self.server(Recorder(error=AssertionError("không được gọi mạng")))
        for name, args in (("finish_answer", {"answer": "x"}), ("ask_clarification", {"question": "?"}), ("decline", {})):
            result = self.call(server, name, args)
            self.assertFalse(result["isError"])
            self.assertIn("ok", self.text(result))

    def test_unknown_tool_and_non_dict_arguments_are_errors_not_crashes(self):
        server = self.server()
        self.assertTrue(self.call(server, "rm_rf", {})["isError"])
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_knowledge_base", "arguments": "chuỗi"}})
        self.assertEqual(self.text(response["result"]), kb.TEXTS["vi"]["bad_query"])


class Search(McpCase):
    def test_request_carries_identity_from_env_and_run_file_not_from_the_model(self):
        rec = Recorder()
        server = self.server(rec, bot="21")
        result = self.call(server, "search_knowledge_base", {"query": "  Giá gói Pro  ", "bot_id": 999, "token": "gia", "run_id": "gia"})
        self.assertFalse(result["isError"])
        self.assertEqual(self.text(result), "Gói Pro 500.000đ")
        self.assertEqual(rec.bodies(), [{"bot_id": 21, "run_id": "run-1", "token": "tok-1", "query": "Giá gói Pro"}])
        self.assertEqual(rec.requests[0].full_url, "http://127.0.0.1:5000/internal/rag/search")
        self.assertEqual(rec.requests[0].get_method(), "POST")

    def test_query_is_capped(self):
        rec = Recorder()
        self.call(self.server(rec), "search_knowledge_base", {"query": "a" * 5000})
        self.assertEqual(len(rec.bodies()[0]["query"]), kb.MAX_QUERY_CHARS)

    def test_search_budget_is_per_run_and_resets_for_the_next_run(self):
        rec = Recorder()
        server = self.server(rec)
        self.write_run("run-1", "tok-1", 2)
        results = [self.call(server, "search_knowledge_base", {"query": f"q{i}"}) for i in range(4)]
        self.assertEqual([self.text(r) == kb.TEXTS["vi"]["limit"] for r in results], [False, False, True, True])
        self.assertEqual(len(rec.requests), 2, "vượt hạn thì không gọi Flask")
        self.write_run("run-2", "tok-2", 2)
        self.assertNotEqual(self.text(self.call(server, "search_knowledge_base", {"query": "moi"})), kb.TEXTS["vi"]["limit"])
        self.assertEqual(rec.bodies()[-1]["run_id"], "run-2")

    def test_missing_query_does_not_call_flask(self):
        rec = Recorder()
        server = self.server(rec)
        for args in ({}, {"query": ""}, {"query": "   "}, {"query": 5}, {"query": None}):
            self.assertTrue(self.call(server, "search_knowledge_base", args)["isError"], args)
        self.assertEqual(rec.requests, [])

    def test_network_errors_tell_the_model_not_to_guess(self):
        for error in (urllib.error.URLError("refused"), TimeoutError("chậm"), OSError("hỏng")):
            result = self.call(self.server(Recorder(error=error)), "search_knowledge_base", {"query": "gia"})
            self.assertTrue(result["isError"])
            self.assertIn("decline", self.text(result))

    def test_http_error_from_flask_is_reported_the_same_way(self):
        error = urllib.error.HTTPError("http://x", 403, "forbidden", {}, io.BytesIO(b"{}"))
        result = self.call(self.server(Recorder(error=error)), "search_knowledge_base", {"query": "gia"})
        self.assertTrue(result["isError"])

    def test_bad_or_empty_responses_fall_back_to_no_context(self):
        for body in (b"khong-phai-json", {"text": ""}, {"text": None}, {}, [1]):
            result = self.call(self.server(Recorder(response=body)), "search_knowledge_base", {"query": "gia"})
            self.assertEqual(self.text(result), kb.TEXTS["vi"]["no_context"] if not isinstance(body, bytes) else kb.TEXTS["vi"]["unavailable"], body)

    def test_missing_run_file_still_answers_without_identity_leaking(self):
        os.remove(self.run_file)
        rec = Recorder()
        self.call(self.server(rec), "search_knowledge_base", {"query": "gia"})
        self.assertEqual(rec.bodies()[0]["run_id"], None)
        self.assertEqual(rec.bodies()[0]["token"], None)


class RealSubprocess(McpCase):
    def run_child(self, payload: str):
        env = {**os.environ, "KB_BOT_ID": "5", "KB_INTERNAL_URL": "http://127.0.0.1:1", "KB_RUN_FILE": self.run_file, "KB_LANGUAGE": "vi"}
        env.pop("PYTHONIOENCODING", None)  # môi trường thật của dsh không có biến này: tiếng Việt phải đi đúng dù code page là cp1252
        env.pop("PYTHONUTF8", None)
        proc = subprocess.run([sys.executable, SCRIPT], input=payload.encode("utf-8"), capture_output=True, env=env, timeout=30)
        return proc, proc.stdout.decode("utf-8")

    def test_child_process_speaks_json_rpc_over_stdio(self):
        lines = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "finish_answer", "arguments": {"answer": "x"}}},
        ]
        payload = "\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\nrác không phải json\n\n"
        proc, stdout = self.run_child(payload)
        replies = [json.loads(x) for x in stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertEqual(len(replies[1]["result"]["tools"]), 5)
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Tìm thông tin", stdout, "mô tả tiếng Việt phải nguyên vẹn")

    def test_vietnamese_query_survives_the_stdio_round_trip(self):
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_knowledge_base", "arguments": {"query": "Giá gói Pro"}}}
        proc, stdout = self.run_child(json.dumps(message, ensure_ascii=False) + "\n")
        reply = json.loads(stdout)
        self.assertTrue(reply["result"]["isError"], "cổng 1 không có ai nghe -> báo không tra cứu được (không làm chết tiến trình)")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_script_imports_nothing_from_the_project(self):
        source = open(SCRIPT, encoding="utf-8").read()
        for forbidden in ("import app", "from app", "import core", "from core", "import flask", "import extensions", "import config"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
