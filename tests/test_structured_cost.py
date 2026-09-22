"""JSON có cấu trúc (parse chặt + gọi lại đúng 1 lần) và Cost Control (đọc usage phòng thủ, đếm lệnh gọi chính/phụ)."""
import json
import unittest

from core.context_engine.cost import LLMUsageTracker, Usage, parse_usage
from core.context_engine.structured import (
    LLMReply, StructuredOutputError, call_structured, parse_structured_output,
)
from tests.helpers import FakeLLM, llm_json, usage


class ParseStructuredOutput(unittest.TestCase):
    def test_valid_output(self):
        o = parse_structured_output(llm_json(
            slots={"product": "Pro", "budget": None},
            memory_updates=[{"category": "preference", "key": "kênh", "value": "Zalo", "confidence": 0.9}],
        ))
        self.assertEqual((o.intent, o.intent_confidence), ("ask_price", 0.95))
        self.assertEqual(o.slots, {"product": "Pro", "budget": None})
        self.assertEqual(o.memory_updates[0]["value"], "Zalo")
        self.assertEqual(o.proposed_answer, "Gói Pro giá 500.000đ.")

    def test_markdown_fence_is_tolerated(self):
        self.assertEqual(parse_structured_output("```json\n" + llm_json() + "\n```").intent, "ask_price")

    def test_missing_optional_fields_use_safe_defaults(self):
        o = parse_structured_output(json.dumps({"proposed_answer": "Xin chào"}))
        self.assertEqual((o.intent, o.intent_confidence, o.slots, o.memory_updates, o.needs_history_lookup), (None, None, {}, [], False))

    def test_invalid_payloads_are_rejected(self):
        bad = {
            "rỗng": "",
            "không phải JSON": "Xin chào, giá là 500k",
            "JSON mảng": "[1,2]",
            "confidence ngoài [0,1]": llm_json(intent_confidence=95),
            "confidence sai kiểu": llm_json(intent_confidence="cao"),
            "confidence là bool": llm_json(intent_confidence=True),
            "slots sai kiểu": llm_json(slots=["a"]),
            "memory sai kiểu": llm_json(memory_updates="x"),
            "needs_history_lookup sai kiểu": llm_json(needs_history_lookup="yes"),
            "answer sai kiểu": llm_json(proposed_answer=123),
            "cả hai rỗng": llm_json(proposed_answer="", proposed_clarification_question=""),
            "intent sai kiểu": llm_json(intent=5),
        }
        for label, payload in bad.items():
            with self.subTest(label), self.assertRaises(StructuredOutputError):
                parse_structured_output(payload)

    def test_bad_memory_items_are_dropped_and_counted_without_failing_the_reply(self):
        good = {"category": "entity", "key": "tên", "value": "An", "confidence": 0.8}
        items = [
            good,
            {"category": "bogus", "key": "k", "value": "v", "confidence": 0.9},
            {"category": "entity", "key": "", "value": "v", "confidence": 0.9},
            {"category": "entity", "key": "k", "value": "", "confidence": 0.9},
            {"category": "entity", "key": "k", "value": "v", "confidence": 1.5},
            "không phải dict",
        ]
        o = parse_structured_output(llm_json(memory_updates=items))
        self.assertEqual(o.memory_updates, [good])
        self.assertEqual(o.dropped_memory_items, 5)

    def test_numeric_memory_value_is_kept_as_text(self):
        o = parse_structured_output(llm_json(memory_updates=[{"category": "requirement", "key": "số lượng", "value": 3, "confidence": 0.9}]))
        self.assertEqual(o.memory_updates[0]["value"], "3")


class CallStructured(unittest.TestCase):
    def test_valid_first_reply_is_exactly_one_call(self):
        llm, tracker = FakeLLM(llm_json()), LLMUsageTracker()
        call_structured([], llm, tracker)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(tracker.main_call_count, 1)

    def test_bad_json_retries_exactly_once_and_records_the_retry(self):
        llm, tracker = FakeLLM("không phải json", llm_json()), LLMUsageTracker()
        self.assertEqual(call_structured([], llm, tracker).intent, "ask_price")
        self.assertEqual([c["kind"] for c in tracker.calls], ["main", "retry"])
        self.assertEqual(tracker.main_usage.prompt_tokens, 2000, "chi phí của lần gọi lại vẫn tính vào lệnh gọi chính")

    def test_empty_content_is_retried(self):
        llm = FakeLLM("", llm_json())  # DeepSeek JSON mode đôi khi trả content rỗng (tài liệu hiện hành)
        self.assertEqual(call_structured([], llm, LLMUsageTracker()).proposed_answer, "Gói Pro giá 500.000đ.")

    def test_two_bad_replies_raise_instead_of_faking_an_answer(self):
        llm = FakeLLM("x", "y", llm_json())
        with self.assertRaises(StructuredOutputError):
            call_structured([], llm, LLMUsageTracker())
        self.assertEqual(len(llm.calls), 2, "không gọi lần thứ 3")

    def test_network_errors_propagate_unswallowed(self):
        def broken(_messages):
            raise ConnectionError("mạng đứt")

        with self.assertRaises(ConnectionError):
            call_structured([], broken, LLMUsageTracker())

    def test_secondary_call_retry_stays_secondary(self):
        tracker = LLMUsageTracker()
        call_structured([], FakeLLM("x", llm_json()), tracker, kind="history_lookup")
        self.assertEqual(tracker.main_call_count, 0)
        self.assertEqual([c["kind"] for c in tracker.extra_calls], ["history_lookup", "history_lookup_retry"])


class ParseUsage(unittest.TestCase):
    def test_real_deepseek_shape(self):
        # Mẫu thật đo ngày 2026-09-21 (lượt 2, cùng prefix)
        u = parse_usage({"completion_tokens": 20, "prompt_tokens": 2478, "total_tokens": 2498,
                         "prompt_tokens_details": {"cached_tokens": 2304}, "prompt_cache_hit_tokens": 2304, "prompt_cache_miss_tokens": 174})
        self.assertEqual((u.prompt_tokens, u.completion_tokens, u.cache_hit_tokens, u.cache_miss_tokens), (2478, 20, 2304, 174))
        self.assertAlmostEqual(u.cache_hit_ratio, 2304 / 2478)

    def test_missing_usage_is_unknown_not_zero(self):
        for raw in (None, {}, "lỗi", 5):
            u = parse_usage(raw)
            self.assertFalse(u.reported)
            self.assertIsNone(u.prompt_tokens)
            self.assertIsNone(u.cache_hit_ratio)

    def test_garbage_fields_do_not_raise(self):
        u = parse_usage({"prompt_tokens": "1000", "completion_tokens": -5, "total_tokens": None, "prompt_cache_hit_tokens": float("nan"),
                         "prompt_cache_miss_tokens": True})
        self.assertIsNone(u.prompt_tokens)
        self.assertIsNone(u.completion_tokens)
        self.assertIsNone(u.cache_hit_tokens)

    def test_falls_back_to_standard_cached_tokens(self):
        u = parse_usage({"prompt_tokens": 1000, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 640}})
        self.assertEqual((u.cache_hit_tokens, u.cache_miss_tokens), (640, 360))

    def test_total_computed_when_absent(self):
        self.assertEqual(parse_usage({"prompt_tokens": 10, "completion_tokens": 5}).total_tokens, 15)

    def test_addition_treats_unknown_as_absent(self):
        self.assertEqual((Usage(10, 1) + Usage()).prompt_tokens, 10)
        self.assertIsNone((Usage() + Usage()).prompt_tokens)


class Tracker(unittest.TestCase):
    def test_extra_calls_are_counted_apart_from_main(self):
        t = LLMUsageTracker()
        t.record("main", parse_usage(usage(1000, 50)))
        t.record("history_lookup", parse_usage(usage(400, 30)))
        t.record("summary", parse_usage(usage(300, 100)))
        self.assertEqual(t.main_call_count, 1)
        self.assertEqual(t.main_usage.prompt_tokens, 1000, "usage lệnh gọi chính không lẫn lệnh gọi phụ")
        self.assertEqual([c["kind"] for c in t.extra_calls], ["history_lookup", "summary"])


if __name__ == "__main__":
    unittest.main()
