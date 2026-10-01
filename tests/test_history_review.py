"""Lịch sử chat dùng để THEO DÕI và tinh chỉnh chatbot (chỉ đọc): lọc theo quyết định của bot, nhãn diễn giải
decision_trace, thống kê tỉ lệ; và tự ghi Customer khi AI điền các slot contact_*. Phần thuần không cần DB; còn lại cần
DB *_test (xem tests/README.md)."""
import json
import unittest
from datetime import datetime, timedelta

from app.customers import service as customers
from app.dashboard.service import DECISION_LABELS, explain_decision
from app.models import Conversation, Customer, Message
from core.context_engine.structured import LLMReply
from tests.db_case import DbCase
from tests.helpers import llm_json, usage


def trace(decision="answer", reasons=(), **extra):
    return {"decision": decision, "reasons": list(reasons), "candidate_count": 3, "intent_confidence": 0.9, "slot_completion": 1.0, **extra}


class ExplainDecision(unittest.TestCase):
    def test_answer_mentions_the_number_of_sources(self):
        result = explain_decision(trace("answer", candidate_count=3))
        self.assertEqual(result["decision"], "answer")
        self.assertIn("3 nguồn", result["text"])

    def test_decline_without_relevant_context(self):
        result = explain_decision(trace("decline", ["no_relevant_context"], candidate_count=0))
        self.assertEqual(result["decision"], "decline")
        self.assertIn("Từ chối", result["text"])
        self.assertIn("không tìm thấy tài liệu liên quan", result["text"])
        self.assertIn("candidate_count=0", result["text"])

    def test_every_clarify_reason_has_its_own_explanation_with_real_numbers(self):
        cases = {
            "low_intent_confidence": ("độ tin cậy nhận diện ý định thấp", "intent_confidence=0.4"),
            "missing_required_slots": ("thiếu thông tin bắt buộc", "slot_completion=0.5"),
            "context_exceeds_budget": ("thu hẹp phạm vi", None),
            "too_many_relevant_candidates": ("nhiều nội dung liên quan ngang nhau", "candidate_count=7"),
            "empty_proposed_answer": ("chưa đủ thông tin", None),
        }
        for reason, (phrase, figure) in cases.items():
            with self.subTest(reason=reason):
                result = explain_decision(trace("clarify", [reason], intent_confidence=0.4, slot_completion=0.5, candidate_count=7))
                self.assertTrue(result["text"].startswith("Hỏi lại"))
                self.assertIn(phrase, result["text"])
                if figure:
                    self.assertIn(figure, result["text"])

    def test_notes_are_added_and_unknown_reason_is_shown_verbatim_not_invented(self):
        result = explain_decision(trace("clarify", ["low_intent_confidence", "context_compressed", "ly_do_moi_cua_engine"]))
        self.assertIn("ngữ cảnh đã bị nén", result["text"])
        self.assertIn("ly_do_moi_cua_engine", result["text"])

    def test_figures_only_appear_for_the_reason_that_uses_them(self):
        text = explain_decision(trace("clarify", ["low_intent_confidence"], slot_completion=0.5))["text"]
        self.assertNotIn("slot_completion", text)

    def test_missing_or_malformed_trace_gives_no_label(self):
        for value in (None, {}, [], "answer", {"decision": "boom"}, {"decision": None}, 5):
            self.assertIsNone(explain_decision(value), value)

    def test_malformed_reasons_do_not_crash(self):
        self.assertIsNotNone(explain_decision({"decision": "decline", "reasons": None}))
        self.assertIsNotNone(explain_decision({"decision": "decline", "reasons": [1, None, "no_relevant_context"]}))

    def test_labels_cover_exactly_the_engine_decisions(self):
        from core.context_engine.decision import Decision

        self.assertEqual(set(DECISION_LABELS), {d.value for d in Decision})


class CleanName(unittest.TestCase):
    def test_accepts_and_normalises_names(self):
        self.assertEqual(customers.clean_name("  Nguyễn   Văn  Nam "), "Nguyễn Văn Nam")

    def test_rejects_non_names(self):
        for bad in (None, "", "   ", 5, ["Nam"], {"a": 1}, True, "nam@example.com", "0912345678", "+84 912 345 678", "x" * 256):
            self.assertIsNone(customers.clean_name(bad), bad)


class ReviewCase(DbCase):
    def setUp(self):
        super().setUp()
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["_user_id"] = str(self.team.user.id)
            session["team_id"] = self.team.id
            session["csrf_token"] = "tok"

    def conversation_with(self, *decisions, bot=None, created_at=None, visitor="v"):
        conversation = self.conversation(bot)
        conversation.visitor_id = visitor
        self.db.session.commit()
        self.add_message(conversation, "customer", "Câu hỏi của khách")
        for decision in decisions:
            message = self.add_message(conversation, "bot", f"Trả lời {decision}")
            message.decision_trace = trace(decision, ["no_relevant_context"] if decision == "decline" else [], candidate_count=0 if decision == "decline" else 3)
            if created_at:
                message.created_at = created_at
            self.db.session.commit()
        return conversation

    def history(self, bot=None, client=None, **query):
        return (client or self.client).get(f"/bots/{(bot or self.bot).id}/history", query_string=query)


class FilterByDecision(ReviewCase):
    def test_filter_keeps_only_conversations_with_that_decision(self):
        answered = self.conversation_with("answer", "answer")
        declined = self.conversation_with("answer", "decline")
        clarified = self.conversation_with("clarify")
        ids = lambda decision: {c.id for c in self.service.list_conversations(self.bot.id, decision=decision)}
        self.assertEqual(ids("decline"), {declined.id})
        self.assertEqual(ids("clarify"), {clarified.id})
        self.assertEqual(ids("answer"), {answered.id, declined.id})
        self.assertEqual(ids(""), {answered.id, declined.id, clarified.id})

    def test_unknown_decision_value_is_ignored_not_an_error(self):
        conv = self.conversation_with("answer")
        self.assertEqual([c.id for c in self.service.list_conversations(self.bot.id, decision="'; DROP TABLE messages;--")], [conv.id])
        self.assertEqual(self.history(decision="khong-co").status_code, 200)

    def test_conversations_without_trace_never_match_a_decision(self):
        plain = self.conversation()
        self.add_message(plain, "customer", "hi")
        self.add_message(plain, "bot", "tin cũ chưa có trace")
        for decision in DECISION_LABELS:
            self.assertEqual(self.service.list_conversations(self.bot.id, decision=decision), [])

    def test_customer_messages_never_count_even_if_they_carry_a_trace(self):
        conversation = self.conversation()
        message = self.add_message(conversation, "customer", "hi")
        message.decision_trace = trace("decline")
        self.db.session.commit()
        self.assertEqual(self.service.list_conversations(self.bot.id, decision="decline"), [])

    def test_filter_combines_with_search_and_channel(self):
        keep = self.conversation_with("decline", visitor="abc123")
        self.conversation_with("decline", visitor="zzz999")
        self.conversation_with("answer", visitor="abc999")
        found = self.service.list_conversations(self.bot.id, search="abc", decision="decline")
        self.assertEqual([c.id for c in found], [keep.id])
        self.assertEqual(self.service.list_conversations(self.bot.id, channel="zalo", decision="decline"), [])

    def test_filter_is_scoped_to_the_bot(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.conversation_with("decline", bot=other)
        self.assertEqual(self.service.list_conversations(self.bot.id, decision="decline"), [])

    def test_route_filters_and_keeps_the_filter_in_links(self):
        declined = self.conversation_with("decline")
        answered = self.conversation_with("answer")
        html = self.history(decision="decline").get_data(as_text=True)
        self.assertIn(f"conversation_id={declined.id}", html)
        self.assertNotIn(f"conversation_id={answered.id}", html)
        self.assertIn("decision=decline", html)
        self.assertIn("1 phiên hội thoại", html)

    def test_empty_filter_result_says_so(self):
        self.conversation_with("answer")
        html = self.history(decision="decline").get_data(as_text=True)
        self.assertIn("Không có hội thoại khớp bộ lọc", html)


class LabelsInTheConversationView(ReviewCase):
    def test_bot_messages_show_the_explanation_and_customer_messages_do_not(self):
        conversation = self.conversation_with("decline")
        html = self.history(conversation_id=conversation.id).get_data(as_text=True)
        self.assertIn("Từ chối trả lời — không tìm thấy tài liệu liên quan (candidate_count=0)", html)
        self.assertEqual(html.count('title="Lý do hệ thống ghi lại'), 1, "đúng 1 nhãn (tin bot), không có ở tin khách")

    def test_answer_and_clarify_labels(self):
        conversation = self.conversation_with("clarify", "answer")
        # clarify không có lý do trong fixture -> chỉ nhãn quyết định; answer nêu số nguồn
        html = self.history(conversation_id=conversation.id).get_data(as_text=True)
        self.assertIn('class="dtag clarify"', html)
        self.assertIn("dựa trên 3 nguồn tài liệu liên quan", html)

    def test_old_messages_without_trace_render_without_label(self):
        conversation = self.conversation()
        self.add_message(conversation, "customer", "hi")
        self.add_message(conversation, "bot", "tin cũ")
        html = self.history(conversation_id=conversation.id).get_data(as_text=True)
        self.assertIn("tin cũ", html)
        self.assertNotIn('class="dtag ', html.split("<body", 1)[-1].split("</style>")[-1])

    def test_label_text_is_escaped(self):
        conversation = self.conversation()
        message = self.add_message(conversation, "bot", "x")
        message.decision_trace = trace("clarify", ["<script>alert(1)</script>"])
        self.db.session.commit()
        html = self.history(conversation_id=conversation.id).get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)", html)


class Stats(ReviewCase):
    def test_counts_and_percentages(self):
        self.conversation_with("answer", "answer", "answer", "decline")
        self.conversation_with("clarify")
        stats = self.service.decision_stats(self.bot.id)
        rows = {r["decision"]: r for r in stats["rows"]}
        self.assertEqual(stats["total"], 5)
        self.assertEqual((rows["answer"]["count"], rows["answer"]["percent"]), (3, 60))
        self.assertEqual((rows["decline"]["count"], rows["decline"]["percent"]), (1, 20))
        self.assertEqual((rows["clarify"]["count"], rows["clarify"]["percent"]), (1, 20))

    def test_empty_bot_has_all_rows_with_zero(self):
        stats = self.service.decision_stats(self.bot.id)
        self.assertEqual(stats["total"], 0)
        self.assertEqual([r["decision"] for r in stats["rows"]], list(DECISION_LABELS))
        self.assertTrue(all(r["count"] == 0 and r["percent"] == 0 for r in stats["rows"]))

    def test_window_excludes_old_messages(self):
        self.conversation_with("decline", created_at=datetime.utcnow() - timedelta(days=45))
        self.conversation_with("answer")
        stats = self.service.decision_stats(self.bot.id, days=30)
        self.assertEqual({r["decision"]: r["count"] for r in stats["rows"]}, {"answer": 1, "clarify": 0, "decline": 0})
        self.assertEqual(self.service.decision_stats(self.bot.id, days=60)["total"], 2)

    def test_scoped_to_the_bot_and_ignores_untraced_messages(self):
        other = self.service.create_bot(self.team.id, "Bot B")
        self.conversation_with("decline", bot=other)
        conversation = self.conversation()
        self.add_message(conversation, "bot", "tin cũ không trace")
        self.assertEqual(self.service.decision_stats(self.bot.id)["total"], 0)

    def test_stats_bar_is_shown_on_the_page_with_filter_links(self):
        self.conversation_with("answer", "decline")
        html = self.history().get_data(as_text=True)
        self.assertIn("Trả lời 50% (1)", html)
        self.assertIn("Từ chối 50% (1)", html)
        self.assertIn("decision=decline", html)

    def test_other_teams_bot_history_is_404_and_shows_nothing(self):
        other_client = self.app.test_client()
        other_team = self.make_team("Team B")
        with other_client.session_transaction() as session:
            session["_user_id"] = str(other_team.user.id)
            session["team_id"] = other_team.id
            session["csrf_token"] = "tok"
        self.conversation_with("decline")
        self.assertEqual(self.history(client=other_client, decision="decline").status_code, 404)

    def test_anonymous_is_redirected_to_login(self):
        response = self.app.test_client().get(f"/bots/{self.bot.id}/history", query_string={"decision": "decline"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])


class CustomerFromSlots(DbCase):
    """Slot contact_* do AI điền (slot-filling của Context Engine) -> Customer, không cần nhân viên nhập tay."""

    def turn(self, conversation, slots, question="Tôi để lại thông tin nhé"):
        self.llm.replies.append(LLMReply(llm_json(slots=slots), usage()))
        customer_message = self.add_message(conversation, "customer", question)
        return self.service.reply_to_customer(self.bot, conversation, customer_message)

    def customers(self):
        self.db.session.expire_all()
        return Customer.query.order_by(Customer.id).all()

    def test_all_three_slots_create_a_normalised_customer_linked_to_the_conversation(self):
        conversation = self.conversation()
        self.turn(conversation, {"contact_name": "  Nguyễn  Văn Nam ", "contact_phone": "0912 345 678", "contact_email": "Nam@Example.COM"})
        (customer,) = self.customers()
        self.assertEqual((customer.name, customer.phone, customer.email), ("Nguyễn Văn Nam", "0912345678", "nam@example.com"))
        self.assertEqual(customer.team_id, self.team.id)
        self.db.session.refresh(conversation)
        self.assertEqual(conversation.customer_id, customer.id)

    def test_slots_filled_over_several_turns_update_the_same_customer_without_overwriting(self):
        conversation = self.conversation()
        self.turn(conversation, {"contact_name": "Nam"})
        self.turn(conversation, {"contact_phone": "0912345678"})
        self.turn(conversation, {"contact_name": "Tên khác", "contact_email": "nam@example.com"})
        (customer,) = self.customers()
        self.assertEqual((customer.name, customer.phone, customer.email), ("Nam", "0912345678", "nam@example.com"))

    def test_a_returning_customer_with_the_same_phone_is_reused_across_conversations(self):
        self.turn(self.conversation(), {"contact_phone": "0912345678", "contact_name": "Nam"})
        second = self.conversation()
        self.turn(second, {"contact_phone": "+84 912 345 678", "contact_email": "nam@example.com"})
        (customer,) = self.customers()
        self.assertEqual((customer.name, customer.email), ("Nam", "nam@example.com"))
        self.db.session.refresh(second)
        self.assertEqual(second.customer_id, customer.id)

    def test_same_phone_in_another_team_is_a_different_customer(self):
        other_team = self.make_team("Team B")
        other_bot = self.service.create_bot(other_team.id, "Bot B")
        self.turn(self.conversation(), {"contact_phone": "0912345678"})
        other_conversation = self.conversation(other_bot)
        self.llm.replies.append(LLMReply(llm_json(slots={"contact_phone": "0912345678"}), usage()))
        customer_message = self.add_message(other_conversation, "customer", "hi")
        self.service.reply_to_customer(other_bot, other_conversation, customer_message)
        self.assertEqual(sorted(c.team_id for c in self.customers()), sorted([self.team.id, other_team.id]))

    def test_invalid_values_are_dropped_and_never_stored_as_junk(self):
        conversation = self.conversation()
        self.turn(conversation, {"contact_phone": "không có", "contact_email": "khong-phai-email", "contact_name": "0912345678"})
        self.assertEqual(self.customers(), [])

    def test_valid_values_survive_next_to_invalid_ones(self):
        self.turn(self.conversation(), {"contact_phone": "abc", "contact_email": "nam@example.com", "contact_name": None})
        (customer,) = self.customers()
        self.assertEqual((customer.name, customer.phone, customer.email), (None, None, "nam@example.com"))

    def test_non_scalar_slot_values_are_ignored(self):
        self.turn(self.conversation(), {"contact_phone": ["0912345678"], "contact_email": {"v": "a@b.vn"}, "contact_name": True})
        self.assertEqual(self.customers(), [])

    def test_numeric_phone_slot_is_accepted_only_when_unambiguous(self):
        self.turn(self.conversation(), {"contact_phone": 84912345678})  # có mã quốc gia -> xác định được
        self.assertEqual([c.phone for c in self.customers()], ["0912345678"])
        self.turn(self.conversation(), {"contact_phone": 987654321})  # số nguyên mất số 0 đầu: không đoán
        self.assertEqual([c.phone for c in self.customers()], ["0912345678"])

    def test_name_only_creates_a_customer_that_later_gets_the_phone(self):
        conversation = self.conversation()
        self.turn(conversation, {"contact_name": "Lan"})
        self.assertEqual([(c.name, c.phone) for c in self.customers()], [("Lan", None)])
        self.turn(conversation, {"contact_phone": "0987654321"})
        self.assertEqual([(c.name, c.phone) for c in self.customers()], [("Lan", "0987654321")])

    def test_other_slots_do_not_create_customers(self):
        self.turn(self.conversation(), {"product": "Gói Pro", "budget": "5 triệu"})
        self.assertEqual(self.customers(), [])

    def test_collect_customer_info_off_stores_nothing(self):
        self.set_settings(collect_customer_info=False)
        self.turn(self.conversation(), {"contact_name": "Nam", "contact_phone": "0912345678"})
        self.assertEqual(self.customers(), [])

    def test_works_together_with_phone_captured_from_message_text(self):
        from app.widget import service as widget_service

        self.llm.replies.append(LLMReply(llm_json(slots={"contact_name": "Nam"}), usage()))
        result = widget_service.receive_message(self.bot, "Số của mình là 0912345678", "visitor-1", None)
        (customer,) = self.customers()
        self.assertEqual((customer.name, customer.phone), ("Nam", "0912345678"))
        self.assertEqual(Conversation.query.get(result["conversation_id"]).customer_id, customer.id)

    def test_a_failed_turn_creates_no_customer(self):
        from core.context_engine.structured import StructuredOutputError

        conversation = self.conversation()
        self.llm.replies += [LLMReply("hỏng", None), LLMReply("vẫn hỏng", None)]
        customer_message = self.add_message(conversation, "customer", "hi")
        with self.assertRaises(StructuredOutputError):
            self.service.reply_to_customer(self.bot, conversation, customer_message)
        self.db.session.rollback()
        self.assertEqual(self.customers(), [])

    def test_slots_and_trace_are_still_persisted(self):
        conversation = self.conversation()
        message = self.turn(conversation, {"contact_name": "Nam"})
        self.assertEqual(json.loads(json.dumps(message.decision_trace))["decision"], "answer")
        self.assertEqual(Message.query.filter_by(sender="bot").count(), 1)


if __name__ == "__main__":
    unittest.main()
