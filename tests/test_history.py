"""Phase 5 — Historical Retrieval: collection riêng history_<bot_id>, chỉ tìm trong đúng hội thoại."""
from datetime import datetime

from core import rag_engine
from core.context_engine import history_retrieval as hr
from tests.chroma_fixture import InMemoryChroma


def msg(mid, conv, sender, content):
    return {"message_id": mid, "conversation_id": conv, "sender": sender, "content": content, "created_at": datetime(2026, 9, 21, 10, mid)}


class History(InMemoryChroma):
    def setUp(self):
        super().setUp()
        hr.index_messages(self.bot_id, [
            msg(1, 10, "customer", "toi ten la Nam sdt 0901234567"),
            msg(2, 10, "bot", "chao anh Nam"),
            msg(3, 10, "customer", "toi can 3 may tinh ngan sach 30 trieu"),
            msg(4, 20, "customer", "toi ten la Lan sdt 0987654321"),  # HỘI THOẠI KHÁC
        ])

    def search(self, question, conversation=10, exclude=frozenset(), threshold=1.6):
        return hr.search_history(self.bot_id, conversation, question, exclude_message_ids=set(exclude), distance_threshold=threshold)

    def test_uses_a_separate_collection_from_knowledge(self):
        self.assertEqual(hr.history_collection_name(7), "history_7")
        self.assertNotEqual(hr.history_collection_name(7), rag_engine.get_collection_name(7))
        self.assertEqual(rag_engine.get_collection(self.bot_id).count(), 0, "lịch sử chat không lẫn vào kho tri thức")
        self.assertEqual(hr.get_history_collection(self.bot_id).count(), 4)

    def test_finds_relevant_message_in_same_conversation(self):
        hits = self.search("ten toi la gi sdt")
        self.assertEqual(hits[0]["message_id"], 1)

    def test_never_returns_other_conversations(self):
        ids = {h["message_id"] for h in self.search("ten toi la sdt", conversation=10)}
        self.assertNotIn(4, ids)
        self.assertEqual({h["message_id"] for h in self.search("ten toi la sdt", conversation=20)}, {4})

    def test_unknown_conversation_returns_nothing(self):
        self.assertEqual(self.search("ten toi la", conversation=999), [])

    def test_excludes_messages_already_in_prompt(self):
        ids = [h["message_id"] for h in self.search("ten toi la sdt", exclude={1})]
        self.assertNotIn(1, ids)

    def test_distance_threshold_filters_irrelevant(self):
        self.assertEqual(self.search("thoi tiet ha noi hom nay", threshold=0.5), [])

    def test_top_k_limit(self):
        self.assertLessEqual(len(self.search("toi", threshold=1.9)), hr.DEFAULT_TOP_K)

    def test_indexing_is_idempotent(self):
        hr.index_messages(self.bot_id, [msg(1, 10, "customer", "toi ten la Nam sdt 0901234567")])
        self.assertEqual(hr.get_history_collection(self.bot_id).count(), 4)

    def test_blank_messages_are_skipped(self):
        self.assertEqual(hr.index_messages(self.bot_id, [msg(9, 10, "customer", "   ")]), 0)

    def test_empty_collection_returns_nothing(self):
        self.assertEqual(hr.search_history(self.bot_id + 3000, 10, "x", exclude_message_ids=set(), distance_threshold=1.9), [])

    def test_format_is_chronological_and_labelled(self):
        text = hr.format_history_context([
            {"message_id": 3, "sender": "bot", "content": "trả lời sau", "distance": 0.1},
            {"message_id": 1, "sender": "customer", "content": "hỏi trước", "distance": 0.5},
        ], "vi")
        self.assertEqual(text.splitlines(), ["- Khách: hỏi trước", "- Trợ lý: trả lời sau"])
