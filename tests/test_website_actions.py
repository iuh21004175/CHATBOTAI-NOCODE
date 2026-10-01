"""Website Action Engine (Phase M) — phần THUẦN (không DB, không mạng): DOM nhẹ + bộ khớp selector tập con, cấu trúc selector_spec, phân loại rủi ro
(sàn cứng không bị AI hạ), giải nén .zip an toàn (zip bomb/slip/allowlist/số file), tự đoán domain từ HTML, phân tích LLM (fake) + kiểm chứng selector."""
import io
import json
import unittest
import zipfile

from core.context_engine.cost import LLMUsageTracker
from core.context_engine.structured import LLMReply
from core.website_actions import analysis, dom, domain_detect, risk, spec, zip_extract

PRODUCT_HTML = """<!doctype html><html><head><title>Áo thun nam</title>
<script>var secret = 'x'; document.write('<button id="fake">');</script><style>.x{color:red}</style></head>
<body><header><a id="cart-link" class="nav cart" href="/gio-hang">Giỏ hàng</a></header>
<main><h1 class="product-title">Áo thun nam</h1><span id="price" class="price">199.000đ</span>
<form id="add-form" action="/cart/add" method="post">
  <input type="number" id="qty" name="quantity" value="1">
  <button type="submit" id="add-btn" class="btn add-to-cart">Thêm vào giỏ</button>
</form>
<form id="contact-form" action="/lien-he"><input name="fullname" id="fullname"><input name="phone" id="phone"><button id="send">Gửi</button></form>
<form id="pay-form" action="/checkout/pay"><input type="text" name="card_number" id="cc"><input type="password" name="pwd"><button class="btn" id="pay-btn">Thanh toán</button></form>
</main></body></html>"""


def parsed():
    return dom.parse(PRODUCT_HTML)


class DomParsingAndReduction(unittest.TestCase):
    def test_script_and_style_content_never_reach_the_tree(self):
        root = parsed()
        self.assertFalse(dom.select(root, "#fake"))
        self.assertNotIn("secret", dom.reduce(root, 10_000))
        self.assertNotIn("color:red", dom.reduce(root, 10_000))

    def test_reduction_keeps_the_addresses_the_widget_needs(self):
        text = dom.reduce(parsed(), 10_000)
        for needle in ('id="add-btn"', 'class="btn add-to-cart"', 'name="quantity"', 'action="/cart/add"', 'href="/gio-hang"', "Thêm vào giỏ", "<title>Áo thun nam"):
            self.assertIn(needle, text)

    def test_over_budget_falls_back_to_interactive_elements_only_and_never_exceeds_the_limit(self):
        text = dom.reduce(parsed(), 700)
        self.assertLessEqual(len(text), 700 + len("\n<!-- đã cắt bớt vì quá dài -->") + 40)
        self.assertIn('id="add-btn"', text)
        self.assertNotIn("product-title", text, "chế độ rút gọn chỉ còn phần tử tương tác")

    def test_unclosed_and_stray_tags_do_not_break_parsing(self):
        root = dom.parse("<div id=a><p>một<p>hai</div></span><ul><li id=x>1<li id=y>2</ul><br/><input id=i>")
        for selector in ("#a", "#x", "#y", "#i"):
            self.assertEqual(len(dom.select(root, selector)), 1, selector)

    def test_empty_and_garbage_input(self):
        self.assertEqual(dom.select(dom.parse(""), "div"), [])
        self.assertEqual(dom.select(dom.parse("<<<>>> &&&"), "div"), [])


class SelectorSubset(unittest.TestCase):
    def test_supported_forms_match_real_elements(self):
        root = parsed()
        cases = {
            "#add-btn": 1, "button.add-to-cart": 1, ".btn": 2, "form#add-form > button": 1, "#add-form button": 1, "input[name=quantity]": 1,
            'input[name="card_number"]': 1, "a[href^='/gio']": 1, "input[type=password]": 1, "[id$=-btn]": 2, "[class*=add-to]": 1, "main input": 5, "form input": 5,
        }
        for selector, count in cases.items():
            with self.subTest(selector=selector):
                self.assertEqual(len(dom.select(root, selector)), count)

    def test_non_existing_selectors_match_nothing(self):
        root = parsed()
        for selector in ("#nope", "button.nope", "form#add-form > input.nope", "#add-btn #add-btn", "section button"):
            with self.subTest(selector=selector):
                self.assertEqual(dom.select(root, selector), [])

    def test_child_combinator_is_strict_but_descendant_is_not(self):
        root = parsed()
        self.assertEqual(len(dom.select(root, "main > button")), 0)
        self.assertEqual(len(dom.select(root, "main button")), 3)

    def test_unsupported_syntax_is_rejected_not_silently_ignored(self):
        for selector in ("li:nth-child(2)", "a:not(.x)", "a, b", "h1 + p", "h1 ~ p", "a::before", "", "   ", "a >", "> a", "x" * 400, "a[href=", "1abc"):
            with self.subTest(selector=selector):
                with self.assertRaises(dom.SelectorError):
                    dom.select(parsed(), selector)


class SpecNormalization(unittest.TestCase):
    HOST = "shop.vn"

    def test_each_action_type_keeps_only_whitelisted_keys(self):
        out = spec.normalize("click", {"selector": "#add-btn", "event": "hover", "evil": "x", "fields": [{"selector": "#qty", "value_from_slot": "so_luong", "junk": 1}]}, page_host=self.HOST)
        self.assertEqual(out, {"selector": "#add-btn", "event": "click", "fields": [{"selector": "#qty", "value_from_slot": "so_luong"}]})
        self.assertEqual(spec.normalize("fill_form", {"form_selector": "#f", "fields": [{"selector": "#a", "value_from_slot": "ho_ten"}]}, page_host=self.HOST)["submit"], False,
                         "mặc định CHỈ điền, không gửi form")
        self.assertEqual(spec.normalize("read_info", {"selector": "#price"}, page_host=self.HOST), {"selector": "#price", "attribute": "text"})

    def test_navigate_must_stay_on_the_same_site(self):
        for relative in ("/gio-hang", "gio-hang.html", "../gio-hang", "?trang=2", "#top"):
            self.assertEqual(spec.normalize("navigate", {"href": relative}, page_host=self.HOST), {"href": relative}, relative)
        self.assertEqual(spec.normalize("navigate", {"href": "https://www.shop.vn/x"}, page_host=self.HOST)["href"], "https://www.shop.vn/x")
        self.assertEqual(spec.normalize("navigate", {"href": "https://m.shop.vn/x"}, page_host=self.HOST)["href"], "https://m.shop.vn/x")
        for bad in ("https://evil.com/x", "//evil.com/x", "javascript:alert(1)", "https://shop.vn.evil.com/", "data:text/html,x", "ftp://shop.vn/x", "", "vbscript:x", "gio-hang.html:80"):
            with self.subTest(href=bad):
                with self.assertRaises(spec.SpecError):
                    spec.normalize("navigate", {"href": bad}, page_host=self.HOST)

    def test_malformed_specs_are_rejected(self):
        bad = [
            ("click", {}), ("click", {"selector": "a:hover"}), ("click", None), ("click", []), ("fill_form", {"form_selector": "#f"}),
            ("fill_form", {"form_selector": "#f", "fields": [{"selector": "#a", "value_from_slot": "Ho Ten"}]}),
            ("fill_form", {"form_selector": "#f", "fields": [{"selector": "#a"}]}), ("read_info", {"selector": "#p", "attribute": "onclick"}),
            ("read_info", {"selector": "#p", "attribute": "a b"}), ("teleport", {"selector": "#a"}),
            ("fill_form", {"form_selector": "#f", "fields": [{"selector": "#a", "value_from_slot": "x"}] * 13}),
        ]
        for action_type, value in bad:
            with self.subTest(action_type=action_type, value=value):
                with self.assertRaises(spec.SpecError):
                    spec.normalize(action_type, value, page_host=self.HOST)

    def test_selectors_and_params_are_listed_for_verification(self):
        normalized = spec.normalize("fill_form", {"form_selector": "#f", "fields": [{"selector": "#a", "value_from_slot": "ho_ten"}, {"selector": "#b", "value_from_slot": "ho_ten"},
                                                                              {"selector": "#c", "value_from_slot": "sdt"}], "submit_selector": "#go"}, page_host=self.HOST)
        self.assertEqual(sorted(spec.selectors_of(normalized)), ["#a", "#b", "#c", "#f", "#go"])
        self.assertEqual(spec.params_of(normalized), ["ho_ten", "sdt"])

    def test_missing_selectors_are_reported_against_the_real_dom(self):
        good = spec.normalize("click", {"selector": "#add-btn"}, page_host=self.HOST)
        bad = spec.normalize("click", {"selector": "#khong-co"}, page_host=self.HOST)
        self.assertEqual(spec.missing_selectors(parsed(), good), [])
        self.assertEqual(spec.missing_selectors(parsed(), bad), ["#khong-co"])


class RiskFloor(unittest.TestCase):
    def floor(self, action_type, raw, **kw):
        return risk.floor_risk(action_type, spec.normalize(action_type, raw, page_host="shop.vn"), root=parsed(), **kw)[0]

    def test_plain_reading_and_contact_form_are_read_only(self):
        self.assertEqual(self.floor("read_info", {"selector": "#price"}), "read_only")
        self.assertEqual(self.floor("fill_form", {"form_selector": "#contact-form", "fields": [{"selector": "#fullname", "value_from_slot": "ho_ten"}]}), "read_only")

    def test_add_to_cart_is_at_least_cart_even_if_the_ai_said_read_only(self):
        self.assertEqual(self.floor("add_to_cart", {"selector": "#add-btn"}), "cart")
        self.assertEqual(risk.effective_risk("read_only", "add_to_cart", {"selector": "#add-btn", "event": "click"}, root=parsed()), "cart")

    def test_cart_wording_raises_a_plain_click_to_cart(self):
        self.assertEqual(self.floor("click", {"selector": "button.add-to-cart"}), "cart")
        self.assertEqual(self.floor("navigate", {"href": "/gio-hang"}), "cart")

    def test_password_or_card_fields_or_payment_words_force_payment(self):
        self.assertEqual(self.floor("fill_form", {"form_selector": "#pay-form", "fields": [{"selector": "#cc", "value_from_slot": "so_the"}]}), "payment")
        self.assertEqual(self.floor("click", {"selector": "#pay-btn"}), "payment", "chữ 'Thanh toán' của nút + tên form/URL")
        self.assertEqual(self.floor("navigate", {"href": "/checkout"}), "payment")
        # form chứa trường mật khẩu: chỉ cần chọn form (không nêu trường) cũng bị nâng — quét cả phần tử con
        self.assertEqual(self.floor("fill_form", {"form_selector": "#pay-form", "fields": [{"selector": "#cc", "value_from_slot": "x"}]}), "payment")

    def test_the_page_role_sets_a_floor_too(self):
        self.assertEqual(self.floor("read_info", {"selector": "#price"}, url_role="checkout"), "payment")
        self.assertEqual(self.floor("read_info", {"selector": "#price"}, url_role="cart"), "cart")

    def test_vietnamese_words_are_matched_with_and_without_accents(self):
        for raw in ("#add-btn",):
            spec_click = spec.normalize("click", {"selector": raw}, page_host="shop.vn")
            self.assertEqual(risk.floor_risk("click", spec_click, url="https://shop.vn/dat-hang")[0], "payment", "URL 'dat-hang' không dấu")
        self.assertEqual(risk.fold("Đặt hàng"), "dat hang")
        self.assertEqual(risk.fold("Thêm vào giỏ"), "them vao gio")

    def test_the_effective_level_is_never_lower_than_the_floor_and_odd_ai_values_are_not_trusted(self):
        self.assertEqual(risk.max_level("read_only", "payment"), "payment")
        self.assertEqual(risk.max_level("payment", "read_only"), "payment")
        self.assertEqual(risk.rank("garbage"), 1, "giá trị lạ -> coi là 'cart', không phải 'read_only'")

    def test_approval_thresholds_per_level(self):
        self.assertIsNone(risk.approval_check("read_only", 0.6, allow_payment=False))
        self.assertIsNotNone(risk.approval_check("read_only", 0.59, allow_payment=False))
        self.assertIsNone(risk.approval_check("cart", 0.8, allow_payment=False))
        self.assertIsNotNone(risk.approval_check("cart", 0.79, allow_payment=False))
        self.assertIsNotNone(risk.approval_check("payment", 1.0, allow_payment=False), "thiếu công tắc thì confidence cao mấy cũng không duyệt được")
        self.assertIsNone(risk.approval_check("payment", 0.95, allow_payment=True))
        self.assertIsNotNone(risk.approval_check("payment", 0.85, allow_payment=True))
        self.assertIsNotNone(risk.approval_check("read_only", None, allow_payment=False))


def make_zip(entries: dict) -> bytes:
    """entries: {tên trong zip: nội dung (str hoặc bytes)}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content.encode("utf-8") if isinstance(content, str) else content)
    return buf.getvalue()


SAVED_PAGE_HTML = '<html><head><title>Áo thun nam</title></head><body><a href="/gio-hang">Giỏ hàng</a></body></html>'


class ZipExtraction(unittest.TestCase):
    def extract(self, raw, max_files=500, max_uncompressed_bytes=200 * 1024 * 1024):
        return zip_extract.extract_entry_html(raw, max_files=max_files, max_uncompressed_bytes=max_uncompressed_bytes)

    def test_a_valid_webpage_complete_zip_is_extracted(self):
        raw = make_zip({"index.html": SAVED_PAGE_HTML, "index_files/style.css": "body{}", "index_files/logo.png": b"\x89PNG"})
        page = self.extract(raw)
        self.assertEqual(page.entry_filename, "index.html")
        self.assertEqual(page.html, SAVED_PAGE_HTML)
        self.assertEqual(page.skipped, [])

    def test_files_outside_the_allowlist_are_skipped_not_fatal(self):
        raw = make_zip({"index.html": SAVED_PAGE_HTML, "Thumbs.db": b"junk", "index_files/readme.txt": "x"})
        page = self.extract(raw)
        self.assertEqual(page.entry_filename, "index.html")
        self.assertEqual(sorted(page.skipped), ["Thumbs.db", "index_files/readme.txt"])

    def test_zero_or_multiple_root_html_files_are_rejected_not_guessed(self):
        with self.assertRaises(zip_extract.ZipExtractError):
            self.extract(make_zip({"index_files/style.css": "body{}"}))
        with self.assertRaises(zip_extract.ZipExtractError):
            self.extract(make_zip({"a.html": "<html>a</html>", "b.htm": "<html>b</html>"}))
        # .html nằm trong thư mục con (không phải cấp gốc) không được tính
        with self.assertRaises(zip_extract.ZipExtractError):
            self.extract(make_zip({"sub/a.html": "<html>a</html>"}))

    def test_path_traversal_entries_are_rejected(self):
        for bad_name in ("../outside.html", "..\\outside.html", "/etc/passwd.html", "a/../../outside.html"):
            with self.subTest(name=bad_name):
                raw = make_zip({"index.html": SAVED_PAGE_HTML, bad_name: "<html>evil</html>"})
                with self.assertRaises(zip_extract.ZipExtractError):
                    self.extract(raw)

    def test_too_many_files_is_rejected(self):
        raw = make_zip({"index.html": SAVED_PAGE_HTML, **{f"index_files/{i}.png": b"x" for i in range(10)}})
        with self.assertRaises(zip_extract.ZipExtractError):
            self.extract(raw, max_files=5)

    def test_uncompressed_size_over_the_cap_is_rejected_before_writing_anything(self):
        raw = make_zip({"index.html": SAVED_PAGE_HTML, "index_files/big.png": b"x" * 5000})
        with self.assertRaises(zip_extract.ZipExtractError):
            self.extract(raw, max_uncompressed_bytes=1000)

    def test_not_a_zip_file_is_rejected(self):
        with self.assertRaises(zip_extract.ZipExtractError):
            self.extract(b"khong phai file zip")

    def test_no_stray_temp_directory_is_left_behind_after_success_or_failure(self):
        import pathlib
        import tempfile

        before = set(pathlib.Path(tempfile.gettempdir()).glob("module-zip-*"))
        self.extract(make_zip({"index.html": SAVED_PAGE_HTML}))
        try:
            self.extract(make_zip({"a.html": "x", "b.html": "y"}))
        except zip_extract.ZipExtractError:
            pass
        after = set(pathlib.Path(tempfile.gettempdir()).glob("module-zip-*"))
        self.assertEqual(before, after)


class DomainDetection(unittest.TestCase):
    def test_base_href_wins_over_everything_else(self):
        html = ('<html><head><base href="https://base.vn/"><link rel="canonical" href="https://canon.vn/x">'
                '<meta property="og:url" content="https://og.vn/x"></head></html>')
        self.assertEqual(domain_detect.detect(html), "base.vn")

    def test_canonical_link_is_used_when_there_is_no_base(self):
        html = '<html><head><link rel="canonical" href="https://www.canon.vn/x"><meta property="og:url" content="https://og.vn/x"></head></html>'
        self.assertEqual(domain_detect.detect(html), "canon.vn", "bỏ tiền tố www.")

    def test_og_url_is_used_when_there_is_no_base_or_canonical(self):
        html = '<html><head><meta property="og:url" content="https://og.vn/san-pham"></head></html>'
        self.assertEqual(domain_detect.detect(html), "og.vn")

    def test_falls_back_to_the_most_common_host_among_absolute_links(self):
        html = ('<html><body><a href="https://shop.vn/a">a</a><a href="https://shop.vn/b">b</a>'
                '<img src="https://cdn.shop.vn/logo.png"><a href="/relative">r</a></body></html>')
        self.assertEqual(domain_detect.detect(html), "shop.vn")

    def test_relative_and_protocol_only_links_are_not_counted_as_a_signal(self):
        html = '<html><body><a href="/gio-hang">giỏ</a><a href="san-pham.html">sp</a></body></html>'
        self.assertIsNone(domain_detect.detect(html))

    def test_nothing_found_returns_none_without_crashing(self):
        self.assertIsNone(domain_detect.detect(""))
        self.assertIsNone(domain_detect.detect("<<<>>> not html at all"))


def reply(actions):
    return LLMReply(content=json.dumps({"actions": actions}, ensure_ascii=False), token_usage={"prompt_tokens": 1000, "completion_tokens": 200})


ADD_CART = {"action_type": "add_to_cart", "action_name": "Thêm giỏ hàng", "description": "Dùng khi khách muốn thêm sản phẩm vào giỏ.",
            "selector_spec": {"selector": "#add-btn", "fields": [{"selector": "#qty", "value_from_slot": "so_luong"}]}, "confidence": 0.9, "risk_level": "read_only"}


def analyze(actions, call=None, **kw):
    tracker = LLMUsageTracker()
    page = analysis.analyze_page(
        PRODUCT_HTML, url="https://shop.vn/ao", url_role="product_detail", url_role_label="URL trang sản phẩm", module_type_name="Hỗ trợ bán hàng",
        page_host="shop.vn", call=call or (lambda messages: reply(actions)), tracker=tracker, max_chars=20_000, **kw,
    )
    return page, tracker


class PageAnalysis(unittest.TestCase):
    def test_valid_action_is_kept_slugged_and_its_risk_is_floored(self):
        page, tracker = analyze([ADD_CART])
        self.assertEqual(page.dropped, [])
        candidate = page.candidates[0]
        self.assertEqual(candidate.action_name, "them_gio_hang")
        self.assertEqual(candidate.risk_level, "cart", "AI nói read_only nhưng là add_to_cart -> sàn cứng nâng lên cart")
        self.assertEqual(candidate.ai_risk_level, "read_only")
        self.assertEqual(len(tracker.calls), 1)

    def test_hallucinated_selectors_and_bad_specs_are_dropped_with_a_reason_not_stored(self):
        bad_selector = {**ADD_CART, "action_name": "ma", "selector_spec": {"selector": "#khong-ton-tai"}}
        bad_syntax = {**ADD_CART, "action_name": "cu_phap", "selector_spec": {"selector": "li:nth-child(2)"}}
        offsite = {"action_type": "navigate", "action_name": "di_dau_do", "description": "x", "selector_spec": {"href": "https://evil.com"}, "confidence": 1}
        no_desc = {**ADD_CART, "action_name": "khong_mo_ta", "description": ""}
        junk = "not a dict"
        page, _ = analyze([bad_selector, bad_syntax, offsite, no_desc, junk, ADD_CART])
        self.assertEqual([c.action_name for c in page.candidates], ["them_gio_hang"])
        self.assertEqual(len(page.dropped), 5)
        self.assertTrue(any("selector không khớp" in d for d in page.dropped))

    def test_payment_actions_are_floored_to_payment_whatever_the_ai_says(self):
        pay = {"action_type": "click", "action_name": "thanh_toan", "description": "Bấm thanh toán", "selector_spec": {"selector": "#pay-btn"}, "confidence": 0.99, "risk_level": "read_only"}
        page, _ = analyze([pay])
        self.assertEqual(page.candidates[0].risk_level, "payment")

    def test_unknown_or_missing_ai_risk_and_bad_confidence_are_handled(self):
        odd = {**ADD_CART, "action_name": "a", "action_type": "read_info", "selector_spec": {"selector": "#price"}, "risk_level": "banana", "confidence": "high"}
        page, _ = analyze([odd])
        candidate = page.candidates[0]
        self.assertEqual((candidate.confidence, candidate.risk_level), (0.0, "cart"))
        over = {**odd, "action_name": "b", "confidence": 7}
        self.assertEqual(analyze([over])[0].candidates[0].confidence, 1.0)

    def test_duplicate_reserved_and_odd_names_are_made_unique_and_safe(self):
        one = {**ADD_CART, "action_name": "decline"}
        two = {**ADD_CART, "action_name": "Thêm giỏ hàng"}
        three = {**ADD_CART, "action_name": "them gio hang"}
        names = [c.action_name for c in analyze([one, two, three])[0].candidates]
        self.assertEqual(names, ["decline_action", "them_gio_hang", "them_gio_hang_2"])
        self.assertEqual(len(set(names)), 3)

    def test_json_in_code_fence_is_accepted_and_bad_json_is_retried_once(self):
        calls = iter([LLMReply("khong phai json", {"prompt_tokens": 10, "completion_tokens": 1}), LLMReply("```json\n" + json.dumps({"actions": [ADD_CART]}) + "\n```", {"prompt_tokens": 20, "completion_tokens": 5})])
        page, tracker = analyze(None, call=lambda messages: next(calls))
        self.assertEqual(len(page.candidates), 1)
        self.assertEqual([c["kind"] for c in tracker.calls], ["module_analysis", "module_analysis_retry"], "usage của MỌI lần gọi đều được ghi")

    def test_json_that_stays_invalid_raises_instead_of_inventing_actions(self):
        for content in ("", "nope", "[]", '{"actions": "x"}', '{"khac": []}'):
            with self.subTest(content=content):
                with self.assertRaises(analysis.AnalysisError):
                    analyze(None, call=lambda messages, c=content: LLMReply(c, None))

    def test_empty_action_list_is_a_valid_result(self):
        page, _ = analyze([])
        self.assertEqual((page.candidates, page.dropped), ([], []))

    def test_the_prompt_carries_the_addresses_and_the_rules_but_no_script_content(self):
        seen = []
        analyze([], call=lambda messages: (seen.append(messages), reply([]))[1])
        system, user = seen[0][0]["content"], seen[0][1]["content"]
        self.assertIn("json", system.lower())
        self.assertIn("nth-child", system, "prompt cấm rõ cú pháp ngoài tập con")
        self.assertIn('id="add-btn"', user)
        self.assertNotIn("secret", user)

    def test_action_count_is_capped(self):
        many = [{**ADD_CART, "action_name": f"a{i}"} for i in range(analysis.MAX_ACTIONS_PER_PAGE + 10)]
        self.assertEqual(len(analyze(many)[0].candidates), analysis.MAX_ACTIONS_PER_PAGE)


if __name__ == "__main__":
    unittest.main()
