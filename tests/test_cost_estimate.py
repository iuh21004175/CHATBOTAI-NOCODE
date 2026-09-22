"""Ước tính chi phí 1 câu hỏi (core/context_engine/cost_estimate.py): công thức khớp các ví dụ trong docs/BANG_GIA_API_AI.md và
cận dưới/cận trên phản ứng đúng với từng cấu hình. Dùng bộ đếm token giả tất định (len//3) nên không cần nạp tokenizer."""
import unittest

from core.context_engine import cost_estimate as ce
from tests.helpers import settings


def fake_count(texts):
    return [max(len(t) // 3, 1) for t in texts]


def estimate(**overrides):
    return ce.estimate_turn(settings(**overrides), chunk_size=450, max_question_tokens=334, count=fake_count)


def hi(result, key="input"):
    return result["question"]["max"][key]


def lo(result, key="input"):
    return result["question"]["min"][key]


class CallCost(unittest.TestCase):
    """Các ví dụ tính tay ở docs/BANG_GIA_API_AI.md mục 28-31 (làm tròn như tài liệu)."""

    def test_doc_example_1_all_cache_miss(self):
        self.assertAlmostEqual(ce.call_cost_vnd(0, 810, 50, ce.OFF_PEAK), 3.97, places=2)
        self.assertAlmostEqual(ce.call_cost_vnd(0, 810, 50, ce.PEAK), 7.94, places=2)

    def test_doc_example_2_cache_hit(self):
        self.assertAlmostEqual(ce.call_cost_vnd(1500, 500, 300, ce.OFF_PEAK), 6.80, places=2)
        self.assertAlmostEqual(ce.call_cost_vnd(1500, 500, 300, ce.PEAK), 13.60, places=2)

    def test_doc_example_3_5000_in_500_out(self):
        self.assertAlmostEqual(ce.call_cost_vnd(0, 5000, 500, ce.OFF_PEAK), 27.51, places=2)
        self.assertAlmostEqual(ce.call_cost_vnd(0, 5000, 500, ce.PEAK), 55.02, places=2)

    def test_doc_example_4_high_cache_hit(self):
        self.assertAlmostEqual(ce.call_cost_vnd(4000, 1000, 500, ce.OFF_PEAK), 12.10, places=2)

    def test_peak_is_twice_off_peak(self):
        self.assertAlmostEqual(ce.call_cost_vnd(100, 200, 300, ce.PEAK), 2 * ce.call_cost_vnd(100, 200, 300, ce.OFF_PEAK))


class EstimateBounds(unittest.TestCase):
    def test_min_not_above_max_for_default_settings(self):
        r = estimate()
        for period in ("off_peak", "peak"):
            self.assertLess(r["question"]["min"]["vnd"][period], r["question"]["max"]["vnd"][period])
        self.assertLessEqual(lo(r), hi(r))
        self.assertLess(lo(r, "output"), hi(r, "output"))

    def test_cache_hit_never_exceeds_input_and_tokens_add_up(self):
        r = estimate()
        for side in ("min", "max"):
            c = r["question"][side]
            self.assertLessEqual(c["cache_hit"], c["input"])
            self.assertEqual(c["cache_hit"] + c["cache_miss"], c["input"])
        self.assertEqual(hi(r, "cache_hit"), 0, "cận trên giả định không trúng cache")
        self.assertEqual(lo(r, "cache_hit") % ce.CACHE_BLOCK_TOKENS, 0, "cache hit theo khối")

    def test_max_output_is_answer_cap_plus_json_overhead(self):
        from core.context_engine.settings import JSON_OVERHEAD_TOKENS
        self.assertEqual(hi(estimate(max_tokens=500), "output"), 500 + JSON_OVERHEAD_TOKENS)
        self.assertEqual(hi(estimate(max_tokens=3000), "output"), 3000 + JSON_OVERHEAD_TOKENS)

    def test_component_maxima_sum_to_max_input_when_within_budget(self):
        r = estimate()
        self.assertFalse(r["over_budget"])
        self.assertEqual(sum(c["max"] for c in r["components"]), hi(r))
        self.assertEqual(sum(c["min"] for c in r["components"]), lo(r))


class ConfigDrivesCost(unittest.TestCase):
    """Mỗi cấu hình liên quan phải làm con số đổi đúng chiều — và chỉ đổi phía nó tác động."""

    def test_max_tokens_changes_only_upper_bound_output_side(self):
        a, b = estimate(max_tokens=200), estimate(max_tokens=1500)
        self.assertLess(a["question"]["max"]["vnd"]["off_peak"], b["question"]["max"]["vnd"]["off_peak"])
        self.assertEqual(a["question"]["min"], b["question"]["min"])

    def test_longer_instructions_raise_both_bounds(self):
        big = dict(max_context_tokens=100000, rag_max_context_tokens=1000)  # ngân sách ngữ cảnh không phải ràng buộc
        short, long = estimate(instructions="Ngắn.", **big), estimate(instructions="Chỉ dẫn rất dài. " * 300, **big)
        self.assertGreater(lo(long), lo(short))
        self.assertGreater(hi(long), hi(short))

    def test_when_context_budget_binds_longer_instructions_squeeze_rag_not_total(self):
        """Ngân sách ngữ cảnh đã đầy: chỉ dẫn dài hơn chiếm chỗ của tài liệu (ContextBudgetManager) nên cận trên không tăng thêm."""
        rag = lambda r: next(c for c in r["components"] if c["key"] == "rag")["max"]
        short, long = estimate(instructions="Ngắn."), estimate(instructions="Chỉ dẫn rất dài. " * 300)
        self.assertLess(rag(long), rag(short))
        self.assertEqual(hi(long), hi(short))

    def test_rag_disabled_removes_rag_from_max(self):
        on, off = estimate(rag_enabled=True), estimate(rag_enabled=False)
        rag = lambda r: next(c for c in r["components"] if c["key"] == "rag")
        self.assertGreater(rag(on)["max"], 0)
        self.assertEqual(rag(off)["max"], 0)
        self.assertLess(hi(off), hi(on))
        self.assertEqual(rag(on)["min"], 0, "không tra cứu được gì thì không có tài liệu trong prompt")

    def test_rag_budget_is_capped_by_configured_budget_and_chunk_size(self):
        r = ce.estimate_turn(settings(rag_max_context_tokens=500), chunk_size=450, max_question_tokens=334, count=fake_count)
        self.assertEqual(next(c for c in r["components"] if c["key"] == "rag")["max"], 500)
        small = ce.estimate_turn(settings(rag_rerank_top_n=1, rag_max_context_tokens=8000, max_context_tokens=100000), chunk_size=100,
                                 max_question_tokens=334, count=fake_count)
        self.assertEqual(next(c for c in small["components"] if c["key"] == "rag")["max"], 1 * 3 * 100)

    def test_recent_messages_capped_by_token_limit_and_message_limit(self):
        component = lambda r: next(c for c in r["components"] if c["key"] == "recent")["max"]
        self.assertEqual(component(estimate(recent_token_limit=300)), 300)
        self.assertEqual(component(estimate(recent_message_limit=2, recent_token_limit=8000, max_tokens=100)), 334 + 100)
        self.assertGreater(component(estimate(recent_message_limit=20, recent_token_limit=8000)),
                           component(estimate(recent_message_limit=4, recent_token_limit=8000)))

    def test_memory_and_summary_toggles(self):
        get = lambda r, k: next(c for c in r["components"] if c["key"] == k)["max"]
        off = estimate(structured_memory_enabled=False, summary_enabled=False)
        on = estimate(structured_memory_enabled=True, summary_enabled=True)
        self.assertEqual((get(off, "memory"), get(off, "summary")), (0, 0))
        self.assertGreater(get(on, "memory"), 0)
        self.assertGreater(get(on, "summary"), 0)
        self.assertGreater(hi(on), hi(off))
        self.assertIsNone(off["summary_job"])

    def test_memory_and_summary_sizes_follow_their_limits(self):
        get = lambda r, k: next(c for c in r["components"] if c["key"] == k)["max"]
        self.assertGreater(get(estimate(memory_max_items=60), "memory"), get(estimate(memory_max_items=10), "memory"))
        self.assertGreater(get(estimate(summary_max_tokens=1000), "summary"), get(estimate(summary_max_tokens=200), "summary"))

    def test_summary_job_follows_trigger_and_max_length(self):
        base = estimate(summary_trigger_tokens=2000, summary_max_tokens=300)["summary_job"]
        more = estimate(summary_trigger_tokens=8000, summary_max_tokens=1200)["summary_job"]
        self.assertEqual(base["trigger_tokens"], 2000)
        self.assertGreater(more["min"]["vnd"]["off_peak"], base["min"]["vnd"]["off_peak"])
        self.assertGreater(more["max"]["vnd"]["off_peak"], base["max"]["vnd"]["off_peak"])
        self.assertLess(base["min"]["vnd"]["off_peak"], base["max"]["vnd"]["off_peak"])

    def test_language_changes_prompt_size(self):
        self.assertNotEqual(lo(estimate(language="vi")), lo(estimate(language="en")))

    def test_over_budget_is_flagged_and_capped(self):
        r = estimate(instructions="x" * 30000, max_context_tokens=2000)
        self.assertTrue(r["over_budget"])
        self.assertEqual(hi(r), 2000, "cận trên bị chặn ở tổng ngân sách ngữ cảnh")

    def test_intents_add_to_static_prompt(self):
        from core.context_engine.state import IntentSpec
        base = estimate()
        with_intents = ce.estimate_turn(settings(), chunk_size=450, max_question_tokens=334, count=fake_count,
                                        intents=[IntentSpec("ask_price", "Hỏi giá", ("product",), ("budget",))] * 5)
        self.assertGreater(lo(with_intents), lo(base))


if __name__ == "__main__":
    unittest.main()
