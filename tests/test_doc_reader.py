"""Module "Đọc tài liệu" — phần THUẦN (không DB, không MinIO, không gọi DeepSeek thật): định dạng/chữ ký tệp, markitdown (PDF/Word/Excel/
PowerPoint, chỉ trích chữ có sẵn — không OCR), quyết định khi nào chuyển PDF bản scan sang DeepSeek vision, và ghi nhận usage vào tracker (để
app/attachments/service.py trừ AI Credit) khi vision thực sự được gọi. Đây là kiểm tra hồi quy ở mức mã — KHÔNG thay cho kiểm thử chức năng
thực tế (cần DEEPSEEK_API_KEY thật, xem docs/DOCUMENT_READER.md)."""
import io
import unittest
from unittest import mock

import openpyxl

from core.context_engine.cost import LLMUsageTracker
from core.context_engine.structured import LLMReply
from core.doc_reader import extract, formats, markitdown_reader, vision_reader
from core.doc_reader.errors import ReadError

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000a4944415408d763f8ffff3f0005fe02fea1399e3f0000000049454e44ae426082"
)


def xlsx_bytes(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def vision_reply(text: str) -> LLMReply:
    return LLMReply(content=text, token_usage={"prompt_tokens": 1024, "completion_tokens": 50})


class FormatRouting(unittest.TestCase):
    def test_text_extensions_are_lightweight_and_everything_else_is_heavy(self):
        for ext in (".txt", ".md", ".markdown", ".csv"):
            self.assertFalse(formats.needs_heavy_processing("a" + ext), ext)
        for ext in (".pdf", ".docx", ".pptx", ".xlsx", ".png", ".jpg", ".tif", ".tiff"):
            self.assertTrue(formats.needs_heavy_processing("a" + ext), ext)

    def test_document_vs_image_classification(self):
        for ext in (".pdf", ".docx", ".pptx", ".xlsx"):
            self.assertTrue(formats.is_document("a" + ext), ext)
            self.assertFalse(formats.is_image("a" + ext), ext)
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"):
            self.assertTrue(formats.is_image("a" + ext), ext)
            self.assertFalse(formats.is_document("a" + ext), ext)

    def test_unsupported_extension_is_rejected(self):
        self.assertFalse(formats.is_supported("a.exe"))
        self.assertFalse(formats.is_supported("a.doc"))  # định dạng .doc cũ (không phải .docx) chưa hỗ trợ

    def test_signature_check_catches_a_renamed_file(self):
        self.assertFalse(formats.has_valid_signature("fake.pdf", b"this is actually a text file"))
        self.assertTrue(formats.has_valid_signature("real.pdf", b"%PDF-1.4 ..."))
        self.assertFalse(formats.has_valid_signature("fake.png", b"\xff\xd8\xffnot a png"))

    def test_text_files_are_rejected_if_they_contain_a_null_byte(self):
        self.assertFalse(formats.has_valid_signature("a.txt", b"binary\x00garbage"))
        self.assertTrue(formats.has_valid_signature("a.txt", "nội dung bình thường".encode()))


class ExtractText(unittest.TestCase):
    def test_plain_text_is_read_directly_without_markitdown_or_vision(self):
        result = extract.extract_text("ghi_chu.txt", "Xin chào thế giới".encode("utf-8"))
        self.assertEqual((result.method, result.text, result.truncated), ("text", "Xin chào thế giới", False))

    def test_an_xlsx_with_real_content_is_read_by_markitdown_as_a_table_without_calling_vision(self):
        raw = xlsx_bytes([["Sản phẩm", "Giá"], ["Áo thun", 199000]])
        with mock.patch.object(vision_reader, "_call") as fake_call:
            result = extract.extract_text("bang_gia.xlsx", raw)
        fake_call.assert_not_called()
        self.assertEqual(result.method, "markitdown")
        self.assertIn("Áo thun", result.text)
        self.assertIn("199000", result.text)

    def test_an_image_is_read_via_deepseek_vision_and_usage_is_recorded_on_the_tracker(self):
        tracker = LLMUsageTracker()
        with mock.patch.object(vision_reader, "_call", return_value=vision_reply("Khuyến mãi 50%")):
            result = extract.extract_text("khuyen_mai.png", PNG_1X1, tracker=tracker)
        self.assertEqual((result.method, result.text), ("vision", "Khuyến mãi 50%"))
        self.assertEqual(len(tracker.calls), 1, "phải ghi nhận usage để app/attachments/service.py trừ đúng AI Credit")

    def test_unsupported_format_is_rejected_with_a_clear_reason(self):
        with self.assertRaises(ReadError):
            extract.extract_text("virus.exe", b"MZ...")

    def test_renamed_file_fails_the_signature_check(self):
        with self.assertRaises(ReadError):
            extract.extract_text("fake.pdf", b"not actually a pdf")

    def test_empty_content_after_extraction_is_an_error_not_a_silent_empty_result(self):
        raw = xlsx_bytes([[]])  # sheet rỗng -> markitdown trả gần như không có gì
        empty = markitdown_reader.DocumentResult(text="", used_vision=False)
        with mock.patch.object(markitdown_reader, "read_document", return_value=empty):
            with self.assertRaises(ReadError):
                extract.extract_text("rong.xlsx", raw)

    def test_long_text_is_truncated_and_flagged(self):
        huge = "a" * 10
        with mock.patch("core.doc_reader.extract.Config") as cfg:
            cfg.ATTACHMENT_MAX_TEXT_CHARS = 5
            result = extract.extract_text("ghi_chu.txt", huge.encode())
        self.assertEqual((result.text, result.truncated), ("aaaaa", True))


class MarkitdownVisionFallback(unittest.TestCase):
    """markitdown_reader.read_document quyết định khi nào 1 PDF được coi là "bản scan" (không có text layer thật) và cần chuyển sang DeepSeek
    vision — đây là ranh giới quan trọng nhất của việc thay MinerU (vốn OCR được) bằng markitdown (không tự OCR) + vision (tốn Credit)."""

    def convert_returning(self, text: str):
        class FakeResult:
            text_content = text

        return mock.patch.object(markitdown_reader._converter, "convert_stream", return_value=FakeResult())

    def test_a_pdf_with_a_real_even_if_short_text_layer_is_not_sent_to_vision(self):
        tracker = LLMUsageTracker()
        with self.convert_returning("Hoá đơn số 001"):
            with mock.patch.object(vision_reader, "read_scanned_pdf") as fake_vision:
                doc = markitdown_reader.read_document("hoadon.pdf", b"%PDF-1.4 ...", tracker=tracker)
        fake_vision.assert_not_called()
        self.assertEqual((doc.text, doc.used_vision, tracker.calls), ("Hoá đơn số 001", False, []))

    def test_a_pdf_with_no_extractable_text_is_sent_to_vision(self):
        tracker = LLMUsageTracker()
        with self.convert_returning(""):
            with mock.patch.object(vision_reader, "read_scanned_pdf", return_value="chữ nhận ra từ vision") as fake_vision:
                doc = markitdown_reader.read_document("ban_scan.pdf", b"%PDF-1.4 ...", tracker=tracker)
        fake_vision.assert_called_once_with(b"%PDF-1.4 ...", tracker=tracker)
        self.assertEqual((doc.text, doc.used_vision), ("chữ nhận ra từ vision", True))

    def test_a_non_pdf_document_with_no_text_is_an_error_not_a_vision_attempt(self):
        # .docx/.xlsx/.pptx không có khái niệm "bản scan" (không phải ảnh) -> rỗng là lỗi thật, không chuyển sang vision (không tốn Credit oan)
        tracker = LLMUsageTracker()
        with self.convert_returning(""):
            with mock.patch.object(vision_reader, "read_scanned_pdf") as fake_vision:
                with self.assertRaises(ReadError):
                    markitdown_reader.read_document("rong.xlsx", b"PK\x03\x04...", tracker=tracker)
        fake_vision.assert_not_called()

    def test_a_conversion_crash_is_reported_as_a_read_error_not_an_unhandled_exception(self):
        with mock.patch.object(markitdown_reader._converter, "convert_stream", side_effect=RuntimeError("hỏng")):
            with self.assertRaises(ReadError):
                markitdown_reader.read_document("hong.docx", b"PK\x03\x04...", tracker=LLMUsageTracker())

    def test_extract_text_tags_a_scanned_pdf_as_vision_not_markitdown(self):
        """extract.py suy ra method từ việc tracker có thêm lệnh gọi hay không — kiểm tra đúng ở mức tích hợp (không mock nội bộ markitdown_reader)."""
        tracker = LLMUsageTracker()
        with self.convert_returning(""):
            with mock.patch.object(vision_reader, "read_scanned_pdf", return_value="chữ từ bản scan"):
                result = extract.extract_text("ban_scan.pdf", b"%PDF-1.4 ...", tracker=tracker)
        self.assertEqual((result.method, result.text), ("vision", "chữ từ bản scan"))


class VisionReader(unittest.TestCase):
    def test_read_image_wraps_a_corrupt_file_as_a_read_error_not_a_crash(self):
        with self.assertRaises(ReadError):
            vision_reader.read_image(b"not an image at all", tracker=LLMUsageTracker())

    def test_a_deepseek_call_failure_is_reported_as_a_read_error(self):
        with mock.patch.object(vision_reader, "_call", side_effect=RuntimeError("mạng đứt")):
            with self.assertRaises(ReadError):
                vision_reader.read_image(PNG_1X1, tracker=LLMUsageTracker())

    def test_estimate_scales_with_call_count_and_markup(self):
        from decimal import Decimal
        from unittest import mock as _mock

        from config import Config

        one = vision_reader.estimate_vision_vnd(1)
        five = vision_reader.estimate_vision_vnd(5)
        self.assertGreater(five, one)
        with _mock.patch.object(Config, "PLATFORM_MARKUP_MULTIPLIER", Decimal("2.0")):
            self.assertEqual(vision_reader.estimate_vision_vnd(1), (one * 2).quantize(Decimal("0.0001")))


if __name__ == "__main__":
    unittest.main()
