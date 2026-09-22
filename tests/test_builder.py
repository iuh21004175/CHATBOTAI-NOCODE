"""Phase 2 — Context Builder: chọn tin gần đây theo token, ngân sách, áp lực, nén đúng thứ tự, dựng message theo role.
Dùng tokenizer thật của model embedding (chỉ đọc tokenizer.json, không nạp model ONNX)."""
import unittest

from core import rag_engine
from core.context_engine import builder
from core.context_engine.builder import ChatTurn, ContextBudgetManager, ContextPlan, MessageBuilder, RecentMessageSelector, RecentRow
from core.context_engine.state import ConversationSnapshot, IntentSpec, MemoryItem
from tests.helpers import settings

SENTENCE = "khach hang can tu van ve goi dich vu "  # ~ 10 token


def rows(n, sender_cycle=("customer", "bot"), text=SENTENCE):
    return [RecentRow(i + 1, sender_cycle[i % len(sender_cycle)], f"{text}{i}") for i in range(n)]


def passage(doc, chunks, distance=0.5):
    return {
        "metadata": {"document_id": doc, "chunk_indexes": list(range(len(chunks)))},
        "chunks": [{"index": i, "content": t, "hit": hit} for i, (t, hit) in enumerate(chunks)],
        "content": "\n\n".join(t for t, _ in chunks),
        "distance": distance,
    }


def make_plan(*, recent=(), passages=(), summary="", memory="", system="Hãy trả lời ngắn gọn. " * 10, **overrides) -> ContextPlan:
    plan = ContextPlan(
        settings=settings(**overrides),
        system_static=system,
        summary_block=f"## Tóm tắt\n{summary}" if summary else "",
        memory_block=memory,
        recent=list(recent),
        passages=[dict(p, chunks=list(p["chunks"])) for p in passages],
        question="Giá gói Pro?",
    )
    counts = rag_engine.count_tokens_many([plan.system_static, plan.summary_block, plan.memory_block, builder.render_final_user(plan.question, "", plan.settings.language)])
    plan.system_tokens, plan.summary_tokens, plan.memory_tokens, plan.scaffold_tokens = counts
    plan.recount_rag()
    return plan


def turns(n, words=30):
    text = "noi dung tin nhan " * (words // 3)
    tokens = rag_engine.count_tokens_many([text])[0]
    return [ChatTurn("user" if i % 2 == 0 else "assistant", f"{text}{i}", tokens) for i in range(n)]


class RecentSelector(unittest.TestCase):
    def test_takes_at_most_limit_newest_messages_in_chronological_order(self):
        result = RecentMessageSelector(limit=4, token_limit=10_000).select(rows(12))
        self.assertEqual(len(result), 4)
        self.assertTrue(result[-1].content.endswith("11"))
        self.assertTrue(result[0].content.endswith("8"))

    def test_stops_early_when_token_limit_exceeded_keeping_newest(self):
        per = rag_engine.count_tokens_many([SENTENCE + "0"])[0]
        result = RecentMessageSelector(limit=10, token_limit=per * 3 + 2).select(rows(10))
        self.assertEqual(len(result), 3)
        self.assertTrue(result[-1].content.endswith("9"), "phần bị bỏ là tin CŨ, không phải tin mới")
        self.assertLessEqual(sum(t.tokens for t in result), per * 3 + 2)

    def test_roles(self):
        result = RecentMessageSelector(10, 10_000).select(rows(4))
        self.assertEqual([t.role for t in result], ["user", "assistant", "user", "assistant"])

    def test_consecutive_same_role_are_merged_with_ids_kept(self):
        data = [RecentRow(1, "customer", "a"), RecentRow(2, "customer", "b"), RecentRow(3, "bot", "c")]
        result = RecentMessageSelector(10, 10_000).select(data)
        self.assertEqual([(t.role, t.content, t.ids) for t in result], [("user", "a\nb", (1, 2)), ("assistant", "c", (3,))])

    def test_blank_messages_ignored(self):
        data = [RecentRow(1, "customer", "  "), RecentRow(2, "bot", "xin chao")]
        self.assertEqual([t.content for t in RecentMessageSelector(10, 10_000).select(data)], ["xin chao"])

    def test_a_single_message_over_the_limit_is_not_taken(self):
        self.assertEqual(RecentMessageSelector(10, 5).select([RecentRow(1, "bot", "tin nhan rat dai " * 20)]), [])

    def test_empty(self):
        self.assertEqual(RecentMessageSelector(10, 100).select([]), [])


class Budget(unittest.TestCase):
    def plan(self, **overrides):
        plan = make_plan(**overrides)
        plan.system_tokens, plan.memory_tokens, plan.summary_tokens, plan.scaffold_tokens = 100, 50, 200, 40
        plan.recent = [ChatTurn("user", "x", 300)]
        return plan

    def test_available_rag_tokens_formula(self):
        plan = self.plan(max_context_tokens=8000, max_tokens=500)
        self.assertEqual(ContextBudgetManager.output_reserve(plan.settings), 500 + 600)
        self.assertEqual(ContextBudgetManager.available_rag_tokens(plan), 8000 - 100 - 50 - 200 - 300 - 40 - 1100)

    def test_budget_is_capped_by_rag_max_context_tokens(self):
        plan = self.plan(max_context_tokens=8000, rag_max_context_tokens=3000)
        self.assertEqual(ContextBudgetManager.rag_budget(plan.settings, ContextBudgetManager.available_rag_tokens(plan)), 3000)

    def test_budget_shrinks_to_what_is_available(self):
        plan = self.plan(max_context_tokens=3000, rag_max_context_tokens=3000)
        available = ContextBudgetManager.available_rag_tokens(plan)
        self.assertEqual(ContextBudgetManager.rag_budget(plan.settings, available), available)
        self.assertLess(available, 3000)

    def test_budget_never_negative(self):
        plan = self.plan(max_context_tokens=800)
        self.assertLess(ContextBudgetManager.available_rag_tokens(plan), 0)
        self.assertEqual(ContextBudgetManager.rag_budget(plan.settings, ContextBudgetManager.available_rag_tokens(plan)), 0)

    def test_build_plan_fits_rag_into_budget(self):
        big = [passage(i, [("noi dung tai lieu tham khao " * 40, True)], 0.3 + i / 10) for i in range(6)]
        s = settings(max_context_tokens=3000, max_tokens=300, rag_max_context_tokens=1000)
        plan = builder.build_plan(s, [], ConversationSnapshot(), [], big, "Giá?")
        self.assertLessEqual(plan.rag_tokens, 1000 + 20)  # + phân cách giữa các đoạn
        self.assertGreaterEqual(len(plan.passages), 1)
        self.assertEqual(plan.passages[0]["metadata"]["document_id"], 0, "đoạn liên quan nhất được giữ")


class Pressure(unittest.TestCase):
    def test_levels(self):
        s = settings(context_pressure_warning=0.8, context_pressure_hard_limit=0.9)
        cases = [(0.0, "normal"), (0.59, "normal"), (0.60, "light"), (0.79, "light"), (0.80, "strong"), (0.90, "strong"), (0.91, "hard"), (3.0, "hard")]
        for pressure, expected in cases:
            self.assertEqual(builder.pressure_level(pressure, s), expected, pressure)

    def test_pressure_is_input_over_max_context(self):
        plan = make_plan(max_context_tokens=1000)
        plan.system_tokens, plan.summary_tokens, plan.memory_tokens, plan.scaffold_tokens, plan.rag_tokens = 100, 0, 0, 0, 400
        plan.recent = [ChatTurn("user", "x", 100)]
        self.assertAlmostEqual(plan.pressure, 0.6)


class CompressionSteps(unittest.TestCase):
    def test_step1_removes_repeated_paragraphs_from_chunk_overlap(self):
        overlap = "doan van bi lap lai do overlap"
        plan = make_plan(passages=[passage(1, [(f"mo dau\n\n{overlap}", True), (f"{overlap}\n\nphan tiep theo", True)])])
        before = plan.rag_tokens
        self.assertTrue(builder._dedupe_repeated_blocks(plan))
        plan.recount_rag()
        self.assertEqual(plan.passages[0]["content"].count(overlap), 1)
        self.assertLess(plan.rag_tokens, before)
        self.assertFalse(builder._dedupe_repeated_blocks(plan), "chạy lần 2 không còn gì để bỏ")

    def test_step2_drops_worst_passage_but_keeps_one(self):
        plan = make_plan(passages=[passage(i, [(f"nguon {i}", True)], 0.3 + i / 10) for i in range(3)])
        self.assertTrue(builder._drop_lowest_passage(plan))
        self.assertEqual([p["metadata"]["document_id"] for p in plan.passages], [0, 1])
        builder._drop_lowest_passage(plan)
        self.assertFalse(builder._drop_lowest_passage(plan))
        self.assertEqual([p["metadata"]["document_id"] for p in plan.passages], [0])

    def test_step3_keeps_only_hit_chunks(self):
        plan = make_plan(passages=[passage(1, [("lan can truoc", False), ("chunk trung", True), ("lan can sau", False)])])
        self.assertTrue(builder._compress_passage_content(plan))
        self.assertEqual([c["content"] for c in plan.passages[0]["chunks"]], ["chunk trung"])
        self.assertEqual(plan.passages[0]["content"], "chunk trung")

    def test_step4_drops_oldest_history_down_to_half_only(self):
        plan = make_plan(recent=turns(6))
        plan.recent_initial = 6
        oldest = plan.recent[0].content
        self.assertTrue(builder._reduce_history(plan))
        self.assertNotIn(oldest, [t.content for t in plan.recent])
        while builder._reduce_history(plan):
            pass
        self.assertEqual(len(plan.recent), 3, "chỉ giảm còn một nửa — phần còn lại chỉ bỏ khi có summary thay thế (bước 5)")

    def test_step4_never_goes_below_the_last_exchange(self):
        plan = make_plan(recent=turns(3))
        plan.recent_initial = 3
        while builder._reduce_history(plan):
            pass
        self.assertGreaterEqual(len(plan.recent), builder.MIN_RECENT_AFTER_COMPRESSION)

    def test_step5_only_when_a_summary_exists(self):
        without = make_plan(recent=turns(8))
        self.assertFalse(builder._use_summary_instead_of_history(without))
        self.assertEqual(len(without.recent), 8, "không có summary thì không bỏ lịch sử thô")
        with_summary = make_plan(recent=turns(8), summary="khach can 3 may tinh")
        self.assertTrue(builder._use_summary_instead_of_history(with_summary))
        self.assertEqual(len(with_summary.recent), builder.MIN_RECENT_AFTER_COMPRESSION)


class CompressPipeline(unittest.TestCase):
    ORDER = ["dedupe_repeated_blocks", "drop_low_score_chunks", "compress_chunk_content", "reduce_history_messages", "use_summary_instead_of_recent"]

    def heavy_plan(self, max_context, summary=""):
        rag = [
            passage(i, [("phan lan can " * 30, False), (f"noi dung trung {i} " * 30, True), ("phan lan can sau " * 30, False)], 0.3 + i / 10)
            for i in range(4)
        ]
        plan = make_plan(recent=turns(10, 300), passages=rag, summary=summary, max_context_tokens=max_context,
                         context_pressure_warning=0.8, context_pressure_hard_limit=0.9)
        return plan

    def test_normal_level_does_nothing(self):
        plan = self.heavy_plan(100_000)
        self.assertEqual(builder.compress(plan, "normal"), [])

    def test_light_level_touches_only_rag_content_not_history(self):
        plan = self.heavy_plan(100_000)
        plan.settings = settings(max_context_tokens=int(plan.input_tokens / 0.7))  # áp lực ~0,7
        history = len(plan.recent)
        applied = builder.compress(plan, "light")
        self.assertTrue(applied)
        self.assertTrue(set(applied) <= set(self.ORDER[:3]))
        self.assertEqual(len(plan.recent), history)
        self.assertLess(plan.pressure, builder.LIGHT_PRESSURE + 0.05)

    def test_strong_level_applies_steps_in_spec_order_and_lowers_pressure(self):
        plan = self.heavy_plan(100_000)
        plan.settings = settings(max_context_tokens=int(plan.input_tokens / 0.95))
        before = plan.pressure
        applied = builder.compress(plan, builder.pressure_level(before, plan.settings))
        self.assertEqual(applied, [s for s in self.ORDER if s in applied], "đúng thứ tự ưu tiên của đặc tả")
        self.assertLess(plan.pressure, before)
        self.assertEqual(plan.compression_steps, applied)

    def test_history_is_reduced_only_after_rag_steps_are_exhausted(self):
        plan = self.heavy_plan(100_000)
        plan.settings = settings(max_context_tokens=int(plan.input_tokens / 1.6))  # áp lực rất cao
        applied = builder.compress(plan, "hard")
        self.assertIn("reduce_history_messages", applied)
        self.assertEqual(len(plan.passages), 1)
        self.assertTrue(all(c["hit"] for p in plan.passages for c in p["chunks"]))
        self.assertGreaterEqual(len(plan.recent), 5, "không có summary: lịch sử chỉ giảm còn một nửa")

    def test_summary_replaces_raw_history_only_at_the_end(self):
        plan = self.heavy_plan(100_000, summary="khach can 3 may tinh ngan sach 30 trieu")
        plan.settings = settings(max_context_tokens=int(plan.input_tokens / 12))  # cực cao: dùng hết mọi bước
        applied = builder.compress(plan, "hard")
        self.assertEqual(applied, self.ORDER)
        self.assertEqual(len(plan.recent), builder.MIN_RECENT_AFTER_COMPRESSION)

    def test_without_summary_step5_never_runs(self):
        plan = self.heavy_plan(100_000)
        plan.settings = settings(max_context_tokens=int(plan.input_tokens / 12))
        self.assertNotIn("use_summary_instead_of_recent", builder.compress(plan, "hard"))

    def test_stops_as_soon_as_below_target(self):
        plan = self.heavy_plan(100_000)
        plan.settings = settings(max_context_tokens=int(plan.input_tokens / 0.85))  # chỉ vượt cảnh báo 1 chút
        applied = builder.compress(plan, "strong")
        self.assertNotIn("use_summary_instead_of_recent", applied)
        self.assertLess(plan.pressure, plan.settings.context_pressure_warning)


class EnsureRagRoom(unittest.TestCase):
    def test_history_is_dropped_to_make_room_for_documents(self):
        docs = [passage(1, [("thong tin quan trong " * 20, True)])]
        plan = builder.build_plan(settings(max_context_tokens=3000, max_tokens=500), [], ConversationSnapshot(), turns(10, 300), docs, "Giá?")
        self.assertEqual(plan.passages, [], "trước khi nhường chỗ: lịch sử ăn hết ngân sách nên không có tài liệu nào")
        self.assertTrue(builder.ensure_rag_room(plan, docs))
        self.assertEqual(len(plan.passages), 1)
        self.assertLess(len(plan.recent), 10)
        self.assertIn("reduce_history_for_rag", plan.compression_steps)

    def test_no_op_when_documents_already_fit(self):
        docs = [passage(1, [("thong tin " * 5, True)])]
        plan = builder.build_plan(settings(), [], ConversationSnapshot(), turns(3), docs, "Giá?")
        self.assertTrue(builder.ensure_rag_room(plan, docs))
        self.assertEqual(len(plan.recent), 3)
        self.assertEqual(plan.compression_steps, [])

    def test_gives_up_when_even_minimal_history_leaves_no_room(self):
        docs = [passage(1, [("thong tin " * 5, True)])]
        plan = builder.build_plan(settings(max_context_tokens=700, max_tokens=500), [], ConversationSnapshot(), turns(6, 90), docs, "Giá?")
        self.assertFalse(builder.ensure_rag_room(plan, docs))
        self.assertEqual(len(plan.recent), builder.MIN_RECENT_AFTER_COMPRESSION)

    def test_nothing_retrieved_is_not_a_failure_to_fit(self):
        plan = builder.build_plan(settings(), [], ConversationSnapshot(), [], [], "Giá?")
        self.assertFalse(builder.ensure_rag_room(plan, []))


class ScopeNarrowingPlan(unittest.TestCase):
    """Chunk tìm được vượt ngân sách RAG -> chế độ trích phần mở đầu của từng chunk."""

    @staticmethod
    def big(n=6):
        return [passage(i, [(f"# Muc {i}\n" +f"noi dung chi tiet cua muc {i} " * 80, True)], 0.3 + i / 20) for i in range(n)]

    def plan(self, passages, allow=True, **overrides):
        s = settings(max_context_tokens=8000, rag_max_context_tokens=1000, **overrides)
        return builder.build_plan(s, [], ConversationSnapshot(), [], passages, "Giá?", allow_scope_narrowing=allow)

    def test_overflow_shows_every_chunk_within_budget(self):
        plan = self.plan(self.big())
        self.assertTrue(plan.scope_narrowing)
        self.assertEqual(len(plan.passages), 6)
        self.assertLessEqual(plan.rag_tokens, 1000 + 20)
        self.assertEqual(plan.overflow_info["found_chunks"], 6)
        self.assertGreater(plan.overflow_info["found_tokens"], plan.overflow_info["budget"])
        self.assertTrue(all(p["chunks"][0]["truncated"] for p in plan.passages))
        for i, p in enumerate(plan.passages):
            self.assertTrue(p["content"].startswith(f"# Muc {i}"), "phần MỞ ĐẦU của chunk (gồm tiêu đề mục) được giữ")

    def test_final_message_carries_the_narrowing_instruction(self):
        plan = self.plan(self.big())
        last = MessageBuilder.build(plan)[-1]["content"]
        self.assertIn("LƯU Ý", last)
        self.assertLess(last.index("Câu hỏi của khách"), last.index("LƯU Ý"))

    def test_fits_the_budget_is_untouched(self):
        plan = self.plan([passage(1, [("ngan", True)]), passage(2, [("cung ngan", True)])])
        self.assertFalse(plan.scope_narrowing)
        self.assertIsNone(plan.overflow_info)
        self.assertNotIn("LƯU Ý", MessageBuilder.build(plan)[-1]["content"])

    def test_not_allowed_falls_back_to_best_chunks_that_fit(self):
        plan = self.plan(self.big(), allow=False)
        self.assertFalse(plan.scope_narrowing)
        self.assertLess(len(plan.passages), 6)
        self.assertEqual(plan.passages[0]["metadata"]["document_id"], 0)
        self.assertNotIn("LƯU Ý", MessageBuilder.build(plan)[-1]["content"])

    def test_neighbour_chunks_do_not_count_towards_overflow(self):
        # 1 chunk trúng nhỏ + 2 chunk lân cận lớn: phần lân cận luôn bỏ được nên không phải "quá nhiều nội dung tìm được"
        p = passage(1, [("lan can lon " * 300, False), ("chunk trung nho", True), ("lan can lon " * 300, False)])
        plan = self.plan([p])
        self.assertFalse(plan.scope_narrowing)

    def test_no_budget_means_no_passages_and_no_narrowing(self):
        plan = builder.build_plan(settings(max_context_tokens=800, max_tokens=500), [], ConversationSnapshot(), [], self.big(), "Giá?", allow_scope_narrowing=True)
        self.assertEqual(plan.passages, [])
        self.assertFalse(plan.scope_narrowing)

    def test_ensure_rag_room_never_switches_to_narrowing(self):
        plan = builder.build_plan(settings(max_context_tokens=3000, max_tokens=500), [], ConversationSnapshot(), turns(10, 300), self.big(2), "Giá?")
        builder.ensure_rag_room(plan, self.big(2))
        self.assertFalse(plan.scope_narrowing)


class Messages(unittest.TestCase):
    def build(self, **overrides):
        # structured_memory_enabled cố định tắt trong sản phẩm (FIXED_TOGGLES); bật lại ở đây để kiểm tra riêng cơ chế
        # dựng khối bộ nhớ vào prompt vẫn đúng (vẫn còn trong code, chỉ không được gọi qua luồng trả lời thật nữa).
        s = settings(instructions="Bạn là trợ lý của cửa hàng An Phát.", structured_memory_enabled=True, **overrides)
        snap = ConversationSnapshot(summary="Khách cần 3 máy tính.", memory=[MemoryItem("constraint", "ngân sách", "30 triệu", 0.9)])
        recent = RecentMessageSelector(10, 2000).select(rows(4))
        plan = builder.build_plan(s, [IntentSpec("ask_price", "Hỏi giá", ("product",), ())], snap, recent,
                                  [passage(1, [("Gói Pro giá 500.000đ", True)])], "Giá gói Pro?")
        return plan, MessageBuilder.build(plan)

    def test_roles_and_order(self):
        _, messages = self.build()
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user", "assistant", "user"])

    def test_system_contains_everything_static_then_dynamic(self):
        _, messages = self.build()
        system = messages[0]["content"]
        self.assertTrue(system.startswith("Bạn là trợ lý của cửa hàng An Phát."))
        for needle in ("json", "ask_price", "Tóm tắt hội thoại", "Khách cần 3 máy tính.", "[constraint] ngân sách: 30 triệu"):
            self.assertIn(needle, system)
        self.assertLess(system.index("json"), system.index("Khách cần 3 máy tính."), "phần tĩnh đứng trước phần động")

    def test_last_user_message_has_rag_then_question_and_no_string_concat_of_history(self):
        _, messages = self.build()
        last = messages[-1]["content"]
        self.assertLess(last.index("Gói Pro giá 500.000đ"), last.index("Giá gói Pro?"))
        self.assertNotIn("Hội thoại trước đó", last)
        self.assertNotIn(SENTENCE, last)

    def test_static_prefix_is_identical_across_turns_for_prompt_caching(self):
        s = settings(instructions="Bạn là trợ lý.")
        static = builder.render_system_static(s, [])
        for summary in ("", "Tóm tắt A", "Tóm tắt B khác hẳn"):
            plan = make_plan(system=static, summary=summary)
            self.assertTrue(MessageBuilder.build(plan)[0]["content"].startswith(static))

    def test_language_labels(self):
        _, en = self.build(language="en")
        self.assertIn("Reference information:", en[-1]["content"])
        self.assertIn("Always reply in English", en[-1]["content"])

    def test_no_context_marker(self):
        plan = make_plan()
        self.assertIn("Không tìm thấy thông tin liên quan", MessageBuilder.build(plan)[-1]["content"])

    def test_history_context_section_added_on_lookup(self):
        plan = make_plan()
        content = MessageBuilder.build(plan, history_context="- Khách: tôi tên Nam")[-1]["content"]
        self.assertIn("tôi tên Nam", content)
        self.assertIn("tìm lại được", content)

    def test_no_memory_or_summary_blocks_when_absent(self):
        plan = make_plan()
        system = MessageBuilder.build(plan)[0]["content"]
        self.assertNotIn("Tóm tắt hội thoại trước đó", system)
        self.assertNotIn("Những điều đã biết", system)


if __name__ == "__main__":
    unittest.main()
