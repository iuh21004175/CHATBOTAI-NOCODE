"""Hiển thị tin nhắn: danh sách sản phẩm/dịch vụ dạng box + bảng markdown (format_message) và quy tắc định dạng trong prompt.
Không cần DB. Quy tắc nhận diện phải giống hệt app/widget/embed.js và app/static/js/inbox.js (xem ghi chú ở service.py)."""
import unittest

from app.dashboard import service
from core.context_engine import prompts


def render(text: str) -> str:
    return str(service.format_message(text))


class ItemListTests(unittest.TestCase):
    def test_dash_separated_lines_become_boxes(self):
        html = render("Các gói hiện có:\n**Gói Basic** — 199.000đ/tháng\n**Gói Pro** — 499.000đ/tháng\n**Gói Max** — 999.000đ/tháng")
        self.assertEqual(html.count('class="msg-item"'), 3)
        self.assertEqual(html.count('class="msg-items"'), 1)
        self.assertIn('<div class="msg-item-name">Gói Basic</div>', html)
        self.assertIn('<div class="msg-item-desc">499.000đ/tháng</div>', html)
        self.assertTrue(html.startswith("Các gói hiện có:<div"))  # không dòng trống thừa giữa lời dẫn và box
        self.assertNotIn("<table", html)

    def test_bullets_numbering_and_blank_lines_between_items(self):
        html = render("- **A** — mô tả A\n\n- **B** - mô tả B\n\n1. **C** – mô tả C")
        self.assertEqual(html.count('class="msg-item"'), 3)

    def test_text_after_the_list_is_kept(self):
        html = render("**A** — a\n**B** — b\n\nBạn quan tâm gói nào?")
        self.assertEqual(html.count('class="msg-item"'), 2)
        self.assertTrue(html.endswith("Bạn quan tâm gói nào?"))

    def test_single_line_is_not_a_box(self):
        html = render("**Gói Pro** — 499.000đ/tháng")
        self.assertNotIn("msg-item", html)
        self.assertEqual(html, "<strong>Gói Pro</strong> — 499.000đ/tháng")

    def test_colon_pairs_are_not_boxes(self):
        html = render("**Địa chỉ**: 12 Nguyễn Huệ\n**Hotline**: 0900000000")
        self.assertNotIn("msg-item", html)

    def test_bold_label_with_inner_colon_is_not_a_box(self):
        self.assertNotIn("msg-item", render("**Lưu ý:** giá chưa gồm VAT\n**Ghi chú:** đặt trước 1 ngày"))

    def test_non_item_line_between_breaks_the_run(self):
        html = render("**A** — a\nvăn bản xen giữa\n**B** — b")
        self.assertNotIn("msg-item", html)

    def test_html_in_name_and_description_is_escaped(self):
        html = render("**<img src=x onerror=alert(1)>** — <script>alert(1)</script>\n**B** — b")
        self.assertNotIn("<script", html)
        self.assertNotIn("<img", html)
        self.assertIn("&lt;script&gt;", html)

    def test_inline_bold_and_code_in_description(self):
        html = render("**A** — giá **199k** mã `PRO`\n**B** — b")
        self.assertIn("<strong>199k</strong>", html)
        self.assertIn("<code>PRO</code>", html)

    def test_markdown_table_still_renders_as_table(self):
        html = render("| Gói | Giá |\n| --- | --- |\n| A | 1 |\n| B | 2 |")
        self.assertIn("<table>", html)
        self.assertNotIn("msg-item", html)

    def test_list_and_table_in_same_message(self):
        html = render("**A** — a\n**B** — b\n\n| X | Y |\n| --- | --- |\n| 1 | 2 |")
        self.assertEqual(html.count('class="msg-item"'), 2)
        self.assertIn("<table>", html)

    def test_plain_text_unchanged(self):
        self.assertEqual(render("Xin chào **bạn**"), "Xin chào <strong>bạn</strong>")
        self.assertEqual(render(""), "")
        self.assertEqual(render(None), "")


class PromptFormattingRuleTests(unittest.TestCase):
    def test_rules_tell_the_model_not_to_use_tables_for_product_lists(self):
        for language in prompts.SUPPORTED_LANGUAGES:
            rules = prompts.texts(language)["rules"]
            self.assertIn("**", rules, language)
            self.assertIn("|", rules, language)


class MessageSegmentsTests(unittest.TestCase):
    """Tin bot có danh sách sản phẩm -> nhiều tin riêng (Lịch sử chat); quy tắc giống splitSegments() ở widget/inbox."""

    def segs(self, text):
        return [str(x) for x in service.message_segments(text)]

    def test_intro_each_item_and_closing_become_separate_messages(self):
        out = self.segs("Bảng giá:\n**Gói Basic** — 199.000đ\n**Gói Pro** — 500.000đ\n**Gói Enterprise** — 1.500.000đ\n\nBạn muốn gói nào?")
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0], "Bảng giá:")
        self.assertIn('<div class="msg-item-name">Gói Pro</div><div class="msg-item-desc">500.000đ</div>', out[2])
        self.assertEqual(out[4], "Bạn muốn gói nào?")
        self.assertFalse(any('class="msg-items"' in part for part in out), "không còn khung danh sách chung")

    def test_no_list_or_single_item_stays_one_message_identical_to_format_message(self):
        for text in ("Xin chào **bạn**", "Chỉ 1 mục:\n**A** — a", "", None, "| a | b |\n|---|---|\n| 1 | 2 |"):
            self.assertEqual(self.segs(text), [str(service.format_message(text))], repr(text))

    def test_list_only_and_table_kept_in_its_text_segment(self):
        self.assertEqual(len(self.segs("**A** — a\n**B** — b")), 2)
        out = self.segs("| a | b |\n|---|---|\n| 1 | 2 |\n**A** — a\n**B** — b")
        self.assertEqual(len(out), 3)
        self.assertIn("<table>", out[0])

    def test_content_is_escaped(self):
        out = self.segs("**<script>x</script>** — <b>y</b>\n**B** — b")
        self.assertNotIn("<script>", "".join(out))
        self.assertNotIn("<b>y", "".join(out))


if __name__ == "__main__":
    unittest.main()
