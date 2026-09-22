"""Phase 3 — RAG Controller: lọc theo ngưỡng KHOẢNG CÁCH, loại trùng, tín hiệu (top/second/gap/candidate_count),
rerank_top_n, ngân sách token, mở rộng lân cận."""
import unittest

from core import rag_engine
from tests.chroma_fixture import InMemoryChroma

QUERY = "gia goi pro"
PRICE = "gia goi pro 500000 dong"
FEATURES = "goi pro gom 10 nguoi dung"
WARRANTY = "chinh sach bao hanh 12 thang"
HOTLINE = "lien he hotline 1900"


def retrieve(bot_id, question=QUERY, **kw):
    kw.setdefault("top_k", 8)
    kw.setdefault("distance_threshold", 1.5)
    kw.setdefault("rerank_top_n", 5)
    return rag_engine.retrieve(bot_id, question, **kw)


class DistanceMetric(InMemoryChroma):
    def test_similarity_formula_matches_real_cosine(self):
        # Chroma trả BÌNH PHƯƠNG L2: d = 2(1 - cos). Công thức cos = 1 - d²/2 trong yêu cầu ban đầu sẽ SAI.
        self.add_document(1, [PRICE, HOTLINE])
        result = retrieve(self.bot_id, distance_threshold=1.9)
        cos = self.cosine(QUERY, PRICE)
        self.assertAlmostEqual(result.top_distance, 2 * (1 - cos), places=5)
        self.assertAlmostEqual(rag_engine.similarity_from_distance(result.top_distance), cos, places=5)
        wrong = 1 - result.top_distance ** 2 / 2
        self.assertNotAlmostEqual(wrong, cos, places=2)


class ThresholdFilter(InMemoryChroma):
    def setUp(self):
        super().setUp()
        # Mỗi chunk 1 tài liệu riêng: chunk trúng liền kề CÙNG tài liệu bị gộp thành 1 ứng viên (xem test bên dưới)
        for document_id, text in enumerate([PRICE, FEATURES, WARRANTY, HOTLINE], start=1):
            self.add_document(document_id, [text])

    def test_chunks_farther_than_threshold_are_dropped(self):
        d_price = 2 * (1 - self.cosine(QUERY, PRICE))
        d_features = 2 * (1 - self.cosine(QUERY, FEATURES))
        self.assertLess(d_price, 0.6 < d_features)  # dữ liệu thử đúng như thiết kế
        result = retrieve(self.bot_id, distance_threshold=0.6)
        self.assertEqual(result.candidate_count, 1)
        self.assertEqual(result.over_threshold, result.considered - 1)
        hit_chunks = [c for p in result.passages for c in p["chunks"] if c["hit"]]
        self.assertEqual([c["content"] for c in hit_chunks], [PRICE])

    def test_larger_distance_is_worse_not_better(self):
        # Ngược chiều điểm tương đồng: chunk có khoảng cách LỚN HƠN ngưỡng bị loại (không phải "điểm thấp hơn")
        strict = retrieve(self.bot_id, distance_threshold=0.5)
        loose = retrieve(self.bot_id, distance_threshold=1.9)
        self.assertLess(strict.candidate_count, loose.candidate_count)

    def test_nothing_within_threshold_means_zero_candidates(self):
        result = retrieve(self.bot_id, distance_threshold=0.2)
        self.assertEqual((result.candidate_count, result.top_distance, result.distance_gap), (0, None, None))
        self.assertEqual(result.passages, [])
        self.assertFalse(result.knowledge_empty)

    def test_empty_knowledge_base(self):
        result = retrieve(self.bot_id + 5000)  # collection chưa có gì
        self.assertTrue(result.knowledge_empty)
        self.assertEqual(result.candidate_count, 0)

    def test_signals_top_second_gap(self):
        result = retrieve(self.bot_id, distance_threshold=1.9, neighbors=0)
        self.assertLess(result.top_distance, result.second_distance)
        self.assertAlmostEqual(result.distance_gap, result.second_distance - result.top_distance)
        self.assertGreater(result.distance_gap, 0)

    def test_single_candidate_has_no_gap(self):
        result = retrieve(self.bot_id, distance_threshold=0.6)
        self.assertIsNone(result.second_distance)
        self.assertIsNone(result.distance_gap)


class NeighborExpansion(InMemoryChroma):
    def test_neighbours_are_attached_and_merged_but_marked(self):
        self.add_document(1, [WARRANTY, PRICE, FEATURES, HOTLINE])
        result = retrieve(self.bot_id, distance_threshold=0.6)  # chỉ PRICE (chunk 1) trúng
        self.assertEqual(len(result.passages), 1)
        passage = result.passages[0]
        self.assertEqual(passage["metadata"]["chunk_indexes"], [0, 1, 2])
        self.assertEqual([c["hit"] for c in passage["chunks"]], [False, True, False])
        self.assertEqual(passage["distance"], result.top_distance)

    def test_neighbors_can_be_disabled(self):
        self.add_document(1, [WARRANTY, PRICE, FEATURES])
        result = retrieve(self.bot_id, distance_threshold=0.6, neighbors=0)
        self.assertEqual([c["content"] for p in result.passages for c in p["chunks"]], [PRICE])

    def test_neighbours_never_cross_documents(self):
        self.add_document(1, [PRICE])
        self.add_document(2, [HOTLINE])
        result = retrieve(self.bot_id, distance_threshold=0.6)
        self.assertEqual({p["metadata"]["document_id"] for p in result.passages}, {1})


class DuplicateFilter(InMemoryChroma):
    def test_exact_duplicate_across_documents_kept_once(self):
        self.add_document(1, [PRICE])
        self.add_document(2, [PRICE.upper() + "  "])  # cùng nội dung sau chuẩn hóa hoa/thường + khoảng trắng
        result = retrieve(self.bot_id, neighbors=0)
        self.assertEqual(result.duplicates_dropped, 1)
        self.assertEqual(result.candidate_count, 1)

    def test_semantic_duplicate_across_documents_is_dropped(self):
        base = " ".join(f"w{i}" for i in range(20))
        near = base + " extra"
        self.assertGreater(self.cosine(base, near), rag_engine.DUPLICATE_COSINE)
        self.add_document(1, [base])
        self.add_document(2, [near])
        result = retrieve(self.bot_id, "w0 w1 w2 w3", neighbors=0)
        self.assertEqual((result.duplicates_dropped, result.candidate_count), (1, 1))

    def test_near_identical_chunks_in_the_same_document_are_kept(self):
        base = " ".join(f"w{i}" for i in range(20))
        self.add_document(1, [base, base + " extra"])  # liền kề trong cùng tài liệu (overlap) — không phải bản sao
        result = retrieve(self.bot_id, "w0 w1 w2 w3", neighbors=0)
        self.assertEqual(result.duplicates_dropped, 0)
        self.assertEqual(sum(len(p["chunks"]) for p in result.passages), 2)

    def test_dedupe_helper_keeps_closest_of_the_pair(self):
        hits = [
            {"id": "1-0", "content": "a b", "metadata": {"document_id": 1, "chunk_index": 0}, "distance": 0.1, "embedding": None},
            {"id": "2-0", "content": "A  B", "metadata": {"document_id": 2, "chunk_index": 0}, "distance": 0.2, "embedding": None},
        ]
        self.assertEqual([h["id"] for h in rag_engine.drop_duplicates(hits)], ["1-0"])


class CandidateCountAndSpread(InMemoryChroma):
    def _seven_equal_documents(self):
        for i in range(7):
            self.add_document(i + 1, [f"gia goi u{i}"])  # mỗi nguồn liên quan NGANG nhau với câu hỏi "gia goi"

    def test_many_equally_relevant_sources_flag_narrowing(self):
        self._seven_equal_documents()
        result = retrieve(self.bot_id, "gia goi", neighbors=0)
        self.assertEqual(result.candidate_count, 7)
        self.assertLess(result.distance_gap, rag_engine.DISTANCE_GAP_SMALL)
        self.assertTrue(result.spread_is_ambiguous(5))
        self.assertFalse(result.spread_is_ambiguous(7))

    def test_candidate_count_is_measured_before_rerank_truncation(self):
        self._seven_equal_documents()
        result = retrieve(self.bot_id, "gia goi", rerank_top_n=3, neighbors=0)
        self.assertEqual(result.candidate_count, 7, "nếu cắt trước rồi mới đếm thì nhánh 'nhiều ứng viên' không bao giờ kích hoạt")
        self.assertEqual(len(result.passages), 3)

    def test_spread_signal_does_not_trim_candidates_itself(self):
        self._seven_equal_documents()
        result = retrieve(self.bot_id, "gia goi", rerank_top_n=8, neighbors=0)
        self.assertEqual(len(result.passages), 7)

    def test_adjacent_hits_in_one_document_are_one_candidate(self):
        self.add_document(1, ["gia goi a", "gia goi b", "gia goi c"])
        result = retrieve(self.bot_id, "gia goi", neighbors=0)
        self.assertEqual(result.candidate_count, 1)

    def test_one_clear_winner_is_not_ambiguous(self):
        for i in range(6):
            self.add_document(i + 1, [f"gia goi u{i}"])
        self.add_document(9, ["gia goi"])  # khớp hoàn hảo
        result = retrieve(self.bot_id, "gia goi", neighbors=0)
        self.assertGreater(result.candidate_count, 5)
        self.assertGreater(result.distance_gap, rag_engine.DISTANCE_GAP_SMALL)
        self.assertFalse(result.spread_is_ambiguous(5))


class TokenBudget(unittest.TestCase):
    """fit_passages_to_budget dùng tokenizer thật của model embedding."""

    @staticmethod
    def passage(doc, chunks, distance=0.5):
        return {
            "metadata": {"document_id": doc, "chunk_indexes": list(range(len(chunks)))},
            "chunks": [{"index": i, "content": text, "hit": hit} for i, (text, hit) in enumerate(chunks)],
            "content": "\n\n".join(text for text, _ in chunks),
            "distance": distance,
        }

    def setUp(self):
        self.p1 = self.passage(1, [("chunk lan can truoc " * 8, False), ("chunk trung chinh " * 8, True), ("chunk lan can sau " * 8, False)], 0.4)
        self.p2 = self.passage(2, [("nguon thu hai " * 10, True)], 0.9)

    def test_large_budget_keeps_everything_in_priority_order(self):
        kept, used = rag_engine.fit_passages_to_budget([self.p1, self.p2], 10_000)
        self.assertEqual([p["metadata"]["document_id"] for p in kept], [1, 2])
        self.assertEqual(used, sum(p["tokens"] for p in kept))

    def test_never_exceeds_budget_and_prefers_better_passage(self):
        full = rag_engine.fit_passages_to_budget([self.p1, self.p2], 10_000)[1]
        budget = full - 20
        kept, used = rag_engine.fit_passages_to_budget([self.p1, self.p2], budget)
        self.assertLessEqual(used, budget)
        self.assertEqual(kept[0]["metadata"]["document_id"], 1, "đoạn khoảng cách nhỏ hơn (liên quan hơn) được giữ trước")

    def test_neighbour_chunks_are_dropped_before_the_hit_chunk(self):
        only_p1 = rag_engine.fit_passages_to_budget([self.p1], 10_000)[1]
        kept, _ = rag_engine.fit_passages_to_budget([self.p1], only_p1 // 2)
        chunks = kept[0]["chunks"]
        self.assertTrue(any(c["hit"] for c in chunks))
        self.assertLess(len(chunks), 3)

    def test_first_passage_is_truncated_rather_than_dropped(self):
        big = self.passage(1, [("\n".join(f"dong so {i} co noi dung dai dai dai" for i in range(60)), True)])
        kept, used = rag_engine.fit_passages_to_budget([big], 80)
        self.assertEqual(len(kept), 1)
        self.assertLessEqual(used, 80)
        self.assertGreater(used, 0)

    def test_zero_budget_returns_nothing(self):
        self.assertEqual(rag_engine.fit_passages_to_budget([self.p1], 0), ([], 0))

    def test_results_are_copies(self):
        kept, _ = rag_engine.fit_passages_to_budget([self.p1], 10_000)
        kept[0]["chunks"].clear()
        self.assertEqual(len(self.p1["chunks"]), 3)


class ScopeNarrowingExcerpts(unittest.TestCase):
    """hit_tokens / excerpt_passages: phần mở đầu của TỪNG chunk trúng, tổng <= ngân sách (tokenizer thật)."""

    passage = staticmethod(TokenBudget.passage)

    def chunks(self, sizes, hits=None):
        """1 đoạn/chunk; mỗi chunk bắt đầu bằng tiêu đề '# Muc i' rồi lặp từ để đạt kích thước mong muốn."""
        return [
            self.passage(i, [(f"# Muc {i}\n" + f"noi dung so {i} " * words, True)], 0.3 + i / 20)
            for i, words in enumerate(sizes)
        ]

    @staticmethod
    def total_tokens(passages):
        return rag_engine.count_tokens_many([rag_engine.PASSAGE_SEPARATOR.join(p["content"] for p in passages)])[0]

    def test_hit_tokens_counts_only_hit_chunks_and_separators(self):
        p = self.passage(1, [("lan can " * 200, False), ("chunk trung " * 10, True)])
        q = self.passage(2, [("chunk trung khac " * 10, True)])
        total, n = rag_engine.hit_tokens([p, q])
        own = rag_engine.count_tokens_many(["chunk trung " * 10, "chunk trung khac " * 10, rag_engine.PASSAGE_SEPARATOR])
        self.assertEqual(n, 2)
        self.assertEqual(total, own[0] + own[1] + own[2])

    def test_hit_tokens_empty(self):
        self.assertEqual(rag_engine.hit_tokens([]), (0, 0))
        self.assertEqual(rag_engine.hit_tokens([self.passage(1, [("chi lan can", False)])]), (0, 0))

    def test_every_chunk_is_represented_and_total_fits_budget(self):
        passages = self.chunks([200, 200, 200, 200])
        excerpts = rag_engine.excerpt_passages(passages, 400)
        self.assertEqual(len(excerpts), 4)
        self.assertLessEqual(self.total_tokens(excerpts), 400 + 2 * len(excerpts))  # dung sai: dấu "…" + sai lệch decode/encode
        self.assertTrue(all(e["chunks"][0]["truncated"] for e in excerpts))

    def test_heads_are_the_beginning_of_each_chunk(self):
        excerpts = rag_engine.excerpt_passages(self.chunks([200, 200, 200]), 300)
        for i, e in enumerate(excerpts):
            self.assertTrue(e["content"].startswith(f"# Muc {i}"), e["content"][:40])
            self.assertTrue(e["content"].endswith("…"))

    def test_short_chunks_are_kept_whole_and_leftover_goes_to_long_ones(self):
        passages = self.chunks([3, 300, 300])
        excerpts = rag_engine.excerpt_passages(passages, 300)
        self.assertFalse(excerpts[0]["chunks"][0]["truncated"], "chunk ngắn hơn phần chia đều thì giữ nguyên")
        short_tokens = rag_engine.count_tokens_many([passages[0]["content"]])[0]
        self.assertGreater(excerpts[1]["tokens"], (300 - short_tokens) // 3, "phần dư của chunk ngắn được chia cho chunk dài")

    def test_waterfill(self):
        self.assertEqual(rag_engine._waterfill([10, 100, 100], 120), [10, 55, 55])
        self.assertEqual(rag_engine._waterfill([50, 50], 500), [50, 50])
        self.assertEqual(sum(rag_engine._waterfill([100, 100, 100], 90)), 90)

    def test_priority_order_is_kept(self):
        excerpts = rag_engine.excerpt_passages(self.chunks([100, 100, 100]), 150)
        self.assertEqual([e["metadata"]["document_id"] for e in excerpts], [0, 1, 2])

    def test_neighbour_chunks_are_excluded(self):
        p = self.passage(1, [("lan can truoc " * 50, False), ("chunk trung " * 50, True), ("lan can sau " * 50, False)])
        excerpts = rag_engine.excerpt_passages([p], 100)
        self.assertEqual(len(excerpts), 1)
        self.assertNotIn("lan can", excerpts[0]["content"])

    def test_too_many_chunks_keeps_the_most_relevant_ones_with_meaningful_heads(self):
        passages = self.chunks([100] * 40)
        excerpts = rag_engine.excerpt_passages(passages, 200)
        self.assertLess(len(excerpts), 40)
        self.assertGreaterEqual(len(excerpts), 1)
        self.assertEqual([e["metadata"]["document_id"] for e in excerpts], list(range(len(excerpts))), "giữ các chunk liên quan nhất")
        self.assertTrue(all(e["tokens"] >= rag_engine.MIN_EXCERPT_TOKENS for e in excerpts))

    def test_no_budget_or_no_hits(self):
        self.assertEqual(rag_engine.excerpt_passages(self.chunks([50]), 0), [])
        self.assertEqual(rag_engine.excerpt_passages([], 500), [])

    def test_single_chunk_gets_the_whole_budget(self):
        excerpts = rag_engine.excerpt_passages(self.chunks([400]), 60)
        self.assertEqual(len(excerpts), 1)
        self.assertLessEqual(self.total_tokens(excerpts), 60 + 2)

    def test_input_passages_are_not_mutated(self):
        passages = self.chunks([200, 200])
        before = [p["content"] for p in passages]
        rag_engine.excerpt_passages(passages, 100)
        self.assertEqual([p["content"] for p in passages], before)


if __name__ == "__main__":
    unittest.main()
