"""Luồng có DB thật (DB *_test): Bước D/E của 1 lượt, việc nền, widget, multi-tenant."""
import json
from datetime import datetime, timedelta

from app.models import (
    Conversation, ConversationMessageEmbedding, ConversationState, Message, StructuredMemory,
)
from core import rag_engine
from core.context_engine import cost, jobs, state as ctx_state
from core.context_engine.settings import EngineSettings
from core.context_engine.structured import LLMReply
from tests.chroma_fixture import InMemoryChroma
from tests.db_case import DbCase
from tests.helpers import llm_json, usage
from tests.test_engine import retrieval


class TurnPersistence(DbCase):
    def turn(self, conversation, question, reply, **kw):
        self.llm.replies.append(LLMReply(reply, kw.pop("token_usage", usage(1200, 60, hit=1024))))
        customer = self.add_message(conversation, "customer", question)
        return customer, self.service.reply_to_customer(self.bot, conversation, customer)

    def test_bot_message_carries_trace_usage_and_state_and_memory(self):
        conversation = self.conversation()
        memory = [
            {"category": "constraint", "key": "ngân sách", "value": "30 triệu", "confidence": 0.9},
            {"category": "preference", "key": "màu", "value": "đen", "confidence": 0.5},  # dưới memory_min_confidence 0,70
        ]
        _, bot_message = self.turn(conversation, "Tôi cần 3 máy, ngân sách 30 triệu", llm_json(
            intent="ask_price", slots={"quantity": 3}, memory_updates=memory))

        stored = self.db.session.get(Message, bot_message.id)
        self.assertEqual((stored.sender, stored.content), ("bot", "Gói Pro giá 500.000đ."))
        self.assertEqual(stored.decision_trace["decision"], "answer")
        self.assertEqual(stored.decision_trace["main_llm_calls"], 1)
        self.assertEqual((stored.usage_prompt_tokens, stored.usage_completion_tokens), (1200, 60))
        self.assertEqual((stored.usage_cache_hit_tokens, stored.usage_cache_miss_tokens), (1024, 176))
        json.dumps(stored.decision_trace)

        state = ConversationState.query.filter_by(conversation_id=conversation.id).one()
        self.assertEqual((state.bot_id, state.current_intent, state.slots), (self.bot.id, "ask_price", {"quantity": 3}))
        rows = StructuredMemory.query.filter_by(conversation_id=conversation.id).all()
        self.assertEqual([(r.mem_key, r.value, r.bot_id) for r in rows], [("ngân sách", "30 triệu", self.bot.id)])

    def test_unreported_usage_is_stored_as_null_not_zero(self):
        conversation = self.conversation()
        _, bot_message = self.turn(conversation, "Giá?", llm_json(), token_usage=None)
        stored = self.db.session.get(Message, bot_message.id)
        self.assertIsNone(stored.usage_prompt_tokens)
        self.assertIsNone(stored.usage_cache_hit_tokens)

    def test_memory_updates_same_key_and_respects_max_items(self):
        self.set_settings(config_tier="expert", memory_max_items=2)
        conversation = self.conversation()
        item = lambda key, value, conf: {"category": "entity", "key": key, "value": value, "confidence": conf}
        self.turn(conversation, "a", llm_json(memory_updates=[item("tên", "Nam", 0.9), item("sdt", "0901", 0.8)]))
        self.turn(conversation, "b", llm_json(memory_updates=[item("tên", "Nam Nguyễn", 0.95), item("email", "a@b.vn", 0.75)]))
        rows = {r.mem_key: r.value for r in StructuredMemory.query.filter_by(conversation_id=conversation.id).all()}
        self.assertEqual(rows, {"tên": "Nam Nguyễn", "sdt": "0901"}, "cùng key -> ghi đè; vượt 2 mục -> bỏ mục confidence thấp nhất (email 0,75)")

    def test_memory_switch_off_stores_nothing_and_does_not_read(self):
        self.set_settings(structured_memory_enabled=False)
        conversation = self.conversation()
        self.turn(conversation, "a", llm_json(memory_updates=[{"category": "entity", "key": "k", "value": "v", "confidence": 0.99}]))
        self.assertEqual(StructuredMemory.query.count(), 0)

    def test_memory_from_earlier_turn_reaches_the_next_prompt(self):
        conversation = self.conversation()
        self.turn(conversation, "Tôi tên Nam", llm_json(memory_updates=[{"category": "entity", "key": "tên", "value": "Nam", "confidence": 0.9}]))
        self.turn(conversation, "Tôi tên gì?", llm_json())
        system = self.llm.calls[1][0]["content"]
        self.assertIn("[entity] tên: Nam", system)

    def test_clarification_cap_over_real_turns(self):
        conversation = self.conversation()
        clarify = llm_json(intent_confidence=0.3, proposed_answer="Đây là câu trả lời", proposed_clarification_question="Bạn nói rõ hơn được không?")
        decisions, counters = [], []
        for q in ("a", "b", "c", "d"):
            _, message = self.turn(conversation, q, clarify)
            decisions.append(message.decision_trace["decision"])
            counters.append(ConversationState.query.filter_by(conversation_id=conversation.id).one().clarification_turns_used)
        self.assertEqual(decisions, ["clarify", "clarify", "answer", "clarify"])
        self.assertEqual(counters, [1, 2, 0, 1], "hết 2 lượt hỏi làm rõ thì ép trả lời rồi bắt đầu đợt hỏi mới")

    def test_recent_messages_exclude_current_and_staff_and_use_roles(self):
        conversation = self.conversation()
        self.add_message(conversation, "customer", "Xin chào")
        self.add_message(conversation, "bot", "Chào bạn")
        self.add_message(conversation, "staff", "NHÂN VIÊN NÓI RIÊNG")
        self.turn(conversation, "Giá gói Pro?", llm_json())
        messages = self.llm.calls[0]
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        blob = json.dumps(messages, ensure_ascii=False)
        self.assertNotIn("NHÂN VIÊN NÓI RIÊNG", blob)
        self.assertEqual(blob.count("Giá gói Pro?"), 1, "câu hỏi hiện tại chỉ xuất hiện 1 lần (không nằm trong lịch sử)")

    def test_old_history_limits_are_gone(self):
        from app.dashboard import service
        self.assertFalse(hasattr(service, "HISTORY_MESSAGES"))
        self.assertFalse(hasattr(service, "clean_history"))
        from core import rag_engine
        self.assertFalse(hasattr(rag_engine, "build_prompt"), "cách nối 1 chuỗi cũ đã bị xóa")
        self.assertFalse(hasattr(rag_engine, "answer"))

    def test_history_size_follows_recent_message_limit_setting(self):
        self.set_settings(config_tier="advanced", recent_message_limit=4)
        conversation = self.conversation()
        for i in range(10):
            self.add_message(conversation, "customer" if i % 2 == 0 else "bot", f"tin {i}")
        self.turn(conversation, "câu hỏi mới", llm_json())
        history = [m["content"] for m in self.llm.calls[0][1:-1]]
        self.assertEqual(history, ["tin 6", "tin 7", "tin 8", "tin 9"])

    def test_llm_failure_writes_nothing_but_keeps_customer_message(self):
        conversation = self.conversation()
        customer = self.add_message(conversation, "customer", "Giá?")
        self.llm.replies.append(LLMReply("hỏng", None))
        self.llm.replies.append(LLMReply("vẫn hỏng", None))
        from core.context_engine.structured import StructuredOutputError

        with self.assertRaises(StructuredOutputError):
            self.service.reply_to_customer(self.bot, conversation, customer)
        self.db.session.rollback()
        self.assertEqual(Message.query.filter_by(sender="bot").count(), 0)
        self.assertEqual(Message.query.filter_by(sender="customer").count(), 1)
        self.assertEqual(StructuredMemory.query.count(), 0)


class SummaryTrigger(DbCase):
    def fill(self, conversation, n, words):
        for i in range(n):
            self.add_message(conversation, "customer" if i % 2 == 0 else "bot", ("noi dung dai " * words) + str(i))

    def reply(self, conversation):
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        customer = self.add_message(conversation, "customer", "câu hỏi")
        self.service.reply_to_customer(self.bot, conversation, customer)
        return ConversationState.query.filter_by(conversation_id=conversation.id).one()

    def test_flag_set_when_unsummarised_tokens_exceed_trigger(self):
        self.set_settings(config_tier="advanced", summary_trigger_tokens=500)
        conversation = self.conversation()
        self.fill(conversation, 6, 60)
        self.assertTrue(self.reply(conversation).summary_pending)

    def test_flag_not_set_below_trigger(self):
        self.set_settings(config_tier="advanced", summary_trigger_tokens=20_000)
        conversation = self.conversation()
        self.fill(conversation, 4, 10)
        self.assertFalse(self.reply(conversation).summary_pending)

    def test_flag_never_set_when_summary_disabled(self):
        self.set_settings(config_tier="basic", summary_enabled=False)
        conversation = self.conversation()
        self.fill(conversation, 12, 200)
        self.assertFalse(self.reply(conversation).summary_pending)

    def test_the_request_does_not_call_the_llm_for_summary(self):
        self.set_settings(config_tier="advanced", summary_trigger_tokens=500)
        conversation = self.conversation()
        self.fill(conversation, 6, 60)
        self.reply(conversation)
        self.assertEqual(len(self.llm.calls), 1, "tóm tắt chạy ở nền, không nằm trong luồng trả lời realtime")

    def test_only_unsummarised_messages_are_counted(self):
        self.set_settings(config_tier="advanced", summary_trigger_tokens=500)
        conversation = self.conversation()
        self.fill(conversation, 6, 60)
        last = Message.query.filter_by(conversation_id=conversation.id).order_by(Message.id.desc()).first()
        state = ctx_state.get_or_create_state(conversation)
        state.last_summarized_message_id = last.id  # đã tóm tắt hết
        self.db.session.commit()
        self.assertFalse(self.reply(conversation).summary_pending)


class SummaryJob(DbCase):
    def setUp(self):
        super().setUp()
        self.set_settings(config_tier="advanced", recent_message_limit=4)
        self.conversation_ = self.conversation()
        self.messages = [self.add_message(self.conversation_, "customer" if i % 2 == 0 else "bot", f"tin so {i}") for i in range(12)]
        self.state = ctx_state.get_or_create_state(self.conversation_)
        self.state.summary_pending = True
        self.db.session.commit()
        self.settings = EngineSettings.from_model(self.service.get_or_create_settings(self.bot))

    def fake(self, text="Khách cần 3 máy tính."):
        seen = []

        def call(messages):
            seen.append(messages)
            return LLMReply(text, usage(500, 40))

        return call, seen

    def test_summarises_messages_older_than_the_recent_window(self):
        call, seen = self.fake()
        self.assertTrue(jobs.summarize_conversation(self.state, self.settings, call=call))
        self.db.session.refresh(self.state)
        self.assertEqual(self.state.summary, "Khách cần 3 máy tính.")
        self.assertEqual(self.state.last_summarized_message_id, self.messages[7].id, "4 tin mới nhất (8-11) ở lại cửa sổ gần đây, tin 0-7 được tóm tắt")
        self.assertFalse(self.state.summary_pending)
        self.assertIsNotNone(self.state.summary_updated_at)
        prompt = json.dumps(seen[0], ensure_ascii=False)
        self.assertIn("tin so 0", prompt)
        self.assertNotIn("tin so 9", prompt)

    def test_old_summary_is_fed_into_the_next_round(self):
        call, seen = self.fake()
        self.state.summary = "TÓM TẮT CŨ ĐÃ CÓ"
        self.state.last_summarized_message_id = self.messages[3].id
        self.db.session.commit()
        jobs.summarize_conversation(self.state, self.settings, call=call)
        prompt = json.dumps(seen[0], ensure_ascii=False)
        self.assertIn("TÓM TẮT CŨ ĐÃ CÓ", prompt)
        self.assertNotIn("tin so 3", prompt, "tin đã tóm tắt không bị đưa vào lần sau")
        self.assertIn("tin so 4", prompt)

    def test_llm_failure_keeps_flag_and_state(self):
        def broken(_):
            raise ConnectionError("mạng đứt")

        with self.assertRaises(ConnectionError):
            jobs.summarize_conversation(self.state, self.settings, call=broken)
        self.db.session.rollback()
        self.db.session.refresh(self.state)
        self.assertTrue(self.state.summary_pending)
        self.assertIsNone(self.state.summary)

    def test_empty_llm_output_is_an_error_not_an_empty_summary(self):
        call, _ = self.fake("   ")
        with self.assertRaises(RuntimeError):
            jobs.summarize_conversation(self.state, self.settings, call=call)
        self.assertIsNone(self.state.summary)

    def test_nothing_outside_the_window_clears_the_flag_without_calling_llm(self):
        self.db.session.execute(__import__("sqlalchemy").text("DELETE FROM messages WHERE id < :i"), {"i": self.messages[9].id})
        self.db.session.commit()
        call, seen = self.fake()
        self.assertFalse(jobs.summarize_conversation(self.state, self.settings, call=call))
        self.assertEqual(seen, [])
        self.db.session.refresh(self.state)
        self.assertFalse(self.state.summary_pending)

    def test_disabled_summary_clears_flag(self):
        self.set_settings(summary_enabled=False, config_tier="expert")
        settings = EngineSettings.from_model(self.service.get_or_create_settings(self.bot))
        call, seen = self.fake()
        self.assertFalse(jobs.summarize_conversation(self.state, settings, call=call))
        self.assertEqual(seen, [])

    def test_summary_reaches_the_next_prompt_and_summarised_messages_leave_it(self):
        call, _ = self.fake("BẢN TÓM TẮT: khách cần 3 máy.")
        jobs.summarize_conversation(self.state, self.settings, call=call)
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        customer = self.add_message(self.conversation_, "customer", "câu hỏi mới")
        self.service.reply_to_customer(self.bot, self.conversation_, customer)
        messages = self.llm.calls[0]
        self.assertIn("BẢN TÓM TẮT: khách cần 3 máy.", messages[0]["content"])
        history = " ".join(m["content"] for m in messages[1:-1])
        self.assertNotIn("tin so 3", history)
        self.assertIn("tin so 11", history)


class EmbedJob(DbCase, InMemoryChroma):
    """Nhiều kế thừa: DbCase.setUp rồi InMemoryChroma.setUp (Chroma trong bộ nhớ + embedding giả)."""

    def setUp(self):
        DbCase.setUp(self)
        InMemoryChroma.setUp(self)
        self.other_team = self.make_team("Team B")
        self.other_bot = self.service.create_bot(self.other_team.id, "Bot B")
        self.addCleanup(self._drop_history_collections)

    def _drop_history_collections(self):
        for bot in (self.bot, self.other_bot):
            try:
                rag_engine.chroma_client.delete_collection(f"history_{bot.id}")
            except Exception:  # collection chưa được tạo trong test này
                pass

    def test_embeds_customer_and_bot_messages_into_the_right_bot_collection(self):
        a, b = self.conversation(), self.conversation(self.other_bot)
        m1 = self.add_message(a, "customer", "toi ten la Nam")
        m2 = self.add_message(a, "bot", "chao anh Nam")
        self.add_message(a, "staff", "tin nhan vien khong duoc embed")
        m4 = self.add_message(b, "customer", "tin cua bot khac")
        self.assertEqual(jobs.embed_pending_messages(), 3, "khách + bot của 2 bot; tin nhân viên bị bỏ")

        from core.context_engine import history_retrieval as hr

        self.assertEqual(hr.get_history_collection(self.bot.id).count(), 2)
        self.assertEqual(hr.get_history_collection(self.other_bot.id).count(), 1)
        got = hr.get_history_collection(self.bot.id).get(ids=[f"msg-{m1.id}"])
        self.assertEqual(got["metadatas"][0]["conversation_id"], a.id)
        self.assertEqual(got["metadatas"][0]["sender"], "customer")
        marks = {(r.message_id, r.bot_id) for r in ConversationMessageEmbedding.query.all()}
        self.assertEqual(marks, {(m1.id, self.bot.id), (m2.id, self.bot.id), (m4.id, self.other_bot.id)})

    def test_is_idempotent_and_picks_up_only_new_messages(self):
        a = self.conversation()
        self.add_message(a, "customer", "tin dau")
        self.assertEqual(jobs.embed_pending_messages(), 1)
        self.assertEqual(jobs.embed_pending_messages(), 0)
        self.add_message(a, "customer", "tin moi")
        self.assertEqual(jobs.embed_pending_messages(), 1)

    def test_backfills_messages_that_existed_before_the_feature(self):
        a = self.conversation()
        for i in range(5):
            self.add_message(a, "customer", f"tin cu {i}")
        self.assertEqual(jobs.embed_pending_messages(limit=3), 3)
        self.assertEqual(jobs.embed_pending_messages(limit=3), 2)
        self.assertEqual(jobs.embed_pending_messages(limit=3), 0)

    def test_history_lookup_end_to_end_in_the_engine(self):
        a = self.conversation()
        self.add_message(a, "customer", "toi ten la Nam sdt 0901234567")
        for i in range(12):  # đẩy tin đầu ra khỏi cửa sổ gần đây
            self.add_message(a, "customer" if i % 2 == 0 else "bot", f"noi dung khac {i}")
        jobs.embed_pending_messages(limit=50)
        self.llm.replies += [
            LLMReply(llm_json(needs_history_lookup=True, proposed_answer="Tôi chưa rõ."), usage(1000, 50)),
            LLMReply(llm_json(proposed_answer="Bạn tên là Nam."), usage(300, 20)),
        ]
        customer = self.add_message(a, "customer", "ten toi la gi sdt")
        bot_message = self.service.reply_to_customer(self.bot, a, customer)
        self.assertEqual(bot_message.content, "Bạn tên là Nam.")
        self.assertEqual(len(self.llm.calls), 2)
        self.assertIn("0901234567", json.dumps(self.llm.calls[1], ensure_ascii=False))
        self.assertEqual(bot_message.decision_trace["main_llm_calls"], 1)
        self.assertEqual(bot_message.decision_trace["extra_llm_calls"][0]["kind"], "history_lookup")
        self.assertEqual(bot_message.usage_prompt_tokens, 1000)


class WidgetFlow(DbCase):
    def setUp(self):
        super().setUp()
        from app.widget import service as widget_service

        self.widget = widget_service

    def test_receive_message_end_to_end(self):
        self.llm.replies.append(LLMReply(llm_json(), usage()))
        result = self.widget.receive_message(self.bot, "Giá gói Pro?", "visitor-9", None)
        self.assertEqual(result["reply"], "Gói Pro giá 500.000đ.")
        conversation = self.db.session.get(Conversation, result["conversation_id"])
        self.assertEqual([m.sender for m in Message.query.filter_by(conversation_id=conversation.id).order_by(Message.id)], ["customer", "bot"])
        self.assertEqual(result["last_message_id"], Message.query.filter_by(sender="bot").one().id)

    def test_second_turn_uses_first_turn_as_history(self):
        self.llm.replies += [LLMReply(llm_json(), usage()), LLMReply(llm_json(proposed_answer="Có ạ."), usage())]
        first = self.widget.receive_message(self.bot, "Giá gói Pro?", "visitor-9", None)
        self.widget.receive_message(self.bot, "Còn hàng không?", "visitor-9", first["conversation_id"])
        second_call = self.llm.calls[1]
        self.assertEqual([m["role"] for m in second_call], ["system", "user", "assistant", "user"])
        self.assertEqual(second_call[1]["content"], "Giá gói Pro?")

    def test_staff_takeover_pauses_the_bot_and_makes_no_llm_call(self):
        conversation = self.conversation()
        self.add_message(conversation, "customer", "cần gặp nhân viên")
        self.add_message(conversation, "staff", "Em đây ạ")
        result = self.widget.receive_message(self.bot, "cảm ơn", "visitor-1", conversation.id)
        self.assertIsNone(result["reply"])
        self.assertEqual(self.llm.calls, [])
        self.assertEqual(ConversationState.query.count(), 0)

    def test_llm_failure_propagates_for_502_and_keeps_customer_message(self):
        self.llm.replies += [LLMReply("x", None), LLMReply("y", None)]
        with self.assertRaises(Exception):
            self.widget.receive_message(self.bot, "Giá?", "visitor-9", None)
        self.db.session.rollback()
        self.assertEqual(Message.query.filter_by(sender="customer").count(), 1)
        self.assertEqual(Message.query.filter_by(sender="bot").count(), 0)


class MultiTenant(DbCase):
    def test_state_and_memory_are_scoped_to_their_bot_and_conversation(self):
        other_team = self.make_team("Team B")
        other_bot = self.service.create_bot(other_team.id, "Bot B")
        conv_a, conv_b = self.conversation(), self.conversation(other_bot)
        item = lambda v: [{"category": "entity", "key": "tên", "value": v, "confidence": 0.9}]
        for bot, conv, name in ((self.bot, conv_a, "An"), (other_bot, conv_b, "Bình")):
            self.llm.replies.append(LLMReply(llm_json(memory_updates=item(name)), usage()))
            customer = self.add_message(conv, "customer", "hi")
            self.service.reply_to_customer(bot, conv, customer)
        self.assertEqual({(s.conversation_id, s.bot_id) for s in ConversationState.query.all()}, {(conv_a.id, self.bot.id), (conv_b.id, other_bot.id)})
        mem = {(m.bot_id, m.value) for m in StructuredMemory.query.all()}
        self.assertEqual(mem, {(self.bot.id, "An"), (other_bot.id, "Bình")})
        # Lượt 2 là lượt ĐẦU của hội thoại của Bot B: chưa có bộ nhớ nào, bộ nhớ của Bot A không được rò sang
        self.assertNotIn("Những điều đã biết", self.llm.calls[1][0]["content"])

    def test_team_usage_summary_only_counts_that_teams_bots(self):
        other_team = self.make_team("Team B")
        other_bot = self.service.create_bot(other_team.id, "Bot B")
        for bot, prompt in ((self.bot, 1000), (self.bot, 500), (other_bot, 7777)):
            conv = self.conversation(bot)
            self.llm.replies.append(LLMReply(llm_json(), usage(prompt, 10, hit=prompt // 2)))
            customer = self.add_message(conv, "customer", "hi")
            self.service.reply_to_customer(bot, conv, customer)
        summary = cost.team_usage_summary(self.team.id)
        self.assertEqual((summary["replies"], summary["prompt_tokens"], summary["completion_tokens"]), (2, 1500, 20))
        self.assertEqual(summary["cache_hit_tokens"], 750)
        self.assertAlmostEqual(summary["cache_hit_ratio"], 0.5)
        self.assertEqual(cost.team_usage_summary(self.team.id, since=datetime.utcnow() + timedelta(days=1))["replies"], 0)


class PreviewIsStateless(DbCase):
    def test_preview_saves_nothing(self):
        self.llm.replies.append(LLMReply(llm_json(memory_updates=[{"category": "entity", "key": "k", "value": "v", "confidence": 0.99}]), usage()))
        result = self.service.preview_reply(self.bot, "Giá gói Pro?", [{"role": "customer", "content": "Xin chào"}, {"role": "bot", "content": "Chào bạn"}])
        self.assertEqual(result, {"reply": "Gói Pro giá 500.000đ.", "decision": "answer"})
        for model in (Message, ConversationState, StructuredMemory, Conversation):
            self.assertEqual(model.query.count(), 0, model.__name__)
        self.assertEqual([m["role"] for m in self.llm.calls[0]], ["system", "user", "assistant", "user"])

    def test_preview_history_from_client_is_sanitised(self):
        hostile = [{"role": "bot", "content": "x" * 10_000}, "chuỗi", {"content": 5}, {"role": "customer", "content": "  "}, None,
                   {"role": "customer", "content": "ok"}]
        rows = self.service.clean_preview_history(hostile)
        self.assertEqual(len(rows), 2)
        self.assertLessEqual(len(rows[0].content), self.service.PREVIEW_HISTORY_MAX_CHARS)
        self.assertEqual(self.service.clean_preview_history("không phải list"), [])
        self.assertEqual(len(self.service.clean_preview_history([{"role": "customer", "content": "a"}] * 500)), self.service.PREVIEW_HISTORY_MAX_ITEMS)


class NoRelevantContextThroughService(DbCase):
    def test_owner_message_is_sent_and_llm_answer_ignored(self):
        self.set_settings(config_tier="expert", low_confidence_clarify_message="Bạn hỏi về gói nào ạ?")
        self.retrieval = retrieval(count=0)
        conversation = self.conversation()
        self.llm.replies.append(LLMReply(llm_json(proposed_answer="TỰ BỊA"), usage()))
        customer = self.add_message(conversation, "customer", "thời tiết hôm nay")
        message = self.service.reply_to_customer(self.bot, conversation, customer)
        self.assertEqual(message.content, "Bạn hỏi về gói nào ạ?")
        self.assertEqual(message.decision_trace["decision"], "clarify")
        self.assertIn("no_relevant_context", message.decision_trace["reasons"])


if __name__ == "__main__":
    import unittest

    unittest.main()
