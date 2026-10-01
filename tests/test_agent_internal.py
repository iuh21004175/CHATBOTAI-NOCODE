"""Route nội bộ /internal/rag/search (công cụ tra cứu của agent gọi vào Flask): xác thực nhiều lớp, tham số theo cấu hình bot, ghi nhận tra cứu.
Cần DB *_test và Redis (xem tests/README.md)."""
import json
import time
import uuid

from core import rag_engine
from core.context_engine.agent import protocol as p
from core.context_engine.settings import DEFAULTS
from tests.db_case import DbCase
from tests.test_engine import passage, retrieval


class InternalCase(DbCase):
    def setUp(self):
        super().setUp()
        from extensions import redis_client

        self.redis = redis_client
        self.prefix = f"test-agent-{uuid.uuid4().hex[:10]}"
        self.old_prefix = self.app.config["AGENT_REDIS_PREFIX"]
        self.app.config["AGENT_REDIS_PREFIX"] = self.prefix
        self.addCleanup(self.restore)
        self.client = self.app.test_client()
        self.run_id = "run-" + uuid.uuid4().hex[:8]
        self.secret = self.app.config["SECRET_KEY"]
        self.retrieve_calls = []
        original = self.retrieval

        def capturing(*args, **kwargs):
            self.retrieve_calls.append((args, kwargs))
            return self.retrieval

        from unittest import mock

        patcher = mock.patch.object(rag_engine, "retrieve", capturing)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.retrieval = original

    def restore(self):
        self.app.config["AGENT_REDIS_PREFIX"] = self.old_prefix
        for key in self.redis.scan_iter(f"{self.prefix}:*"):
            self.redis.delete(key)

    def token(self, bot=None, run_id=None, **kw):
        return p.sign_run_token(self.secret, (bot or self.bot).id, run_id or self.run_id, **kw)

    def post(self, bot=None, token=None, run_id=None, query="Giá gói Pro?", remote="127.0.0.1", headers=None, **extra):
        body = {"bot_id": (bot or self.bot).id, "run_id": run_id or self.run_id, "token": token if token is not None else self.token(bot, run_id), "query": query}
        body.update(extra)
        return self.client.post("/internal/rag/search", json=body, environ_overrides={"REMOTE_ADDR": remote}, headers=headers or {})

    def recorded(self, run_id=None):
        return [json.loads(x) for x in self.redis.lrange(p.Keys(self.prefix).searches(run_id or self.run_id), 0, -1)]


class Success(InternalCase):
    def test_returns_text_and_signals_and_needs_no_login_or_csrf(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("Gói Pro giá 500.000đ/tháng", data["text"])
        self.assertEqual((data["candidate_count"], data["top_distance"], data["knowledge_empty"]), (3, 0.5, False))
        self.assertIn("distance_gap", data)
        self.assertIn("spread_ambiguous", data)

    def test_search_is_recorded_for_the_run_and_expires(self):
        self.post(query="  bảng giá  ")
        (record,) = self.recorded()
        self.assertEqual((record["query"], record["candidate_count"]), ("bảng giá", 3))
        self.assertTrue(0 < self.redis.ttl(p.Keys(self.prefix).searches(self.run_id)) <= 300)

    def test_several_searches_accumulate_in_order_and_are_capped(self):
        for i in range(25):
            self.post(query=f"cau {i}")
        records = self.recorded()
        self.assertEqual(len(records), 20)
        self.assertEqual(records[-1]["query"], "cau 24")

    def test_records_of_different_runs_do_not_mix(self):
        self.post(run_id="run-a", query="a")
        self.post(run_id="run-b", query="b")
        self.assertEqual([r["query"] for r in self.recorded("run-a")], ["a"])
        self.assertEqual([r["query"] for r in self.recorded("run-b")], ["b"])

    def test_retrieval_parameters_are_the_system_defaults_not_the_stored_values(self):
        self.set_settings(rag_top_k=12, rag_rerank_top_n=3, rag_distance_threshold=1.2)  # tham số nội bộ: cột DB không được đọc
        self.post(query="Giá?")
        (args, kwargs), = self.retrieve_calls
        self.assertEqual(args, (self.bot.id, "Giá?"))
        self.assertEqual((kwargs["top_k"], kwargs["rerank_top_n"], kwargs["distance_threshold"]),
                         (DEFAULTS["rag_top_k"], DEFAULTS["rag_rerank_top_n"], DEFAULTS["rag_distance_threshold"]))

    def test_query_is_trimmed_and_capped(self):
        self.post(query="  " + "a" * 2000 + "  ")
        (args, _), = self.retrieve_calls
        self.assertEqual(len(args[1]), 500)

    def test_no_documents_found_returns_the_no_context_text_and_zero_candidates(self):
        self.retrieval = retrieval(count=0)
        data = self.post().get_json()
        self.assertEqual(data["candidate_count"], 0)
        self.assertIn("Không tìm thấy thông tin liên quan", data["text"])
        self.assertEqual(self.recorded()[0]["candidate_count"], 0)

    def test_empty_knowledge_base_is_reported(self):
        self.retrieval = rag_engine.RetrievalResult(knowledge_empty=True)
        data = self.post().get_json()
        self.assertTrue(data["knowledge_empty"])

    def test_text_budget_is_the_system_default_not_the_stored_value(self):
        self.retrieval = retrieval(passages=[passage("noi dung tai lieu rat dai " * 300)])
        default = self.post().get_json()["text"]
        self.set_settings(rag_max_context_tokens=200)  # tham số nội bộ: cột DB không được đọc
        stored = self.post().get_json()["text"]
        self.assertEqual(default, stored)

    def test_language_of_the_bot_selects_the_no_context_text(self):
        self.retrieval = retrieval(count=0)
        self.set_settings(language="en")
        self.assertIn("No relevant information", self.post().get_json()["text"])


class Rejections(InternalCase):
    def denied(self, response, status=403):
        self.assertEqual(response.status_code, status)
        self.assertEqual(self.retrieve_calls, [], "bị từ chối thì không được truy xuất gì")
        self.assertEqual(self.recorded(), [])

    def test_only_loopback_is_accepted(self):
        for remote in ("10.0.0.5", "192.168.1.9", "8.8.8.8", "::2"):
            self.denied(self.post(remote=remote))
        self.assertEqual(self.post(remote="::1").status_code, 200)

    def test_forwarded_headers_cannot_fake_loopback(self):
        self.denied(self.post(remote="8.8.8.8", headers={"X-Forwarded-For": "127.0.0.1", "X-Real-IP": "127.0.0.1", "Forwarded": "for=127.0.0.1"}))

    def test_missing_wrong_or_malformed_token(self):
        good = self.token()
        for token in ("", "abc", ".", "1.2", good + "x", good[:-2], None, 5, ["x"]):
            response = self.client.post("/internal/rag/search", json={"bot_id": self.bot.id, "run_id": self.run_id, "token": token, "query": "gia"})
            self.denied(response)

    def test_token_of_another_bot_cannot_search_this_bot(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.denied(self.post(bot=self.bot, token=self.token(bot=other)))
        self.denied(self.post(bot=other, token=self.token(bot=self.bot)))

    def test_token_of_another_run_is_rejected(self):
        self.denied(self.post(token=self.token(run_id="run-khac")))

    def test_expired_token_is_rejected(self):
        self.denied(self.post(token=self.token(now=time.time() - 1000)))

    def test_a_token_signed_with_another_secret_is_rejected(self):
        forged = p.sign_run_token("khoa-doan", self.bot.id, self.run_id)
        self.denied(self.post(token=forged))

    def test_body_bot_id_cannot_be_swapped_for_another_teams_bot(self):
        other_team = self.make_team("Team B")
        victim = self.service.create_bot(other_team.id, "Bot B")
        self.denied(self.post(bot=victim, token=self.token(bot=self.bot)))

    def test_bad_bodies(self):
        for payload in ("chuỗi", [1], 5, None):
            response = self.client.post("/internal/rag/search", data=json.dumps(payload), content_type="application/json")
            self.assertIn(response.status_code, (400, 403))
        self.assertEqual(self.client.post("/internal/rag/search", data="không phải json", content_type="text/plain").status_code, 400)
        self.assertEqual(self.retrieve_calls, [])

    def test_bad_identity_types(self):
        for bot_id, run_id in (("1", self.run_id), (None, self.run_id), (True, self.run_id), (self.bot.id, None), (self.bot.id, 5), (self.bot.id, "")):
            response = self.client.post("/internal/rag/search", json={"bot_id": bot_id, "run_id": run_id, "token": self.token(), "query": "gia"})
            self.denied(response)

    def test_empty_or_non_string_query_is_a_bad_request(self):
        for query in ("", "   ", None, 5, ["a"], {"a": 1}):
            self.denied(self.post(query=query), status=400)

    def test_signed_token_for_a_deleted_bot_is_404_not_a_crash(self):
        token = p.sign_run_token(self.secret, 987654, self.run_id)
        self.denied(self.client.post("/internal/rag/search", json={"bot_id": 987654, "run_id": self.run_id, "token": token, "query": "gia"}), status=404)

    def test_only_post_is_allowed(self):
        self.assertEqual(self.client.get("/internal/rag/search").status_code, 405)
        self.assertEqual(self.client.put("/internal/rag/search", json={}).status_code, 405)

    def test_error_body_does_not_reveal_why(self):
        bodies = {json.dumps(self.post(token="sai").get_json()), json.dumps(self.post(remote="8.8.8.8").get_json())}
        self.assertEqual(len(bodies), 1, "cùng 1 thông báo cho mọi lý do từ chối (không giúp kẻ dò)")


if __name__ == "__main__":
    import unittest

    unittest.main()
