"""Worker việc nền (workers/context_jobs.py) và tình huống đồng thời."""
from unittest import mock

from app.models import ConversationState
from core.context_engine import state as ctx_state
from core.context_engine.structured import LLMReply
from tests.chroma_fixture import InMemoryChroma
from tests.db_case import DbCase
from tests.helpers import usage


class SummaryScheduling(DbCase):
    def setUp(self):
        super().setUp()
        from extensions import redis_client
        from workers import context_jobs

        self.worker, self.redis = context_jobs, redis_client
        self.set_settings(recent_message_limit=4)
        self.conv = self.conversation()
        for i in range(10):
            self.add_message(self.conv, "customer" if i % 2 == 0 else "bot", f"tin so {i}")
        self.state = ctx_state.get_or_create_state(self.conv)
        self.state.summary_pending = True
        self.db.session.commit()
        self.addCleanup(self.redis.delete, self.worker._backoff_key(self.state.id))
        self.redis.delete(self.worker._backoff_key(self.state.id))

    def test_pending_conversation_is_summarised_once(self):
        with mock.patch("core.context_engine.jobs.summary_llm_call", lambda max_tokens: lambda messages: LLMReply("Tóm tắt.", usage())):
            self.assertEqual(self.worker.process_summaries_once(), 1)
            self.assertEqual(self.worker.process_summaries_once(), 0, "đã tóm tắt hết phần ngoài cửa sổ gần đây")
        self.db.session.expire_all()
        state = ConversationState.query.filter_by(conversation_id=self.conv.id).one()
        self.assertEqual((state.summary, state.summary_pending), ("Tóm tắt.", False))

    def test_failure_backs_off_instead_of_hammering_the_llm(self):
        calls = []

        def broken(max_tokens):
            def call(messages):
                calls.append(1)
                raise ConnectionError("mạng đứt")
            return call

        with mock.patch("core.context_engine.jobs.summary_llm_call", broken):
            self.assertEqual(self.worker.process_summaries_once(), 0)
            self.assertEqual(self.worker.process_summaries_once(), 0)
        self.assertEqual(len(calls), 1, "lần 2 nằm trong thời gian chờ sau lỗi nên không gọi lại LLM")
        self.db.session.expire_all()
        self.assertTrue(ConversationState.query.filter_by(conversation_id=self.conv.id).one().summary_pending, "cờ giữ nguyên để thử lại sau")
        self.assertTrue(self.redis.exists(self.worker._backoff_key(self.state.id)))

    def test_one_failing_conversation_does_not_block_others(self):
        other = self.conversation()
        for i in range(10):
            self.add_message(other, "customer" if i % 2 == 0 else "bot", f"khac {i}")
        other_state = ctx_state.get_or_create_state(other)
        other_state.summary_pending = True
        self.db.session.commit()
        self.addCleanup(self.redis.delete, self.worker._backoff_key(other_state.id))

        def flaky(max_tokens):
            def call(messages):
                if "tin so" in messages[1]["content"]:
                    raise ConnectionError("chỉ hội thoại đầu lỗi")
                return LLMReply("Tóm tắt khác.", usage())
            return call

        with mock.patch("core.context_engine.jobs.summary_llm_call", flaky):
            self.assertEqual(self.worker.process_summaries_once(), 1)

    def test_lock_is_exclusive_and_owner_only(self):
        first, second = self.worker.ContextJobsLock(), self.worker.ContextJobsLock()
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.redis.delete(self.worker.LOCK_KEY)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire(), "chỉ 1 worker hoạt động tại 1 thời điểm")
        self.assertTrue(first.acquire(), "chủ khoá gia hạn được")
        second.release()  # không phải chủ khoá -> không nhả được
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())

    def test_lock_key_is_separate_from_the_document_worker(self):
        from workers import process_documents

        self.assertNotEqual(self.worker.LOCK_KEY, process_documents.LOCK_KEY)


class EmbedInWorker(DbCase, InMemoryChroma):
    def setUp(self):
        DbCase.setUp(self)
        InMemoryChroma.setUp(self)
        from workers import context_jobs

        self.worker = context_jobs
        self.addCleanup(lambda: rag_client_delete(self.bot.id))

    def test_run_once_embeds_and_reports_work_done(self):
        conv = self.conversation()
        self.add_message(conv, "customer", "xin chao")
        self.add_message(conv, "bot", "chao anh")
        self.assertEqual(self.worker.run_once(), 2)
        self.assertEqual(self.worker.run_once(), 0, "rảnh -> 0 để vòng lặp nghỉ POLL_SECONDS")


def rag_client_delete(bot_id):
    from core import rag_engine

    try:
        rag_engine.chroma_client.delete_collection(f"history_{bot_id}")
    except Exception:  # collection chưa được tạo
        pass


class StateCreationRace(DbCase):
    def test_get_or_create_is_idempotent(self):
        conv = self.conversation()
        first = ctx_state.get_or_create_state(conv)
        self.db.session.commit()
        self.assertEqual(ctx_state.get_or_create_state(conv).id, first.id)
        self.assertEqual(ConversationState.query.filter_by(conversation_id=conv.id).count(), 1)

    def test_losing_the_creation_race_reuses_the_winners_row(self):
        conv = self.conversation()
        winner = ctx_state.get_or_create_state(conv)
        self.db.session.commit()
        real_first = ConversationState.query.filter_by

        calls = {"n": 0}

        class Blind:
            """Lần tra cứu ĐẦU báo 'chưa có' (như request thứ 2 chạy song song), các lần sau thấy thật."""

            def first(self_inner):
                calls["n"] += 1
                return None if calls["n"] == 1 else real_first(conversation_id=conv.id).first()

            def one(self_inner):
                return real_first(conversation_id=conv.id).one()

        with mock.patch.object(ConversationState.query.__class__, "filter_by", lambda *a, **k: Blind()):
            state = ctx_state.get_or_create_state(conv)
        self.assertEqual(state.id, winner.id)
        self.assertEqual(ConversationState.query.filter_by(conversation_id=conv.id).count(), 1)
        self.db.session.commit()  # giao dịch vẫn dùng được sau khi savepoint bị hủy
