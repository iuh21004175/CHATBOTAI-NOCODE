"""Phase 1 — tính toán trạng thái hội thoại: slot, intent, lượt hỏi làm rõ, loại bỏ bộ nhớ."""
import unittest
from datetime import datetime, timedelta

from core.context_engine.state import (
    ConversationSnapshot, IntentSpec, compute_slot_completion, finalize_turns, is_filled, merge_slots,
    resolve_state, select_memory_to_keep,
)
from tests.helpers import output, settings

PRICE = IntentSpec("ask_price", "Hỏi giá", required=("product", "budget"))


class Slots(unittest.TestCase):
    def test_is_filled(self):
        for empty in (None, "", "   ", [], {}, ()):
            self.assertFalse(is_filled(empty), repr(empty))
        for filled in ("x", 0, False, ["a"], {"k": 1}):
            self.assertTrue(is_filled(filled), repr(filled))

    def test_new_value_overwrites_old(self):
        self.assertEqual(merge_slots({"product": "Basic"}, {"product": "Pro"})["product"], "Pro")

    def test_slot_not_mentioned_this_turn_is_kept(self):
        self.assertEqual(merge_slots({"product": "Pro", "budget": "5tr"}, {"product": "Pro"})["budget"], "5tr")

    def test_null_or_empty_new_value_does_not_erase_old(self):
        merged = merge_slots({"budget": "5tr"}, {"budget": None, "color": ""})
        self.assertEqual(merged["budget"], "5tr")
        self.assertIsNone(merged["color"])

    def test_merge_does_not_mutate_inputs(self):
        old = {"a": 1}
        merge_slots(old, {"b": 2})
        self.assertEqual(old, {"a": 1})

    def test_completion(self):
        self.assertEqual(compute_slot_completion({}, ()), 1.0, "không khai báo required -> luôn đạt")
        self.assertEqual(compute_slot_completion({"product": "Pro"}, ("product", "budget")), 0.5)
        self.assertEqual(compute_slot_completion({"product": "Pro", "budget": "5tr"}, ("product", "budget")), 1.0)
        self.assertEqual(compute_slot_completion({"product": "", "budget": None}, ("product", "budget")), 0.0)
        self.assertEqual(compute_slot_completion({"budget": 0}, ("budget",)), 1.0, "0 là giá trị hợp lệ")


class IntentTransitions(unittest.TestCase):
    def test_first_turn_sets_intent_without_change_flag(self):
        u = resolve_state(ConversationSnapshot(), output(intent="ask_price", intent_confidence=0.9), [PRICE], settings())
        self.assertEqual((u.current_intent, u.previous_intent, u.intent_changed), ("ask_price", None, False))

    def test_change_sets_previous_and_flag(self):
        snap = ConversationSnapshot(current_intent="ask_price", slots={"product": "Pro"})
        u = resolve_state(snap, output(intent="ask_warranty", intent_confidence=0.9), [PRICE], settings())
        self.assertEqual((u.current_intent, u.previous_intent, u.intent_changed), ("ask_warranty", "ask_price", True))
        self.assertEqual(u.slots["product"], "Pro", "slot cũ giữ nguyên khi đổi intent")

    def test_same_intent_is_not_a_change(self):
        snap = ConversationSnapshot(current_intent="ask_price", previous_intent="greeting")
        u = resolve_state(snap, output(intent="ask_price", intent_confidence=0.9), [PRICE], settings())
        self.assertFalse(u.intent_changed)
        self.assertEqual(u.previous_intent, "greeting")

    def test_low_confidence_intent_does_not_overwrite_a_known_one(self):
        snap = ConversationSnapshot(current_intent="ask_price", intent_confidence=0.9)
        u = resolve_state(snap, output(intent="other", intent_confidence=0.3), [PRICE], settings(intent_confidence_threshold=0.7))
        self.assertEqual(u.current_intent, "ask_price")
        self.assertFalse(u.intent_changed)
        self.assertEqual(u.intent_confidence, 0.9)
        self.assertEqual(u.required_slots, ("product", "budget"), "vẫn theo đuổi required_slots của intent đang có")

    def test_low_confidence_first_intent_is_still_recorded(self):
        u = resolve_state(ConversationSnapshot(), output(intent="other", intent_confidence=0.3), [], settings())
        self.assertEqual(u.current_intent, "other")

    def test_intent_without_config_has_no_required_slots(self):
        u = resolve_state(ConversationSnapshot(), output(intent="chitchat", slots={"x": 1}), [PRICE], settings())
        self.assertEqual((u.required_slots, u.slot_completion), ((), 1.0))

    def test_completion_uses_merged_slots(self):
        snap = ConversationSnapshot(current_intent="ask_price", slots={"product": "Pro"})
        u = resolve_state(snap, output(intent="ask_price", slots={"budget": "5tr"}), [PRICE], settings())
        self.assertEqual(u.slot_completion, 1.0)


class ClarificationTurns(unittest.TestCase):
    def test_increments_on_clarify_and_resets_otherwise(self):
        snap = ConversationSnapshot(clarification_turns_used=1)
        base = resolve_state(snap, output(), [], settings())
        self.assertEqual(finalize_turns(base, snap, True).clarification_turns_used, 2)
        self.assertEqual(finalize_turns(base, snap, False).clarification_turns_used, 0)


class MemoryEviction(unittest.TestCase):
    def _rows(self):
        now = datetime(2026, 9, 21, 12)
        return [
            {"key": "a", "confidence": 0.9, "updated_at": now - timedelta(days=3)},
            {"key": "b", "confidence": 0.7, "updated_at": now - timedelta(days=2)},
            {"key": "c", "confidence": 0.7, "updated_at": now - timedelta(days=1)},
            {"key": "d", "confidence": 0.95, "updated_at": now},
        ]

    def test_under_limit_keeps_all(self):
        self.assertEqual(len(select_memory_to_keep(self._rows(), 4)), 4)

    def test_lowest_confidence_is_evicted_first_then_oldest(self):
        kept = {r["key"] for r in select_memory_to_keep(self._rows(), 3)}
        self.assertEqual(kept, {"a", "c", "d"}, "b (confidence thấp nhất, cũ hơn c) bị loại")

    def test_evicts_several(self):
        kept = {r["key"] for r in select_memory_to_keep(self._rows(), 2)}
        self.assertEqual(kept, {"a", "d"})


if __name__ == "__main__":
    unittest.main()
