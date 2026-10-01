# Website Action Engine (Phase M)

Cho phép mỗi bot khai báo các trang trên website thật của khách bằng cách **tải lên file .zip** của trang đã lưu ("Webpage, Complete") — không còn để
server tự crawl URL (M2 vá, xem lịch sử bên dưới); hệ thống giải nén + phân tích (nền) để rút ra các **hành động** agent có thể thực hiện thay khách
(điều hướng, thêm giỏ, điền form, đọc giá...). Hành động đã được chủ bot **duyệt** trở thành công cụ của Agent Runtime; **thực thi thật xảy ra ở trình duyệt
của khách** (widget), lệnh được đẩy xuống qua Socket.IO.

## Luồng

```
Chủ bot: /bots/<id>/modules  --chọn loại module + tải lên .zip mỗi trang-->  bot_modules(pending) + module_urls(upload_status='uploaded')
   worker nền (workers/module_analysis.py)  --giải nén an toàn (core/website_actions/zip_extract.py)--> HTML
      --tự đoán domain (core/website_actions/domain_detect.py, chỉ để GỢI Ý)--> module_urls.detected_domain
      --rút gọn--> DeepSeek --> đề xuất
   code kiểm chứng: cấu trúc spec, selector CÓ THẬT trong trang, rủi ro = max(AI, sàn cứng)  --> module_actions(verified=false)
   trừ AI Credit thật (credit_transactions type 'module_analysis_charge')
Chủ bot XEM/SỬA rồi XÁC NHẬN domain từng trang (bắt buộc)  --> module_urls.source_url, upload_status='domain_confirmed'
   mọi url BẮT BUỘC đã xác nhận  --> bot_modules chuyển 'ready' (trước đó dừng ở 'awaiting_domain_confirmation')
Chủ bot duyệt từng hành động (ngưỡng theo rủi ro; thanh toán cần công tắc; trang phải đã xác nhận domain)  --> verified=true

Khách chat trên website: widget mở Socket.IO /widget (auth: public_id + visitor_id, Origin phải thuộc bot_domains)
   agent (dsh) gọi công cụ hành động --MCP--> POST /internal/actions/dispatch (loopback + token ký theo bot+lượt)
   Flask kiểm lại MỌI điều kiện, ghi pending_widget_actions, đẩy 'widget_action' vào room của khách, CHỜ kết quả
   widget thực thi trên DOM --> POST /widget/api/<public_id>/actions/<id>/result --> Redis --> lượt agent nhận kết quả THẬT
```

## Các quyết định thiết kế (và vì sao)

| Điểm | Quyết định |
| --- | --- |
| Khai báo trang | Chủ bot TẢI LÊN file .zip của trang đã lưu ("Webpage, Complete") thay vì khai URL để server tự crawl (M2 vá) — tránh hẳn bề mặt SSRF của việc server tự fetch URL do khách hàng nhập; đổi lại chủ bot phải tự lưu trang đúng cách (đủ nội dung JS đã tải xong TẠI THỜI ĐIỂM lưu, vì hệ thống không tự chạy JavaScript). |
| Bảo mật file .zip | Thư viện chuẩn: `zipfile`/`tempfile`/`pathlib` (không thêm dependency) — `core/website_actions/zip_extract.py`. 4 lớp bắt buộc: (1) chống zip bomb — tổng kích thước SAU giải nén đọc từ `ZipInfo.file_size` (central directory, không cần giải nén) kiểm TRƯỚC khi ghi; (2) chống zip slip — mọi đường dẫn đích `resolve()` và phải nằm trong đúng thư mục tạm; (3) allowlist phần mở rộng (html/htm/css/js/ảnh/font) — file khác bị bỏ qua, không fail cả zip; (4) trần số file. Đúng 1 file .html/.htm ở CẤP GỐC mới được coi là hợp lệ, không tự đoán file nào là chính. |
| Domain của trang | Không còn biết "miễn phí" từ URL như bản crawl cũ. Worker tự ĐOÁN (`core/website_actions/domain_detect.py`, thứ tự `<base href>` → `<link rel=canonical>` → `<meta og:url>` → domain phổ biến nhất trong `<a>`/`<img>`) và ghi vào `module_urls.detected_domain` — CHỈ để gợi ý. Chủ bot XEM/SỬA rồi xác nhận, ghi vào `module_urls.source_url`; chỉ `source_url` được dùng để so khớp bảo mật Origin (M3) và để lọc hành động dùng được (`app/modules/service.py:usable_actions`). |
| Selector | Tập con CSS (tag, #id, .class, [attr], `>` và khoảng trắng) — prompt chỉ cho LLM dùng tập này; mọi selector phải khớp ≥1 phần tử trong trang đã tải lên, nếu không hành động bị loại (không lưu action "ma"). |
| Rủi ro | `risk_level` = max(AI, `risk.floor_risk`). Sàn cứng đọc selector_spec + phần tử thật (input password, trường thẻ/CVV, từ khoá thanh toán/giỏ, vai trò URL). Tính lại lúc phân tích, lúc duyệt và lúc giao cho agent. |
| Ngưỡng duyệt | read_only ≥ 0.6, cart ≥ 0.8 (theo đặc tả), payment ≥ 0.9 + công tắc `allow_agent_payment_actions` (mặc định TẮT). Không duyệt tự động, không duyệt hàng loạt. |
| Thanh toán | Ngoài công tắc + duyệt riêng, **khách luôn phải bấm Đồng ý** trong khung chat; agent chỉ nhận `awaiting_confirmation`, không bao giờ "đã xong" khi khách chưa xác nhận. |
| Kênh widget | Namespace `/widget` (không đụng namespace mặc định của khung quản trị). Room theo `(public_id, visitor_id)` thay vì conversation_id vì lượt chat ĐẦU TIÊN chưa có hội thoại lúc widget cần kết nối. |
| Kết quả | HTTP POST thường (không phụ thuộc socket còn sống); mỗi lệnh có `token` riêng, chỉ ghi được 1 lần; sai bất kỳ điều kiện nào đều trả 404 giống nhau. |
| Agent chờ | Tool chặn tối đa `AGENT_ACTION_WAIT_SECONDS` để nhận kết quả thật. Hết hạn → `timeout` (agent được dặn KHÔNG khẳng định đã xong); kết quả đến muộn vẫn ghi vào DB. Trần thời gian của lượt được cộng thêm thời gian chờ này khi bot có hành động. |
| Đăng ký tool | Dùng đúng máy chủ MCP `kb` của Phase C (giao thức nội bộ của dsh, không thêm gì mới): danh sách hành động đi qua biến môi trường `KB_ACTIONS` và **thuộc `process_key`** — đổi danh sách (duyệt/bỏ duyệt) ⇒ tiến trình dsh mới. |
| Selector hết hạn | Widget báo `element_not_found` ⇒ hành động bị bỏ duyệt + hiện cảnh báo trên trang kết quả. Không tự đoán selector mới, không tự crawl lại (chỉ khi chủ bot bấm "Phân tích lại"). |
| Chi phí | Phân tích chạy nền (không có `agent_executions`) nên trừ Credit theo `module_analysis_charge`; chỉ kiểm số dư ≥ ước tính cao nhất (không giữ chỗ — tránh khoản giữ chỗ mồ côi nếu worker chết), số dư không bao giờ âm. Mỗi vòng agent gọi hành động là chi phí LLM của chính lượt đó (đã có ở `execution_costs`). |

## Loại module

`module_types` + `module_type_url_roles` (`app/modules/service.py:MODULE_TYPE_SEEDS`, tự chèn bằng `ensure_module_types()` — thêm loại mới = thêm 1
dòng ở đây, KHÔNG cần migration/đổi schema). Đúng **1 loại** hiện có: `sales_support` — "Hỗ trợ bán hàng".

Nhãn URL của loại này **trung tính**, cố ý không phân biệt "sản phẩm" hay "dịch vụ" bằng loại module, để phủ đủ 3 kịch bản thực tế bằng CÙNG 1 loại:

| # | Kịch bản | Khai báo |
| --- | --- | --- |
| 1 | Web bán sản phẩm, có thanh toán trực tuyến trên website | Khai đủ cả 4 URL (sản phẩm bắt buộc; danh sách/giỏ hàng/thanh toán tuỳ chọn). |
| 2 | Web dịch vụ, có thanh toán/đặt cọc trực tuyến trên website | Khai "URL trang sản phẩm / dịch vụ" = trang dịch vụ chính; "giỏ hàng / đặt lịch" và "thanh toán / đặt cọc" = trang tương ứng của luồng đặt cọc. |
| 3 | Web bán hàng và/hoặc dịch vụ, KHÔNG thanh toán trên website (chỉ liên hệ/đặt lịch) | Chỉ khai URL bắt buộc (trang sản phẩm/dịch vụ); bỏ trống 2 ô giỏ hàng/thanh toán — đã tuỳ chọn sẵn. |

4 role (`product_listing`/`product_detail`/`cart`/`checkout`) là enum CỐ ĐỊNH của `module_urls.url_role` (`app/models.py:MODULE_URL_ROLES`). Lý do
không tách role riêng cho dịch vụ (`booking`...) hay tách thêm loại module thứ hai (đã thử rồi gộp lại): `core/website_actions/risk.py:floor_risk`
khoá cứng theo đúng 2 chuỗi `"cart"`/`"checkout"` để nâng SÀN rủi ro (trang giỏ hàng/đặt lịch, trang thanh toán/đặt cọc luôn tối thiểu ở mức đó, bất
kể AI phân loại gì) — tách thêm sẽ cần migration đổi enum lẫn dạy lại `floor_risk` mà không có lợi ích hành vi nào khác; đổi loại module thứ hai chỉ
đổi NHÃN hiển thị, còn gây rối vì chủ bot phải tự chọn đúng loại theo sản phẩm/dịch vụ trong khi bản chất kỹ thuật (role, rủi ro, hành động) giống hệt
nhau. AI vẫn tự đọc HTML thật của trang khai báo để đề xuất đúng hành động có thật (`fill_form`, `click`, `read_info`, `navigate`...); `add_to_cart`
chỉ được đề xuất khi trang thực sự có nút kiểu giỏ hàng.

Migration `e2f5a7c9b1d3` gộp loại `service_support` (thử nghiệm ở một bản trước) trở lại `sales_support`: module đã khai báo theo loại đó được
CHUYỂN sang `sales_support` (an toàn — 2 loại dùng chung đúng 4 role key, dữ liệu `module_urls`/`module_actions` giữ nguyên), rồi xoá loại đó.

## Giới hạn hiện tại

- Không thực thi được trong chat thử (Bước 1) — không có DOM thật; công cụ vẫn được quảng bá (cùng tiến trình dsh) nhưng trả lời "không dùng được".
- Kết quả thực thi của hành động thanh toán (sau khi khách bấm Đồng ý) chỉ ghi vào `pending_widget_actions`, chưa đưa ngược vào lượt chat kế tiếp của agent.
- Chưa có UI đổi `out_of_credit_message`/các ngưỡng; ngưỡng đặt ở `core/website_actions/risk.py:CONFIDENCE_MIN`.
- "Phân tích lại" dùng lại ĐÚNG file .zip đã tải lên (không có ô tải file mới) — hữu ích khi lỗi là do sự cố tạm thời (LLM/mạng); nếu file .zip
  chính nó có vấn đề (sai trang, thiếu file), chủ bot phải xoá module và khai báo lại. "Phân tích lại" cũng đưa MỌI url về `upload_status='uploaded'`
  (kể cả url đã xác nhận domain) nên phải xác nhận domain lại sau mỗi lần phân tích lại.
- Nội dung CSS/JS/ảnh/font hợp lệ trong .zip chỉ được kiểm tra (đúng allowlist, không zip bomb/slip) khi giải nén, KHÔNG được lưu lại riêng — chỉ
  file .zip GỐC được giữ (`module_urls.upload_storage_key`); nếu sau này cần xem lại nguyên trạng trang đã lưu (vd tính năng xem trước trong trình
  duyệt) thì giải nén lại từ file .zip gốc đó, không cần đổi schema.
