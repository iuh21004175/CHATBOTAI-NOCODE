# Module "Đọc tài liệu"

Khách gửi tệp ngay trong khung chat widget; trợ lý đọc nội dung tệp (kể cả chữ trong ảnh / bản scan) và trả lời theo đó.

## Luồng

```
Chủ bot cài module "Đọc tài liệu" (Module → Khai báo module mới)            [app/modules]
        │  widget /config trả attachments.enabled = true
        ▼
Khách chọn tệp ─► POST /widget/api/<public_id>/attachments (multipart)      [app/widget/routes.py]
        │           kiểm: bot đã cài module, cỡ, đuôi, chữ ký nội dung, giới hạn số tệp   [app/attachments/service.py]
        │           lưu MinIO (team/bot/attachments/<id>.<đuôi>) + dòng message_attachments (status=processing)
        ▼ tác vụ nền
   md/txt/csv            ─► đọc trực tiếp, MIỄN PHÍ                         [core/doc_reader/text.py]
   pdf/docx/pptx/xlsx     ─► markitdown (trích text layer có sẵn), MIỄN PHÍ [core/doc_reader/markitdown_reader.py]
       │ PDF không có text layer thật (bản scan) ─► DeepSeek vision toàn bộ, TỐN AI Credit
   ảnh (.png/.jpg/...)    ─► DeepSeek vision, TỐN AI Credit                 [core/doc_reader/vision_reader.py]
        ▼
   nếu đã gọi vision: trừ AI Credit theo giá vốn thật (credit_transactions type 'attachment_vision_charge')
        ▼
   cắt chunk + embed ─► collection Chroma RIÊNG attach_<bot_id>              [core/attachment_rag.py]
        ▼ status=ready (widget hỏi GET .../attachments/<id> tới khi xong)
Khách gửi tin kèm attachment_ids ─► tin lưu "nội dung + 📎 tên tệp"          [app/widget/service.py]
        ▼ mỗi lượt trả lời của hội thoại đó
   engine tra cứu top-k đoạn tệp gần câu hỏi, đặt TRƯỚC tri thức của bot     [core/context_engine/engine.py]
   (qua đúng đường cắt ngân sách token/nén như mọi đoạn khác)
```

Tệp chỉ dùng trong hội thoại của khách gửi; **không** vào Cơ sở tri thức của bot.

## Lịch sử: MinerU → markitdown + DeepSeek vision

Trước đây module này dùng MinerU 4.0 (chạy như tiến trình con trong venv riêng `env-mineru/`, vì MinerU kéo theo `huggingface_hub 1.x`/`openai 2.x`
xung đột với môi trường chính). Đã đổi sang **markitdown** (Microsoft, MIT, cho PDF/Word/Excel/PowerPoint có chữ thật) + **DeepSeek vision**
(cho ảnh và PDF dạng bản scan), lý do:

- markitdown chỉ parse cấu trúc tệp (pdfminer/pdfplumber/python-pptx/openpyxl...), không dùng model ngôn ngữ nào, nên **không xung đột** với
  `huggingface_hub`/`openai` của môi trường chính — cài thẳng vào venv chính, không cần venv riêng nữa (đã kiểm chứng bằng `pip install --dry-run`
  và `pip check`, không có cảnh báo xung đột; embedding ONNX trong `core/rag_engine.py` vẫn chạy đúng sau khi cài).
- markitdown KHÔNG tự OCR — nó chỉ trích chữ CÓ SẴN trong tệp (text layer PDF, text run Word/Excel/PowerPoint). PDF dạng bản scan (ảnh chụp/quét,
  không có text layer) và ảnh (.png/.jpg/...) được xử lý bằng **chính model `deepseek-flash` đã dùng cho toàn hệ thống**
  (`core/llm_client.py`) — model này nhận ảnh trực tiếp qua `image_url` theo chuẩn OpenAI-compatible (đã xác minh với tài liệu chính thức
  https://api-docs.deepseek.com/guides/vision/: hỗ trợ JPEG/PNG/GIF/WebP, mỗi ảnh quy đổi tối đa ~1024 token). Lựa chọn này KHÔNG dùng OCR cục bộ
  (Tesseract...) hay dịch vụ ngoài khác (Azure Document Intelligence...): tận dụng đúng công nghệ/API đã có sẵn trong project, không thêm phần
  mềm hệ thống mới, không phát sinh bên thứ ba mới (ảnh vốn đã phải gửi ra DeepSeek để phân tích/trả lời giống mọi lệnh gọi khác của hệ thống).
- **Đánh đổi chi phí**: khác markitdown/text (miễn phí, chạy tại chỗ), MỖI lần gọi vision là 1 lệnh gọi DeepSeek THẬT — tốn AI Credit của team,
  trừ qua `credit_transactions` type `attachment_vision_charge`, ĐÚNG khuôn `module_analysis_charge` của Phase M (kiểm đủ Credit TRƯỚC lúc nhận
  tệp — `app/attachments/service.py:upload`, trừ giá vốn THẬT sau khi đọc xong — `core/credits/service.py:settle_attachment_vision`). PDF bản
  scan nhiều trang bị trần `VISION_MAX_PDF_PAGES` (mỗi trang = 1 lệnh gọi) để chặn chi phí/thời gian không giới hạn.

## Cài đặt markitdown (một lần, trên máy chủ — không cần venv riêng)

Cài từ mã nguồn (chưa có bản PyPI ổn định phù hợp tại thời điểm viết tài liệu này); dùng đúng venv chính của dự án, KHÔNG tạo venv mới:

```bash
git clone https://github.com/microsoft/markitdown.git
cd markitdown
<venv-chính>/Scripts/python -m pip install -e 'packages/markitdown[all]'   # Linux: <venv-chính>/bin/python
```

Thư mục `markitdown/` (bản sao mã nguồn, cài kiểu editable nên PHẢI giữ lại — không xoá sau khi cài) nằm ở gốc dự án và đã có trong `.gitignore`.
Nếu triển khai lên máy chủ mới, lặp lại đúng 2 bước trên (clone + `pip install -e`) trước khi chạy app.

Ảnh/PDF bản scan KHÔNG cần cài đặt gì thêm — dùng chung `DEEPSEEK_API_KEY` đã cấu hình cho toàn hệ thống (`core/llm_client.py`).

Lưu ý:

- Tệp `.md/.txt/.csv` không cần markitdown/vision (miễn phí).
- PDF/Word/Excel/PowerPoint có chữ thật (không phải bản scan) đọc được qua markitdown, miễn phí — không gọi DeepSeek vision, không tốn Credit.
- Ảnh và PDF bản scan LUÔN tốn AI Credit (xem mục trên); nếu team hết Credit, khách nhận lỗi rõ ràng ngay lúc tải tệp lên thay vì tốn tiền xong
  mới báo lỗi.

## Cấu hình (`.env`, xem `.env.example`)

`ATTACHMENT_MAX_BYTES`, `ATTACHMENT_MAX_PER_MESSAGE`, `ATTACHMENT_MAX_PER_CONVERSATION`, `ATTACHMENT_MAX_TEXT_CHARS`, `ATTACHMENT_TOP_K`,
`ATTACHMENT_STALE_SECONDS`, `VISION_MAX_TOKENS`, `VISION_IMAGE_TOKENS`, `VISION_MAX_PDF_PAGES`, `VISION_CONCURRENCY`.

## Bảo vệ

- Bot phải cài module; tệp thuộc đúng bot + đúng `visitor_id` (sai → 404, không tiết lộ tệp có tồn tại).
- Kiểm cỡ, đuôi VÀ chữ ký nội dung (không tin đuôi tên tệp); tên tệp của khách không dùng làm đường dẫn.
- Nội dung tệp là dữ liệu không tin cậy: chỉ được đưa vào prompt như ngữ cảnh tham khảo, có nhãn `[Tệp khách gửi: tên]`.
- Rate limit: 10 lượt tải lên / phút / IP.

## Giới hạn hiện tại

- Tệp và chunk của hội thoại chưa có cơ chế tự xoá theo thời gian (MinIO + Chroma `attach_<bot_id>`).
- Gỡ module khỏi bot thì tệp cũ không còn được dùng cho câu trả lời, nhưng dữ liệu vẫn nằm trong MinIO/Chroma.
