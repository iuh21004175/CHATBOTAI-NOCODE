"""Cây quyết định Bước C — mỗi nhánh 1 test (candidate_count=0, intent thấp, slot thiếu, nhiều ứng viên + gap nhỏ,
áp lực ngữ cảnh cao) cộng thứ tự ưu tiên, giới hạn lượt hỏi làm rõ, và các trường hợp biên."""
import unittest

from core import rag_engine
from core.context_engine.decision import (
    Decision, can_clarify, decide, is_ambiguous_reference, resolve_reply_text,
)
from tests.helpers import output, settings, signals


class NoRelevantContext(unittest.TestCase):
    """Nhánh 1: candidate_count == 0."""

    def test_ask_clarify_mode_uses_owner_clarify_message_not_llm_answer(self):
        s = settings(low_confidence_clarify_message="Bạn hỏi về sản phẩm nào ạ?")
        result = decide(signals(candidate_count=0), s, output(proposed_answer="Tự bịa 999đ"))
        self.assertEqual(result.decision, Decision.CLARIFY)
        text = resolve_reply_text(result, output(proposed_answer="Tự bịa 999đ"), s)
        self.assertEqual(text, "Bạn hỏi về sản phẩm nào ạ?")
        self.assertNotIn("999", text)

    def test_decline_mode_uses_owner_decline_message(self):
        s = settings(low_confidence_reply_mode="decline", low_confidence_decline_message="Chưa có thông tin, xin liên hệ 1900.")
        result = decide(signals(candidate_count=0), s, output())
        self.assertEqual(result.decision, Decision.DECLINE)
        self.assertEqual(resolve_reply_text(result, output(), s), "Chưa có thông tin, xin liên hệ 1900.")

    def test_default_messages_when_owner_left_blank(self):
        vi = settings(low_confidence_reply_mode="decline")
        en = settings(low_confidence_reply_mode="decline", language="en")
        r = decide(signals(candidate_count=0), vi, output())
        self.assertIn("Xin lỗi", resolve_reply_text(r, output(), vi))
        self.assertIn("Sorry", resolve_reply_text(decide(signals(candidate_count=0), en, output()), output(), en))

    def test_clarify_cap_reached_declines_instead_of_llm_answer(self):
        # Hết lượt hỏi làm rõ: KHÔNG được chuyển sang ANSWER bằng proposed_answer khi không có ngữ cảnh đạt ngưỡng
        s = settings(max_clarification_turns=2)
        result = decide(signals(candidate_count=0, clarification_turns_used=2), s, output())
        self.assertEqual(result.decision, Decision.DECLINE)
        self.assertIn("clarification_limit_reached", result.reasons)

    def test_clarification_disabled_declines(self):
        result = decide(signals(candidate_count=0), settings(clarification_enabled=False), output())
        self.assertEqual(result.decision, Decision.DECLINE)
        self.assertIn("clarification_disabled", result.reasons)

    def test_takes_priority_over_every_other_rule(self):
        result = decide(signals(candidate_count=0, intent_confidence=0.1, slot_completion=0.0, has_required_slots=True), settings(), output())
        self.assertIn("no_relevant_context", result.reasons)
        self.assertNotIn("low_intent_confidence", result.reasons)

    def test_not_applied_without_knowledge_base(self):
        # KB tắt/trống: không có ngữ cảnh RAG nào "dưới ngưỡng" -> rule 1 không áp dụng
        result = decide(signals(rag_used=False, candidate_count=0), settings(), output())
        self.assertEqual(result.decision, Decision.ANSWER)


class LowIntentConfidence(unittest.TestCase):
    def test_below_threshold_clarifies_with_llm_question(self):
        result = decide(signals(intent_confidence=0.5), settings(intent_confidence_threshold=0.7), output())
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.reasons, ["low_intent_confidence"])
        self.assertEqual(resolve_reply_text(result, output(), settings()), "Bạn cần gói nào?")

    def test_exactly_at_threshold_answers(self):
        self.assertEqual(decide(signals(intent_confidence=0.7), settings(), output()).decision, Decision.ANSWER)

    def test_tracking_disabled_answers(self):
        self.assertEqual(decide(signals(intent_confidence=0.1), settings(intent_tracking_enabled=False), output()).decision, Decision.ANSWER)

    def test_missing_confidence_does_not_clarify(self):
        self.assertEqual(decide(signals(intent_confidence=None), settings(), output()).decision, Decision.ANSWER)

    def test_empty_llm_question_falls_back_to_owner_message(self):
        s = settings(low_confidence_clarify_message="Bạn nói rõ hơn được không?")
        result = decide(signals(intent_confidence=0.2), s, output(proposed_clarification_question=""))
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(resolve_reply_text(result, output(proposed_clarification_question=""), s), "Bạn nói rõ hơn được không?")


class MissingSlots(unittest.TestCase):
    def test_below_completion_threshold_clarifies(self):
        result = decide(signals(has_required_slots=True, slot_completion=0.5), settings(slot_completion_threshold=0.8), output())
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.reasons, ["missing_required_slots"])

    def test_not_applied_when_intent_has_no_required_slots(self):
        self.assertEqual(decide(signals(has_required_slots=False, slot_completion=0.0), settings(), output()).decision, Decision.ANSWER)

    def test_complete_answers(self):
        self.assertEqual(decide(signals(has_required_slots=True, slot_completion=1.0), settings(), output()).decision, Decision.ANSWER)

    def test_slot_filling_disabled_answers(self):
        r = decide(signals(has_required_slots=True, slot_completion=0.0), settings(slot_filling_enabled=False), output())
        self.assertEqual(r.decision, Decision.ANSWER)


class TooManyCandidates(unittest.TestCase):
    def test_many_equally_relevant_candidates_clarify(self):
        result = decide(signals(candidate_count=7, distance_gap=0.01, spread_ambiguous=True), settings(), output())
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.reasons, ["too_many_relevant_candidates"])

    def test_spread_signal_definition(self):
        def r(count, gap):
            return rag_engine.RetrievalResult(candidate_count=count, distance_gap=gap)

        self.assertTrue(r(7, 0.01).spread_is_ambiguous(5))
        self.assertFalse(r(7, 0.30).spread_is_ambiguous(5), "nhiều ứng viên nhưng có 1 ứng viên nổi bật rõ rệt")
        self.assertFalse(r(5, 0.01).spread_is_ambiguous(5), "đúng bằng max_candidate_count chưa vượt")
        self.assertFalse(r(1, None).spread_is_ambiguous(5), "1 ứng viên không có gap")
        self.assertFalse(r(0, None).spread_is_ambiguous(5))


class ScopeNarrowingBranch(unittest.TestCase):
    def test_context_over_budget_clarifies_with_llm_question(self):
        out = output(proposed_answer="", proposed_clarification_question="Bạn muốn hỏi về mục nào ạ?")
        result = decide(signals(scope_narrowing=True), settings(), out)
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.reasons, ["context_exceeds_budget"])
        self.assertEqual(resolve_reply_text(result, out, settings()), "Bạn muốn hỏi về mục nào ạ?")

    def test_wins_over_low_intent_and_missing_slots(self):
        result = decide(signals(scope_narrowing=True, intent_confidence=0.1, has_required_slots=True, slot_completion=0.0), settings(), output())
        self.assertEqual(result.reasons, ["context_exceeds_budget"])

    def test_does_not_override_no_relevant_context(self):
        self.assertIn("no_relevant_context", decide(signals(scope_narrowing=True, candidate_count=0), settings(), output()).reasons)

    def test_without_the_flag_nothing_changes(self):
        self.assertEqual(decide(signals(scope_narrowing=False), settings(), output()).decision, Decision.ANSWER)

    def test_when_turns_are_exhausted_it_answers_instead_of_looping(self):
        # Engine không bật chế độ này khi hết lượt; nếu vẫn lọt tới đây thì cũng không được hỏi tiếp vô hạn
        result = decide(signals(scope_narrowing=True, clarification_turns_used=2), settings(max_clarification_turns=2), output())
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertTrue(result.forced_answer)


class ContextPressure(unittest.TestCase):
    def test_high_pressure_never_clarifies_by_itself(self):
        # Đặc tả mục 15: ngữ cảnh lớn KHÔNG có nghĩa là hỏi lại khách — nén trước rồi trả lời
        for pressure in (0.65, 0.85, 0.95, 1.4):
            result = decide(signals(context_pressure=pressure, context_compressed=True), settings(), output())
            self.assertEqual(result.decision, Decision.ANSWER, pressure)
            self.assertIn("context_compressed", result.reasons)

    def test_normal_pressure_has_no_compression_reason(self):
        self.assertNotIn("context_compressed", decide(signals(), settings(), output()).reasons)


class ClarificationLimit(unittest.TestCase):
    def test_can_clarify(self):
        s = settings(max_clarification_turns=2)
        self.assertTrue(can_clarify(s, 0))
        self.assertTrue(can_clarify(s, 1))
        self.assertFalse(can_clarify(s, 2))
        self.assertFalse(can_clarify(settings(clarification_enabled=False), 0))
        self.assertFalse(can_clarify(settings(max_clarification_turns=0), 0))

    def test_cap_reached_forces_answer_with_polite_note_when_unsure(self):
        s = settings(max_clarification_turns=2)
        out = output(self_assessed_confidence=0.3)
        result = decide(signals(intent_confidence=0.2, clarification_turns_used=2), s, out)
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertTrue(result.forced_answer)
        self.assertIn("clarification_limit_reached", result.reasons)
        text = resolve_reply_text(result, out, s)
        self.assertTrue(text.startswith(out.proposed_answer))
        self.assertIn("Lưu ý", text)

    def test_cap_reached_no_note_when_confident(self):
        s = settings()
        out = output(self_assessed_confidence=0.9)
        result = decide(signals(intent_confidence=0.2, clarification_turns_used=2), s, out)
        self.assertEqual(resolve_reply_text(result, out, s), out.proposed_answer)

    def test_forced_answer_with_empty_answer_declines(self):
        out = output(proposed_answer="")
        result = decide(signals(intent_confidence=0.2, clarification_turns_used=2), settings(), out)
        self.assertEqual(result.decision, Decision.DECLINE)

    def test_clarification_disabled_forces_answer(self):
        result = decide(signals(intent_confidence=0.2), settings(clarification_enabled=False), output())
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIn("clarification_disabled", result.reasons)

    def test_llm_only_gave_question_clarifies_if_allowed(self):
        out = output(proposed_answer="", proposed_clarification_question="Bạn muốn xem gói nào?")
        self.assertEqual(decide(signals(), settings(), out).decision, Decision.CLARIFY)
        self.assertEqual(decide(signals(clarification_turns_used=2), settings(), out).decision, Decision.DECLINE)


class AmbiguityDetector(unittest.TestCase):
    def test_flags_vague_references(self):
        for q in ("Cái này giá bao nhiêu?", "cai nay bao nhieu", "Loại nào tốt hơn?", "mẫu trên còn hàng không", "Còn cái đó thì sao", "thế còn gói kia"):
            self.assertTrue(is_ambiguous_reference(q), q)

    def test_does_not_flag_specific_questions(self):
        for q in ("Giá gói Pro bao nhiêu?", "Chính sách bảo hành thế nào", "xin chào", ""):
            self.assertFalse(is_ambiguous_reference(q), q)

    def test_flag_is_only_recorded_never_changes_decision(self):
        result = decide(signals(is_ambiguous_reference=True), settings(), output())
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIn("ambiguous_reference", result.reasons)


if __name__ == "__main__":
    unittest.main()
