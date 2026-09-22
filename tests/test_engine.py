"""Bộ điều phối 1 lượt (Bước A -> B -> C -> F): mỗi nhánh của cây quyết định đi qua TOÀN BỘ pipeline (không chỉ hàm decide),
kiểm chứng "đúng 1 lệnh gọi DeepSeek chính mỗi lượt", lệnh gọi phụ tách riêng, và decision_trace."""
import json
import unittest

from core import rag_engine
from core.context_engine.builder import RecentRow
from core.context_engine.decision import Decision
from core.context_engine.engine import TurnRequest, run_turn
from core.context_engine.state import ConversationSnapshot, IntentSpec
from core.context_engine.structured import LLMReply, StructuredOutputError
from tests.helpers import FakeLLM, llm_json, settings, usage

PRICE_INTENT = IntentSpec("ask_price", "Hỏi giá", required=("product", "budget"))
TRACE_KEYS = {
    "decision", "intent", "intent_confidence", "slot_completion", "top_retrieval_distance", "distance_gap",
    "candidate_count", "context_pressure", "clarification_turns_used", "reasons",
}


def passage(text="Gói Pro giá 500.000đ/tháng, gồm 10 người dùng.", doc=1, distance=0.5):
    return {
        "metadata": {"document_id": doc, "chunk_indexes": [0]},
        "chunks": [{"index": 0, "content": text, "hit": True}],
        "content": text,
        "distance": distance,
    }


def retrieval(count=3, top=0.5, gap=0.3, passages=None, **kw):
    if passages is None:
        passages = [passage(distance=top)] if count else []
    return rag_engine.RetrievalResult(
        passages=passages, top_distance=top if count else None, second_distance=(top + gap) if count > 1 else None,
        distance_gap=gap if count > 1 else None, candidate_count=count, considered=8, **kw,
    )


def request(question="Gói Pro giá bao nhiêu?", snapshot=None, intents=(), recent=(), conversation_id=1, **overrides):
    return TurnRequest(
        bot_id=1, question=question, settings=settings(**overrides), snapshot=snapshot or ConversationSnapshot(),
        intents=list(intents), recent_rows=list(recent), conversation_id=conversation_id,
    )


def run(req, *replies, retrieved=None, history_hits=(), **kw):
    llm = FakeLLM(*replies)
    retrieved = retrieved if retrieved is not None else retrieval()
    calls = {"retrieve": 0}

    def fake_retrieve(*args, **kwargs):
        calls["retrieve"] += 1
        return retrieved

    result = run_turn(req, llm_call=llm, retrieve_fn=fake_retrieve, history_search_fn=lambda *a, **k: list(history_hits), **kw)
    return result, llm, calls


class HappyPath(unittest.TestCase):
    def test_exactly_one_main_llm_call_and_answer(self):
        result, llm, calls = run(request(), llm_json())
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertEqual(result.reply, "Gói Pro giá 500.000đ.")
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(result.trace["main_llm_calls"], 1)
        self.assertEqual(result.extra_calls, [])
        self.assertEqual(calls["retrieve"], 1)

    def test_trace_has_every_spec_field_and_is_json_serialisable(self):
        result, _, _ = run(request(), llm_json())
        self.assertTrue(TRACE_KEYS <= set(result.trace), TRACE_KEYS - set(result.trace))
        self.assertEqual(result.trace["decision"], "answer")
        self.assertEqual(result.trace["candidate_count"], 3)
        self.assertEqual(result.trace["top_retrieval_distance"], 0.5)
        self.assertEqual(result.trace["reasons"], [])
        json.dumps(result.trace)  # cột messages.decision_trace là JSON

    def test_usage_of_main_call_is_returned(self):
        result, _, _ = run(request(), LLMReply(llm_json(), usage(2478, 20, hit=2304)))
        self.assertEqual((result.usage.prompt_tokens, result.usage.cache_hit_tokens, result.usage.cache_miss_tokens), (2478, 2304, 174))

    def test_missing_usage_never_breaks_the_reply(self):
        result, _, _ = run(request(), LLMReply(llm_json(), None))
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIsNone(result.usage.prompt_tokens)

    def test_prompt_is_role_separated_with_rag_and_recent_messages(self):
        recent = [RecentRow(1, "customer", "Xin chào"), RecentRow(2, "bot", "Chào anh, em giúp gì ạ?")]
        _, llm, _ = run(request(recent=recent, instructions="Bạn là trợ lý An Phát."), llm_json())
        messages = llm.calls[0]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertIn("An Phát", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "Xin chào")
        self.assertIn("Gói Pro giá 500.000đ/tháng", messages[-1]["content"])
        self.assertIn("Gói Pro giá bao nhiêu?", messages[-1]["content"])

    def test_answer_resets_clarification_counter(self):
        snap = ConversationSnapshot(clarification_turns_used=1)
        result, _, _ = run(request(snapshot=snap), llm_json())
        self.assertEqual(result.state_update.clarification_turns_used, 0)
        self.assertEqual(result.trace["clarification_turns_used"], 0)


class BranchesThroughPipeline(unittest.TestCase):
    def test_no_relevant_chunk_uses_owner_message_and_ignores_llm_answer(self):
        req = request(low_confidence_clarify_message="Bạn hỏi về sản phẩm nào ạ?")
        result, llm, _ = run(req, llm_json(proposed_answer="TỰ BỊA 1 TỶ"), retrieved=retrieval(count=0))
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.reply, "Bạn hỏi về sản phẩm nào ạ?")
        self.assertNotIn("1 TỶ", result.reply)
        self.assertEqual(len(llm.calls), 1)
        self.assertIn("no_relevant_context", result.trace["reasons"])
        self.assertEqual(result.trace["candidate_count"], 0)
        self.assertEqual(result.state_update.clarification_turns_used, 1)

    def test_no_relevant_chunk_decline_mode(self):
        req = request(low_confidence_reply_mode="decline", low_confidence_decline_message="Chưa có thông tin.")
        result, _, _ = run(req, llm_json(), retrieved=retrieval(count=0))
        self.assertEqual((result.decision, result.reply), (Decision.DECLINE, "Chưa có thông tin."))
        self.assertEqual(result.state_update.clarification_turns_used, 0)

    def test_low_intent_confidence_clarifies_with_llm_question(self):
        reply = llm_json(intent_confidence=0.4, proposed_answer="", proposed_clarification_question="Bạn muốn hỏi giá gói nào?")
        result, _, _ = run(request(), reply)
        self.assertEqual((result.decision, result.reply), (Decision.CLARIFY, "Bạn muốn hỏi giá gói nào?"))
        self.assertEqual(result.trace["reasons"], ["low_intent_confidence"])
        self.assertEqual(result.state_update.clarification_turns_used, 1)

    def test_missing_required_slots_clarify_then_complete_answers(self):
        first = llm_json(intent="ask_price", slots={"product": "Pro", "budget": None}, proposed_answer="",
                         proposed_clarification_question="Ngân sách của bạn là bao nhiêu?")
        r1, _, _ = run(request(intents=[PRICE_INTENT]), first)
        self.assertEqual(r1.decision, Decision.CLARIFY)
        self.assertEqual(r1.trace["slot_completion"], 0.5)
        self.assertEqual(r1.state_update.slots, {"product": "Pro", "budget": None})

        snap = ConversationSnapshot(current_intent="ask_price", slots={"product": "Pro"}, clarification_turns_used=1)
        r2, _, _ = run(request(snapshot=snap, intents=[PRICE_INTENT]), llm_json(intent="ask_price", slots={"budget": "5 triệu"}))
        self.assertEqual(r2.decision, Decision.ANSWER)
        self.assertEqual(r2.trace["slot_completion"], 1.0)
        self.assertEqual(r2.state_update.slots, {"product": "Pro", "budget": "5 triệu"}, "slot cũ được giữ, slot mới được ghi")
        self.assertEqual(r2.state_update.clarification_turns_used, 0)

    def test_many_equally_relevant_sources_clarify(self):
        result, _, _ = run(request(), llm_json(proposed_answer="", proposed_clarification_question="Bạn quan tâm sản phẩm nào?"),
                           retrieved=retrieval(count=7, gap=0.01))
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.trace["reasons"], ["too_many_relevant_candidates"])

    def test_many_sources_but_clear_winner_answers(self):
        result, _, _ = run(request(), llm_json(), retrieved=retrieval(count=7, gap=0.4))
        self.assertEqual(result.decision, Decision.ANSWER)

    def test_clarification_cap_forces_answer_and_resets_counter(self):
        snap = ConversationSnapshot(clarification_turns_used=2)
        reply = llm_json(intent_confidence=0.3, self_assessed_confidence=0.2)
        result, _, _ = run(request(snapshot=snap), reply)
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertTrue(result.trace["forced_answer"])
        self.assertIn("Lưu ý", result.reply)
        self.assertEqual(result.state_update.clarification_turns_used, 0)

    def test_clarification_switch_off_never_clarifies(self):
        result, _, _ = run(request(clarification_enabled=False), llm_json(intent_confidence=0.2))
        self.assertEqual(result.decision, Decision.ANSWER)


class ContextPressure(unittest.TestCase):
    def heavy(self, **overrides):
        recent = [RecentRow(i + 1, "customer" if i % 2 == 0 else "bot", "noi dung tin nhan cu " * 60 + str(i)) for i in range(10)]
        passages = [passage("thong tin san pham dai " * 60 + f" muc {i}", doc=i, distance=0.4 + i / 10) for i in range(5)]
        # clarification_enabled=False: cô lập việc NÉN ngữ cảnh khỏi chế độ thu hẹp phạm vi (chunk lớn thế này sẽ vượt ngân sách
        # RAG và — nếu được phép hỏi làm rõ — bot sẽ hỏi thu hẹp; nhánh đó có test riêng ở lớp ScopeNarrowing bên dưới)
        return request(recent=recent, max_context_tokens=3000, recent_token_limit=8000, rag_max_context_tokens=3000,
                       clarification_enabled=False, **overrides), passages

    def test_high_pressure_compresses_first_then_answers_and_never_clarifies(self):
        req, passages = self.heavy()
        result, llm, _ = run(req, llm_json(), retrieved=retrieval(count=5, passages=passages))
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIn(result.trace["pressure_level"], ("strong", "hard"))
        self.assertGreater(result.trace["context_pressure"], 0.8)
        self.assertTrue(result.trace["compression_steps"])
        self.assertLess(result.trace["context_pressure_after_compression"], result.trace["context_pressure"])
        self.assertIn("context_compressed", result.trace["reasons"])
        self.assertEqual(len(llm.calls), 1, "vẫn chỉ 1 lệnh gọi chính")

    def test_compression_actually_shrinks_what_is_sent(self):
        req, passages = self.heavy()
        _, compressed_llm, _ = run(req, llm_json(), retrieved=retrieval(count=5, passages=passages))
        roomy = request(recent=req.recent_rows, max_context_tokens=100_000, recent_token_limit=8000, rag_max_context_tokens=3000)
        _, roomy_llm, _ = run(roomy, llm_json(), retrieved=retrieval(count=5, passages=passages))
        size = lambda llm: sum(len(m["content"]) for m in llm.calls[0])
        self.assertLess(size(compressed_llm), size(roomy_llm))

    def test_low_pressure_is_untouched(self):
        result, _, _ = run(request(), llm_json())
        self.assertEqual(result.trace["pressure_level"], "normal")
        self.assertEqual(result.trace["compression_steps"], [])

    def test_no_budget_left_for_context_is_treated_as_no_context(self):
        # Các phần khác + dự phòng đầu ra đã ăn hết ngân sách: bot không thấy tài liệu nào -> không được trả lời như đã có
        req = request(max_context_tokens=800, max_tokens=500, low_confidence_reply_mode="decline", low_confidence_decline_message="Chưa có thông tin.")
        result, _, _ = run(req, llm_json(), retrieved=retrieval(count=3))
        self.assertEqual(result.decision, Decision.DECLINE)
        self.assertIn("context_budget_exhausted", result.trace["reasons"])
        self.assertEqual(result.trace["candidate_count"], 3, "trace giữ số ứng viên thật")


class ScopeNarrowing(unittest.TestCase):
    """Chunk tìm được vượt "Ngân sách token cho thông tin tra cứu": đưa phần mở đầu của TỪNG chunk và hỏi thu hẹp phạm vi; khi phạm vi
    đã đủ nhỏ (vừa ngân sách) thì trả lời trực tiếp."""

    BUDGET = 1000  # rag_max_context_tokens dùng trong các test

    @staticmethod
    def big_passages(n=6):
        # ~300+ token mỗi chunk -> tổng vượt xa ngân sách 1000
        return [passage(f"# Mục {i}\n" + f"noi dung chi tiet cua muc {i} " * 80, doc=i, distance=0.4 + i / 20) for i in range(n)]

    def req(self, **kw):
        kw.setdefault("rag_max_context_tokens", self.BUDGET)
        return request(**kw)

    NARROW = llm_json(proposed_answer="", proposed_clarification_question="Bạn muốn hỏi về mục nào ạ?")

    def test_overflow_sends_heads_of_every_chunk_and_asks_to_narrow(self):
        result, llm, _ = run(self.req(), self.NARROW, retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertEqual((result.decision, result.reply), (Decision.CLARIFY, "Bạn muốn hỏi về mục nào ạ?"))
        self.assertEqual(result.trace["reasons"], ["context_exceeds_budget"])
        self.assertEqual(len(llm.calls), 1)
        overflow = result.trace["context_overflow"]
        self.assertGreater(overflow["found_tokens"], overflow["budget"])
        self.assertEqual((overflow["found_chunks"], overflow["shown_chunks"]), (6, 6), "mọi chunk đều có mặt, chỉ bị cắt phần sau")
        self.assertGreaterEqual(overflow["truncated_chunks"], 1)
        last = llm.calls[0][-1]["content"]
        for i in range(6):
            self.assertIn(f"# Mục {i}", last)
        self.assertIn("LƯU Ý", last)
        self.assertEqual(result.state_update.clarification_turns_used, 1)

    def test_prompt_stays_within_the_rag_budget(self):
        result, _, _ = run(self.req(), self.NARROW, retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertLessEqual(result.trace["rag"]["rag_tokens"], self.BUDGET + 20)  # + ký tự ngăn cách/dấu "…"

    def test_narrowed_scope_that_fits_is_answered_directly(self):
        snap = ConversationSnapshot(clarification_turns_used=1)
        small = retrieval(count=1, passages=[passage("Mục 2: giá 500.000đ/tháng.", doc=2)])
        result, llm, _ = run(self.req(snapshot=snap, question="Mục 2"), llm_json(), retrieved=small)
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIsNone(result.trace["context_overflow"])
        self.assertNotIn("LƯU Ý", llm.calls[0][-1]["content"])
        self.assertEqual(result.state_update.clarification_turns_used, 0)

    def test_keeps_narrowing_while_still_over_budget(self):
        snap = ConversationSnapshot(clarification_turns_used=1)
        result, _, _ = run(self.req(snapshot=snap), self.NARROW, retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(result.state_update.clarification_turns_used, 2)

    def test_after_max_turns_answers_from_the_best_chunks_that_fit(self):
        snap = ConversationSnapshot(clarification_turns_used=2)
        result, llm, _ = run(self.req(snapshot=snap), llm_json(), retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIsNone(result.trace["context_overflow"])
        last = llm.calls[0][-1]["content"]
        self.assertNotIn("LƯU Ý", last)
        self.assertIn("# Mục 0", last, "giữ chunk liên quan nhất")
        self.assertNotIn("# Mục 5", last, "chunk kém liên quan nhất bị bỏ để vừa ngân sách")
        self.assertEqual(result.state_update.clarification_turns_used, 0)

    def test_respects_max_clarification_turns_setting(self):
        snap = ConversationSnapshot(clarification_turns_used=1)
        result, _, _ = run(self.req(snapshot=snap, max_clarification_turns=1), llm_json(), retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertEqual(result.decision, Decision.ANSWER)

    def test_clarification_switch_off_never_narrows(self):
        result, llm, _ = run(self.req(clarification_enabled=False), llm_json(), retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertNotIn("LƯU Ý", llm.calls[0][-1]["content"])

    def test_fits_the_budget_means_no_narrowing(self):
        result, llm, _ = run(self.req(), llm_json(), retrieved=retrieval(count=2, passages=[passage("ngắn", doc=1), passage("cũng ngắn", doc=2)]))
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIsNone(result.trace["context_overflow"])

    def test_llm_that_ignores_the_instruction_still_clarifies_with_a_safe_question(self):
        # LLM lỡ trả lời nội dung thay vì hỏi: quyết định vẫn là CLARIFY (không phát câu trả lời dựa trên phần trích cụt)
        result, _, _ = run(self.req(), llm_json(proposed_answer="Mục 1 giá 100đ", proposed_clarification_question=""),
                           retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertNotIn("100đ", result.reply)

    def test_english_note(self):
        _, llm, _ = run(self.req(language="en"), self.NARROW, retrieved=retrieval(count=6, passages=self.big_passages()))
        self.assertIn("NOTE: there is too much relevant content", llm.calls[0][-1]["content"])


class KnowledgeBase(unittest.TestCase):
    def test_rag_switch_off_skips_retrieval_and_rule_1(self):
        result, _, calls = run(request(rag_enabled=False), llm_json(), retrieved=retrieval(count=0))
        self.assertEqual(calls["retrieve"], 0)
        self.assertEqual(result.decision, Decision.ANSWER)

    def test_empty_knowledge_base_does_not_trigger_rule_1(self):
        result, _, calls = run(request(), llm_json(), retrieved=rag_engine.RetrievalResult(knowledge_empty=True))
        self.assertEqual(calls["retrieve"], 1)
        self.assertEqual(result.decision, Decision.ANSWER)

    def test_retrieval_uses_bot_settings(self):
        seen = {}

        def spy(bot_id, question, **kwargs):
            seen.update(kwargs, bot_id=bot_id)
            return retrieval()

        run_turn(request(rag_top_k=11, rag_rerank_top_n=4, rag_distance_threshold=1.2), llm_call=FakeLLM(llm_json()), retrieve_fn=spy)
        self.assertEqual(seen, {"bot_id": 1, "top_k": 11, "distance_threshold": 1.2, "rerank_top_n": 4})


class HistoryLookup(unittest.TestCase):
    HITS = [{"message_id": 5, "sender": "customer", "content": "Tôi tên là Nam, số điện thoại 0901234567", "distance": 0.3}]

    def first(self, **kw):
        return llm_json(needs_history_lookup=True, proposed_answer="Tôi chưa rõ tên bạn.", **kw)

    def test_second_call_completes_the_answer_and_is_counted_separately(self):
        second = LLMReply(llm_json(proposed_answer="Tên bạn là Nam."), usage(400, 30))
        result, llm, _ = run(request(question="Tên tôi là gì?"), LLMReply(self.first(), usage(1000, 50)), second, history_hits=self.HITS)
        self.assertEqual(result.reply, "Tên bạn là Nam.")
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("Tôi tên là Nam", llm.calls[1][-1]["content"], "ngữ cảnh lịch sử được đưa vào lệnh gọi phụ")
        self.assertEqual(result.trace["main_llm_calls"], 1, "thống kê 1 lệnh gọi/lượt không lẫn lệnh gọi phụ")
        self.assertEqual([c["kind"] for c in result.extra_calls], ["history_lookup"])
        self.assertEqual(result.usage.prompt_tokens, 1000, "Message.usage_* chỉ tính lệnh gọi chính")
        self.assertEqual(result.trace["history_lookup"], {"hits": 1, "called_llm": True, "status": "ok"})

    def test_nothing_found_means_no_second_call(self):
        result, llm, _ = run(request(), self.first(), history_hits=[])
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(result.reply, "Tôi chưa rõ tên bạn.")
        self.assertEqual(result.trace["history_lookup"], {"hits": 0, "called_llm": False})

    def test_not_available_without_a_stored_conversation(self):
        result, llm, _ = run(request(conversation_id=None), self.first(), history_hits=self.HITS)
        self.assertEqual(len(llm.calls), 1)
        self.assertIsNone(result.trace["history_lookup"])

    def test_not_attempted_when_decision_is_not_answer(self):
        reply = llm_json(needs_history_lookup=True, intent_confidence=0.2, proposed_answer="", proposed_clarification_question="Bạn nói rõ hơn?")
        result, llm, _ = run(request(), reply, history_hits=self.HITS)
        self.assertEqual(result.decision, Decision.CLARIFY)
        self.assertEqual(len(llm.calls), 1)

    def test_failed_second_call_keeps_the_valid_first_answer(self):
        def broken_second(_):
            raise ConnectionError("mạng đứt")

        llm = FakeLLM(self.first())
        first = llm.replies.pop(0)
        calls = iter([lambda m: first, broken_second])
        result = run_turn(request(), llm_call=lambda m: next(calls)(m), retrieve_fn=lambda *a, **k: retrieval(),
                          history_search_fn=lambda *a, **k: self.HITS)
        self.assertEqual(result.reply, "Tôi chưa rõ tên bạn.")
        self.assertEqual(result.trace["history_lookup"]["status"], "error")

    def test_second_call_without_answer_keeps_first(self):
        second = llm_json(proposed_answer="", proposed_clarification_question="Bạn là ai?")
        result, _, _ = run(request(), self.first(), second, history_hits=self.HITS)
        self.assertEqual(result.reply, "Tôi chưa rõ tên bạn.")
        self.assertEqual(result.trace["history_lookup"]["status"], "empty_answer")

    def test_messages_already_in_prompt_are_excluded_from_search(self):
        captured = {}

        def spy(bot_id, conversation_id, question, **kwargs):
            captured.update(kwargs, conversation_id=conversation_id)
            return []

        recent = [RecentRow(3, "customer", "a"), RecentRow(4, "bot", "b")]
        run_turn(request(recent=recent, conversation_id=77), llm_call=FakeLLM(self.first()), retrieve_fn=lambda *a, **k: retrieval(), history_search_fn=spy)
        self.assertEqual(captured["exclude_message_ids"], {3, 4})
        self.assertEqual(captured["conversation_id"], 77)


class StructuredOutputFailures(unittest.TestCase):
    def test_bad_json_is_retried_once_and_counted(self):
        result, llm, _ = run(request(), "không phải json", llm_json())
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(result.trace["main_llm_calls"], 2)
        self.assertEqual(result.decision, Decision.ANSWER)

    def test_two_bad_replies_raise_and_nothing_is_faked(self):
        with self.assertRaises(StructuredOutputError):
            run(request(), "x", "y")


class OtherSignals(unittest.TestCase):
    def test_ambiguous_reference_is_only_flagged(self):
        result, _, _ = run(request(question="Cái này giá bao nhiêu?"), llm_json())
        self.assertTrue(result.trace["is_ambiguous_reference"])
        self.assertEqual(result.decision, Decision.ANSWER)
        self.assertIn("ambiguous_reference", result.trace["reasons"])

    def test_intent_change_recorded(self):
        snap = ConversationSnapshot(current_intent="ask_price")
        result, _, _ = run(request(snapshot=snap), llm_json(intent="ask_warranty"))
        self.assertTrue(result.trace["intent_changed"])
        self.assertEqual((result.state_update.current_intent, result.state_update.previous_intent), ("ask_warranty", "ask_price"))

    def test_memory_updates_returned_for_persistence(self):
        item = {"category": "constraint", "key": "ngân sách", "value": "30 triệu", "confidence": 0.9}
        result, _, _ = run(request(), llm_json(memory_updates=[item]))
        self.assertEqual(result.output.memory_updates, [item])

    def test_english_bot_gets_english_default_messages(self):
        req = request(language="en", low_confidence_reply_mode="decline")
        result, _, _ = run(req, llm_json(), retrieved=retrieval(count=0))
        self.assertIn("Sorry", result.reply)


if __name__ == "__main__":
    unittest.main()
