"""Cache Redis kết quả tra cứu của agent (core/context_engine/agent/cache.py): khóa, TTL, vô hiệu khi tài liệu đổi, không lẫn bot/cấu hình,
và hành vi ở route nội bộ. Redis thật (prefix riêng); phần route/hook cần DB *_test."""
import json
import time
import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

import redis

from config import Config
from core import rag_engine
from core.context_engine.agent import cache as ac
from tests.db_case import DbCase
from tests.test_agent_internal import InternalCase
from tests.test_engine import passage, retrieval


def client():
    return redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)


class CacheCase(unittest.TestCase):
    def setUp(self):
        self.redis = client()
        self.prefix = f"test-cache-{uuid.uuid4().hex[:10]}"
        self.addCleanup(lambda: [self.redis.delete(k) for k in self.redis.scan_iter(f"{self.prefix}:*")])
        self.cache = ac.RagCache(self.redis, self.prefix, 60)


class Normalisation(unittest.TestCase):
    def test_case_and_whitespace_are_ignored_but_diacritics_are_not(self):
        self.assertEqual(ac.normalize_query("  Giá   gói\tPro \n"), "giá gói pro")
        self.assertNotEqual(ac.normalize_query("giá"), ac.normalize_query("gia"))

    def test_empty_and_none(self):
        self.assertEqual((ac.normalize_query(""), ac.normalize_query(None), ac.normalize_query("   ")), ("", "", ""))

    def test_signature_changes_with_each_retrieval_setting(self):
        base = dict(rag_top_k=8, rag_rerank_top_n=5, rag_distance_threshold=1.5, rag_max_context_tokens=3000, max_candidate_count=5, language="vi")
        sig = ac.settings_signature(SimpleNamespace(**base))
        self.assertEqual(sig, ac.settings_signature(SimpleNamespace(**base)))
        for name, value in (("rag_top_k", 9), ("rag_rerank_top_n", 4), ("rag_distance_threshold", 1.4), ("rag_max_context_tokens", 2000),
                            ("max_candidate_count", 6), ("language", "en")):
            self.assertNotEqual(sig, ac.settings_signature(SimpleNamespace(**{**base, name: value})), name)


class Store(CacheCase):
    payload = {"text": "Gói Pro 500k", "candidate_count": 2, "top_distance": 0.5}

    def test_put_then_get_roundtrip_with_unicode(self):
        self.cache.put(1, "sig", "Giá gói Pro", self.payload)
        self.assertEqual(self.cache.get(1, "sig", "  giá  GÓI pro "), self.payload)

    def test_miss(self):
        self.assertIsNone(self.cache.get(1, "sig", "chưa có"))

    def test_key_is_scoped_by_bot_and_signature(self):
        self.cache.put(1, "sig", "q", self.payload)
        self.assertIsNone(self.cache.get(2, "sig", "q"), "bot khác không được thấy kết quả của bot này")
        self.assertIsNone(self.cache.get(1, "khac", "q"))

    def test_bump_invalidates_only_that_bot(self):
        self.cache.put(1, "s", "q", self.payload)
        self.cache.put(2, "s", "q", self.payload)
        self.assertEqual(self.cache.bump(1), 1)
        self.assertIsNone(self.cache.get(1, "s", "q"))
        self.assertEqual(self.cache.get(2, "s", "q"), self.payload)
        self.cache.put(1, "s", "q", {"text": "mới"})
        self.assertEqual(self.cache.get(1, "s", "q"), {"text": "mới"})

    def test_ttl_is_applied(self):
        self.cache.put(1, "s", "q", self.payload)
        (key,) = [k for k in self.redis.scan_iter(f"{self.prefix}:ragcache:*")]
        self.assertTrue(0 < self.redis.ttl(key) <= 60)

    def test_zero_ttl_disables_the_cache(self):
        off = ac.RagCache(self.redis, self.prefix, 0)
        off.put(1, "s", "q", self.payload)
        self.assertIsNone(off.get(1, "s", "q"))
        self.assertEqual(list(self.redis.scan_iter(f"{self.prefix}:ragcache:*")), [])

    def test_empty_query_is_never_cached(self):
        self.cache.put(1, "s", "   ", self.payload)
        self.assertIsNone(self.cache.get(1, "s", "   "))
        self.assertEqual(list(self.redis.scan_iter(f"{self.prefix}:ragcache:*")), [])

    def test_corrupted_or_non_object_values_are_a_miss(self):
        self.cache.put(1, "s", "q", self.payload)
        (key,) = list(self.redis.scan_iter(f"{self.prefix}:ragcache:*"))
        for bad in ("không phải json", "[1,2]", "5", '"x"'):
            self.redis.set(key, bad)
            self.assertIsNone(self.cache.get(1, "s", "q"), bad)

    def test_entries_expire(self):
        short = ac.RagCache(self.redis, self.prefix, 1)
        short.put(1, "s", "q", self.payload)
        time.sleep(1.3)
        self.assertIsNone(short.get(1, "s", "q"))


class BumpForBot(unittest.TestCase):
    def test_redis_failure_is_logged_not_raised(self):
        with mock.patch.object(ac, "default_cache", side_effect=redis.ConnectionError("Redis chết")), self.assertLogs("context_engine.agent.cache", "WARNING"):
            ac.bump_for_bot(7)

    def test_other_errors_are_not_swallowed(self):
        with mock.patch.object(ac, "default_cache", side_effect=ValueError("lỗi lập trình")):
            with self.assertRaises(ValueError):
                ac.bump_for_bot(7)


class RouteCaching(InternalCase):
    def cached_flags(self):
        return [r["cached"] for r in self.recorded()]

    def test_same_question_is_retrieved_once_and_answers_are_identical(self):
        first = self.post(query="Giá gói Pro?").get_json()
        second = self.post(query="  giá GÓI pro?  ").get_json()
        self.assertEqual(first, second)
        self.assertEqual(len(self.retrieve_calls), 1)
        self.assertEqual(self.cached_flags(), [False, True])

    def test_cached_searches_are_still_recorded_for_the_turn(self):
        self.post(query="q1", run_id="run-a")
        self.post(query="q1", run_id="run-b")
        self.assertEqual([r["candidate_count"] for r in self.recorded("run-a")], [3])
        self.assertEqual([r["candidate_count"] for r in self.recorded("run-b")], [3])
        self.assertEqual(self.recorded("run-b")[0]["cached"], True)

    def test_different_questions_are_not_confused(self):
        self.post(query="giá")
        self.post(query="địa chỉ")
        self.assertEqual(len(self.retrieve_calls), 2)

    def test_bots_do_not_share_cached_results(self):
        other_team = self.make_team("Team B")
        other = self.service.create_bot(other_team.id, "Bot B")
        self.post(query="giá")
        self.retrieval = retrieval(passages=[passage("Tài liệu RIÊNG của bot B: 999k")])
        data = self.post(bot=other, query="giá").get_json()
        self.assertEqual(len(self.retrieve_calls), 2)
        self.assertIn("RIÊNG của bot B", data["text"])
        self.assertNotIn("RIÊNG của bot B", self.post(query="giá").get_json()["text"])

    def test_changing_a_setting_that_shapes_the_result_bypasses_the_old_entry(self):
        self.post(query="giá")
        self.set_settings(language="en")
        self.post(query="giá")
        self.assertEqual(len(self.retrieve_calls), 2)

    def test_stored_engine_internal_settings_are_ignored_so_they_keep_the_entry(self):
        self.post(query="giá")
        self.set_settings(rag_top_k=3, rag_distance_threshold=1.0)  # tham số nội bộ: không còn ảnh hưởng truy xuất/khóa cache
        self.post(query="giá")
        self.assertEqual(len(self.retrieve_calls), 1)

    def test_bump_after_a_document_change_forces_a_fresh_retrieval(self):
        self.post(query="giá")
        ac.RagCache(self.redis, self.prefix, 300).bump(self.bot.id)
        self.retrieval = retrieval(passages=[passage("Giá mới: 600.000đ")])
        self.assertIn("600.000", self.post(query="giá").get_json()["text"])
        self.assertEqual(len(self.retrieve_calls), 2)

    def test_disabled_ttl_never_caches(self):
        self.app.config["AGENT_RAG_CACHE_SECONDS"] = 0
        self.addCleanup(self.app.config.__setitem__, "AGENT_RAG_CACHE_SECONDS", Config.AGENT_RAG_CACHE_SECONDS)
        self.post(query="giá")
        self.post(query="giá")
        self.assertEqual(len(self.retrieve_calls), 2)

    def test_no_context_results_are_cached_too(self):
        self.retrieval = retrieval(count=0)
        self.post(query="thời tiết")
        self.post(query="thời tiết")
        self.assertEqual(len(self.retrieve_calls), 1)

    def test_rejected_requests_never_touch_the_cache(self):
        self.post(token="sai", query="giá")
        self.assertEqual(list(self.redis.scan_iter(f"{self.prefix}:ragcache:*")), [])


class DocumentHooks(DbCase):
    """Huấn luyện/xóa tài liệu phải vô hiệu cache của ĐÚNG bot."""

    def make_document(self):
        from app.models import Document

        document = Document(bot_id=self.bot.id, filename="a.md", storage_path="x/a.md", status="pending")
        self.db.session.add(document)
        self.db.session.commit()
        return document

    def test_successful_training_bumps_the_bots_cache(self):
        document = self.make_document()
        with mock.patch.object(ac, "bump_for_bot") as bump, mock.patch.object(rag_engine, "run_blocking", lambda fn, *a: fn(*a)), \
                mock.patch.object(rag_engine, "chunk_markdown", lambda *a: ["đoạn"]), mock.patch.object(rag_engine, "upsert_chunks", lambda *a, **k: 1):
            self.service.process_document(self.bot, document, b"# Tieu de\nnoi dung")
        bump.assert_called_once_with(self.bot.id)
        self.assertEqual(document.status, "trained")

    def test_failed_training_also_bumps_because_chunks_may_be_partly_written(self):
        document = self.make_document()
        with mock.patch.object(ac, "bump_for_bot") as bump, mock.patch.object(rag_engine, "run_blocking", lambda fn, *a: fn(*a)), \
                mock.patch.object(rag_engine, "chunk_markdown", lambda *a: ["đoạn"]), \
                mock.patch.object(rag_engine, "upsert_chunks", side_effect=RuntimeError("Chroma lỗi giữa chừng")):
            with self.assertRaises(RuntimeError):
                self.service.process_document(self.bot, document, b"# T\nx")
        bump.assert_called_once_with(self.bot.id)
        self.db.session.refresh(document)
        self.assertEqual(document.status, "failed")

    def test_deleting_a_document_bumps_the_bots_cache(self):
        document = self.make_document()
        with mock.patch.object(ac, "bump_for_bot") as bump, mock.patch.object(rag_engine, "delete_document"), \
                mock.patch("app.dashboard.service.storage_service.delete_file"):
            self.service.delete_document(self.bot, document)
        bump.assert_called_once_with(self.bot.id)

    def test_bump_targets_only_that_bot_end_to_end(self):
        other_team = self.make_team("Team B")
        other = self.service.create_bot(other_team.id, "Bot B")
        prefix = f"test-cache-{uuid.uuid4().hex[:10]}"
        self.addCleanup(lambda: [client().delete(k) for k in client().scan_iter(f"{prefix}:*")])
        cache = ac.RagCache(client(), prefix, 60)
        cache.put(self.bot.id, "s", "q", {"text": "a"})
        cache.put(other.id, "s", "q", {"text": "b"})
        with mock.patch.object(ac, "default_cache", lambda: cache):
            ac.bump_for_bot(self.bot.id)
        self.assertIsNone(cache.get(self.bot.id, "s", "q"))
        self.assertEqual(cache.get(other.id, "s", "q"), {"text": "b"})


if __name__ == "__main__":
    unittest.main()
