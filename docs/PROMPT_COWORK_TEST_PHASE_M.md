# TEST PROCEDURE PROMPT — Phase M: Website Action Engine

```text
TÊN TÍNH NĂNG / BUG:
Phase M — Website Action Engine: khai báo module theo loại "Hỗ trợ bán hàng", crawl + phân tích nền, duyệt hành động, agent thực hiện hành động thật
trên website của khách qua widget (Socket.IO), ngưỡng an toàn + xác nhận thanh toán, trừ AI Credit cho phần phân tích.

MỤC TIÊU KIỂM THỬ:
Xác nhận toàn bộ luồng: khai báo → worker phân tích nền (thấy tiến độ realtime) → chủ bot duyệt từng hành động → khách chat trên website thật và widget
thực thi ĐÚNG trên DOM; hành động rủi ro cao bị chặn/cần xác nhận; lỗi (selector hỏng, URL lỗi, hết Credit, sai domain) được báo trung thực, agent KHÔNG
bao giờ nói "đã xong" khi widget chưa báo thành công; các chức năng cũ không bị ảnh hưởng.

ĐIỀU KIỆN TIÊN QUYẾT:
1. Dev DB `aichatbot` đang ở revision 1a2b3c4d5e09 → chạy `flask --app run.py db upgrade` (thêm 6 bảng, cột bot_settings.allow_agent_payment_actions,
   giá trị enum credit_transactions.type='module_analysis_charge'; chỉ THÊM). Kiểm tra `flask --app run.py db current` = 1a2b3c4d5e10 và bảng
   module_types có 1 dòng key='sales_support' cùng 4 dòng module_type_url_roles.
2. .env: AGENT_ENABLED=true; DEEPSEEK_API_KEY hợp lệ; Redis, MinIO, ChromaDB đang chạy; MODULE_ALLOW_PRIVATE_HOSTS=true (CHỈ để crawl trang thử ở localhost;
   Case 4 sẽ tắt lại). Chạy `python run.py` (worker phân tích module chạy nhúng, log có dòng "[module-worker] sẵn sàng").
3. Trang cửa hàng thử: chạy `python -m http.server 8081 --directory tests/manual/shop` (trong thư mục dự án). Có 3 trang: san-pham.html (giá 199.000đ, form thêm giỏ
   #add-form/#add-btn, form liên hệ #contact-form), gio-hang.html, thanh-toan.html (form #checkout-form, nút #place-order — KHÔNG thanh toán thật).
4. Team thử có 2 tài khoản: A (Owner) và B (Member); team thứ hai C (để thử cách ly). Số dư AI Credit của team ≥ 5.000đ (mặc định trial 10.000đ).
5. Tạo bot "Bot Shop". Bước 2: tải 1 tài liệu .txt nội dung (KHÔNG ghi giá): "Áo thun nam cổ tròn, chất liệu cotton 100%, nhiều màu. Chính sách đổi trả trong 7 ngày.
   Khách có thể đặt mua, thêm áo thun vào giỏ hàng và thanh toán trên website. Giá xem trên website." → đợi trạng thái "Đã huấn luyện".
   (Cây quyết định vẫn chạy trên đầu ra của agent: câu hỏi phải liên quan tài liệu, nếu không sẽ bị từ chối theo cấu hình chủ bot — không phải lỗi Phase M.)
6. Bước 3 (Xuất bản): thêm domain `localhost`; ghi lại public_id của bot. Trang thử mở bằng: http://localhost:8081/san-pham.html?bot=<public_id>
   (mở DevTools → tab Console và Network để quan sát).
7. Công cụ xem DB (phpMyAdmin): bot_modules, module_urls, module_actions, pending_widget_actions, credit_transactions, agent_executions, bot_settings.

CASE 1 — ORIGINAL (luồng chính: khai báo → phân tích → duyệt → khách chat → widget thực thi)
Bước thực hiện:
1. Đăng nhập tài khoản A. Vào bot "Bot Shop" → mục điều hướng "Module hành động" (trên thanh đầu trang). Ghi lại số dư AI Credit ở /profile.
2. Ở "Khai báo module mới": thấy thẻ loại "Hỗ trợ bán hàng" kèm mô tả. Chưa chọn loại thì KHÔNG có ô URL nào. Chọn loại đó → hiện đúng 4 ô:
   "URL trang sản phẩm" (dấu * bắt buộc), "URL trang danh sách sản phẩm", "URL trang giỏ hàng", "URL trang thanh toán" (3 ô này ghi "(tuỳ chọn)").
3. Nhập: trang sản phẩm = http://localhost:8081/san-pham.html ; giỏ hàng = http://localhost:8081/gio-hang.html ; bỏ trống 2 ô còn lại. Tên: "Cửa hàng thử". Bấm "Khai báo và phân tích".
4. Ngay sau đó (trang chi tiết module): quan sát thanh tiến độ và nhãn trạng thái KHÔNG tải lại trang. Song song xem DB:
   SELECT id,status,progress_done,progress_total,error_message,analysis_cost_vnd,analysis_charged_vnd FROM bot_modules ORDER BY id DESC LIMIT 1;
5. Khi xong: đọc danh sách "Hành động phát hiện được"; xem DB: SELECT id,action_name,action_type,risk_level,confidence,verified,selector_spec FROM module_actions WHERE module_id=<id>;
6. Duyệt hành động thêm giỏ hàng (nút "Duyệt") và 1 hành động đọc/điều hướng bất kỳ (nếu độ tin cậy đủ ngưỡng). Nếu độ tin cậy dưới ngưỡng, ghi nhận lý do hiện cạnh nút,
   rồi (CHỈ để có dữ liệu thử, DB thử) chạy UPDATE module_actions SET confidence=0.95 WHERE id=<id> và duyệt lại.
7. Mở http://localhost:8081/san-pham.html?bot=<public_id>, mở khung chat, hỏi: "Thêm 2 áo thun nam vào giỏ hàng giúp mình". Quan sát DOM trang thử.
8. Trong chat hỏi tiếp: "Áo thun nam này giá bao nhiêu vậy?".
9. Xem DB: SELECT * FROM pending_widget_actions ORDER BY id DESC LIMIT 3; SELECT id,status,tool_calls_used FROM agent_executions ORDER BY id DESC LIMIT 2;
   SELECT type,amount_vnd FROM credit_transactions WHERE team_id=<team> ORDER BY id DESC LIMIT 8;
Kết quả mong đợi:
- Bước 2: đúng như mô tả; bỏ trống ô bắt buộc rồi gửi → trình duyệt chặn ô bắt buộc (nếu vượt qua được, ví dụ gửi trực tiếp, máy chủ trả lỗi "Vui lòng nhập URL trang sản phẩm.") và không tạo module.
- Bước 3: chuyển tới trang chi tiết; bot_modules có 1 dòng module_type_id = id của 'sales_support', status ban đầu 'pending'/'analyzing'.
- Bước 4: thanh tiến độ và chữ "0 / 2 URL" → "1 / 2" → "2 / 2" tự cập nhật (Socket.IO, không F5); nhãn chuyển "Đang phân tích" → "Đã phân tích xong" và trang tự tải lại.
  DB: status='ready', progress_done=2, progress_total=2, error_message rỗng, analysis_charged_vnd > 0 và nhỏ (dưới ~450đ cho mỗi URL, do giá cận trên chỉ ~420đ/URL).
  module_urls: mỗi dòng có crawled_at, raw_html_storage_key dạng "<team>/<bot>/modules/<module>/url-<id>.html" (file có trong MinIO).
- Bước 5: có ÍT NHẤT 1 hành động thêm giỏ (action_type='add_to_cart' hoặc 'click', risk_level='cart'), 1 hành động đọc giá/điều hướng; cột verified = 0 cho MỌI dòng
  (KHÔNG có hành động nào tự bật dù confidence cao). Trang chi tiết hiện mô tả tiếng Việt, nhãn rủi ro, phần trăm tin cậy, nút "Duyệt". Không có nút "duyệt tất cả".
- Bước 6: verified=1 chỉ với hành động vừa duyệt. Hành động rủi ro "Giỏ hàng" cần tin cậy ≥ 80%, "Chỉ đọc" ≥ 60% (dưới ngưỡng: nút Duyệt bị vô hiệu kèm lý do).
- Bước 7: trong ≤ ~15 giây: trên trang thử dòng #cart-message hiện "Đã thêm 2 sản phẩm vào giỏ hàng" và số ở "Giỏ hàng (N)" tăng 2 (widget thao tác DOM thật:
  điền số lượng 2 rồi bấm nút). Bot trả lời xác nhận đã thêm (chỉ sau khi thao tác xong). Console không có lỗi đỏ; tab Network thấy kết nối websocket tới
  `/socket.io/?EIO=4&transport=websocket` thuộc namespace /widget (101 Switching Protocols) và 1 request POST tới /widget/api/<public_id>/actions/<id>/result trả 200.
- Bước 8: bot trả lời giá 199.000đ (con số chỉ có trên trang, KHÔNG có trong tài liệu ⇒ chứng tỏ agent đọc thật từ trang qua hành động đọc; nếu chưa có hành động đọc giá
  được duyệt thì bot nói không biết giá — cũng đúng, không được bịa giá).
- Bước 9: pending_widget_actions có dòng status='done' cho lượt thêm giỏ (token không rỗng, params chứa số lượng 2, conversation_id đúng hội thoại); agent_executions.tool_calls_used ≥ 1;
  credit_transactions có chuỗi reserve → release → execution_charge cho mỗi lượt chat, và 1 dòng module_analysis_charge (số âm) từ Bước 4; số dư giảm tương ứng.

CASE 2 — SIMILAR CASES (các loại hành động/kịch bản khác cùng cơ chế)
Bước thực hiện:
1. Duyệt thêm hành động điền form liên hệ (fill_form trên #contact-form: họ tên, số điện thoại) nếu được phân tích ra. Trong chat trên trang thử: "Mình tên Lan, số 0912345678, để lại thông tin tư vấn giúp mình".
2. Duyệt hành động điều hướng tới giỏ hàng (nếu có). Chat: "Cho mình xem giỏ hàng".
3. Ở trang chi tiết module bấm "Phân tích lại" (không sửa gì). Sau khi xong xem lại module_actions.
4. Sửa file tests/manual/shop/san-pham.html: đổi id="price" thành id="gia-ban" (giữ nguyên giao diện). Bấm "Phân tích lại" rồi xem danh sách hành động.
5. Khai báo thêm 1 module thứ hai cho cùng bot với URL trang sản phẩm = http://localhost:8081/san-pham.html (hai module cùng URL).
Kết quả mong đợi:
- Bước 1: 2 ô #fullname và #phone của form liên hệ được điền "Lan" và "0912345678" (KHÔNG tự gửi form nếu hành động không đặt submit; dòng #contact-result chưa đổi); bot nói đã điền và nhờ khách bấm Gửi.
- Bước 2: trình duyệt chuyển sang gio-hang.html; widget vẫn hiện lại ở trang mới (nhúng lại theo ?bot lưu trong sessionStorage). Kết quả 'done' được ghi trước khi chuyển trang.
- Bước 3: các hành động giống hệt trước (cùng selector_spec, cùng rủi ro) GIỮ nguyên verified=1; hành động thay đổi bất kỳ thì verified=0 và cần duyệt lại.
- Bước 4: hành động đọc giá cũ (#price) không còn được tạo ra với selector cũ; nếu còn thì đã cập nhật sang #gia-ban và verified=0; không có hành động nào trỏ tới selector không tồn tại trên trang
  (trang chi tiết có thể ghi "đã bỏ hành động ..." trong ghi chú phân tích). Hoàn tác file về id="price".
- Bước 5: tạo module thứ hai thành công; khi cả hai có hành động trùng tên và cùng được duyệt, agent vẫn gọi được từng hành động (tên công cụ được thêm hậu tố _<id> khi trùng, không lỗi).

CASE 3 — NORMAL CASE (không liên quan hành động: không bị ảnh hưởng)
Bước thực hiện:
1. Tạo bot thứ hai "Bot Không Module" (chưa khai báo module nào), huấn luyện tài liệu bất kỳ, thêm domain localhost, nhúng widget vào san-pham.html?bot=<public_id bot 2>. Hỏi 1 câu có trong tài liệu.
2. Với "Bot Shop": hỏi 1 câu thông tin thông thường có trong tài liệu ("Chính sách đổi trả thế nào?").
3. Với "Bot Shop": hỏi 1 câu ngoài phạm vi ("Thời tiết Hà Nội hôm nay?").
Kết quả mong đợi:
- Bước 1-2: bot trả lời đúng từ tài liệu như trước Phase M, không gọi hành động nào (agent_executions.tool_calls_used không tăng vì hành động; pending_widget_actions không có dòng mới), DOM trang thử không bị chạm.
- Bước 3: bot từ chối/hỏi lại theo cấu hình như cũ; không có dòng pending_widget_actions mới.

CASE 4 — EDGE CASES
Bước thực hiện:
a) URL không hợp lệ: khai báo module với URL trang sản phẩm "ftp://x", rồi "javascript:alert(1)", rồi "https://shop_x.vn/", rồi "http://user:pw@localhost:8081/x", rồi 2 ô cùng 1 URL.
b) SSRF: đặt MODULE_ALLOW_PRIVATE_HOSTS=false trong .env, khởi động lại `python run.py`. Khai báo module URL http://localhost:8081/san-pham.html; sau đó module URL http://169.254.169.254/latest/meta-data/ .
   Bật lại true + khởi động lại sau khi thử.
c) URL 404: khai báo module với trang sản phẩm đúng + trang giỏ hàng http://localhost:8081/khong-co.html.
d) Hết Credit: với team thử, chạy SQL (DB thử) tạo giao dịch adjustment âm đưa số dư còn ~0,01đ (INSERT credit_transactions type='adjustment' amount=-(số dư-0.01) balance_after=0.01 và UPDATE credit_accounts.balance_vnd=0.01),
   rồi khai báo module mới. Sau đó khôi phục số dư (adjustment dương).
e) Selector hết hạn: (module đã duyệt ở Case 1) sửa san-pham.html đổi id="add-btn" thành id="btn-them" (KHÔNG bấm Phân tích lại). Chat: "Thêm 1 áo thun vào giỏ".
f) Website khác domain đã crawl: thêm domain 127.0.0.1 vào Bước 3. Mở http://127.0.0.1:8081/san-pham.html?bot=<public_id>&server=http://127.0.0.1:5000 (widget chạy ở host 127.0.0.1, module crawl ở localhost). Chat: "Thêm 1 áo thun vào giỏ".
g) Không có kết nối Socket.IO: trong DevTools → Network → chuột phải một request "socket.io" → Block request URL (chặn "/socket.io/"), tải lại trang thử, chat "Thêm 1 áo thun vào giỏ".
h) Chat thử ở Bước 1 của dashboard (khung xem trước): hỏi "Thêm 1 áo thun vào giỏ".
i) Thanh toán (hành động rủi ro cao): khai báo module mới có thêm URL trang thanh toán = http://localhost:8081/thanh-toan.html, đợi phân tích. Trên trang chi tiết tìm hành động bấm "Đặt hàng và thanh toán".
   i1) Với công tắc "Cho phép trợ lý thực hiện hành động thanh toán" ĐANG TẮT (mặc định): thử bấm Duyệt.
   i2) Bật công tắc (trang "Module hành động" → Lưu). (Nếu độ tin cậy < 90%: UPDATE module_actions SET confidence=0.95 WHERE id=<id> — chỉ để có dữ liệu thử.) Duyệt hành động.
   i3) Trên trang thử (chat): "Chốt đơn giúp mình". Quan sát khung chat và trang. Bấm "Huỷ".
   i4) Chat lại "Chốt đơn giúp mình", lần này bấm "Đồng ý".
   i5) Quay lại trang "Module hành động", TẮT công tắc, mở trang chi tiết module.
j) Hết hạn chờ: (tuỳ chọn) đặt AGENT_ACTION_WAIT_SECONDS=1 rồi khởi động lại; chat "Thêm 1 áo thun vào giỏ" khi trang thử đang mở nhưng máy chủ chậm — chỉ cần quan sát nếu tái hiện được.
Kết quả mong đợi:
- a) Mỗi lần đều hiện thông báo lỗi tiếng Việt cụ thể (URL phải bắt đầu bằng http/https; không chứa tên đăng nhập/mật khẩu; tên miền không hợp lệ; URL trùng) và KHÔNG tạo dòng bot_modules mới.
- b) Với private=false: module 'failed', lỗi từng URL ghi "Địa chỉ này thuộc mạng nội bộ nên không được phép phân tích", số dư không bị trừ (không có module_analysis_charge), không có lệnh gọi DeepSeek.
- c) Module ở trạng thái 'ready' (vì URL kia phân tích được); module_urls của URL 404 có error_message "Website trả lỗi HTTP 404"; trang chi tiết hiện lỗi đỏ ở đúng URL đó.
- d) Module 'failed' với thông báo "Không đủ AI Credit để phân tích ..."; không fetch, không LLM, số dư giữ nguyên (không âm).
- e) Bot KHÔNG được nói đã thêm giỏ hàng; nói không thực hiện được / hướng dẫn khách tự thêm. DB: pending_widget_actions status='failed', result.reason='element_not_found'; module_actions của hành động đó
   verified=0, failure_reason='element_not_found'; trang chi tiết hiện nhãn "Cảnh báo: trang đã đổi"; hành động không còn được giao cho agent ở lượt chat sau. Không tự crawl lại (bot_modules.status vẫn 'ready').
   Sau khi hoàn tác id, bấm "Phân tích lại" và duyệt lại thì dùng được.
- f) Widget vẫn hiện và chat RAG bình thường; bot KHÔNG thực thi (DOM không đổi), giải thích không dùng được; KHÔNG có dòng pending_widget_actions mới (bị chặn ở server vì domain widget ≠ domain đã crawl).
- g) Widget hiện, chat bình thường; hành động không chạy, bot không khẳng định đã thực hiện; không có dòng pending_widget_actions mới.
- h) Chat thử không thực hiện gì trên website và bot không khẳng định đã thêm giỏ.
- i1) Nút Duyệt bị vô hiệu kèm dòng "chỉ duyệt được sau khi bật công tắc"; verified vẫn 0.
- i2) Sau khi bật công tắc: duyệt được (verified=1). Công tắc bot_settings.allow_agent_payment_actions=1.
- i3) Khung chat hiện hộp xác nhận "Bạn xác nhận cho trợ lý thực hiện thao tác sau trên website?" + mô tả + nút "Đồng ý"/"Huỷ". TRƯỚC khi bấm: trang thử chưa đổi (#order-result rỗng), bot nói đang chờ khách xác nhận,
   KHÔNG nói đã đặt hàng. Bấm "Huỷ": hiện "Đã huỷ thao tác.", #order-result vẫn rỗng; pending_widget_actions status='failed', reason='cancelled_by_customer'; hành động vẫn verified=1.
- i4) Bấm "Đồng ý": #order-result hiện "ĐƠN HÀNG ĐÃ ĐƯỢC ĐẶT (DEMO) — <số>"; pending_widget_actions status='done'.
- i5) Tất cả hành động thanh toán bị bỏ duyệt (verified=0); các hành động chỉ đọc/giỏ hàng giữ nguyên. Chat "Chốt đơn" lúc này không thực thi gì.
- j) Nếu hết hạn: bot nói chưa nhận được xác nhận và KHÔNG khẳng định đã thực hiện; nếu kết quả tới muộn, pending_widget_actions vẫn cập nhật thành 'done'.

CASE 5 — REGRESSION
Bước thực hiện:
1. Phân quyền: đăng nhập tài khoản B (Member): mở "Module hành động" và trang chi tiết module. Thử (nếu thấy nút) khai báo/duyệt/xoá.
2. Cách ly: đăng nhập tài khoản của team C, dán trực tiếp URL /bots/<id của Bot Shop>/modules và /bots/<id>/modules/<module_id>.
3. Bước 1-3 của bot (Thiết lập lưu cấu hình, Cơ sở tri thức tải/xoá tài liệu, Xuất bản đổi domain/giao diện) và Lịch sử chat.
4. Trang Tin nhắn (Inbox): xem hội thoại vừa tạo từ trang thử; nhân viên gửi 1 tin trả lời từ Inbox — widget nhận sau vài giây.
5. Trang /profile của team: số dư và lịch sử.
6. Tắt AGENT_ENABLED=false (khởi động lại), chat với Bot Shop trên trang thử; sau đó bật lại.
7. Đăng xuất rồi đăng nhập lại; đăng nhập tab khác của khung quản trị (Socket.IO khung quản trị) — mở trang Cơ sở tri thức, tải 1 tài liệu và xem trạng thái xử lý cập nhật realtime.
Kết quả mong đợi:
- Bước 1: Member xem được danh sách và kết quả, KHÔNG thấy form khai báo/nút Duyệt/Phân tích lại/Xoá; POST trực tiếp (nếu thử) bị 403.
- Bước 2: team C nhận 404 cho mọi URL của bot/module thuộc team khác.
- Bước 3: mọi chức năng cũ hoạt động như trước; mục điều hướng "Lịch sử chat" chỉ sáng ở trang lịch sử, "Module hành động" chỉ sáng ở trang module.
- Bước 4: Inbox hiển thị hội thoại và tin nhắn bình thường; tin nhân viên tới widget (không ảnh hưởng bởi kênh /widget mới).
- Bước 5: lịch sử hiển thị dòng "Phân tích module website" (số âm, làm tròn hiển thị) cạnh "Sử dụng trợ lý AI"; KHÔNG hiển thị dòng giữ chỗ/hoàn giữ chỗ và KHÔNG có token/giá vốn LLM.
- Bước 6: bot trả lời RAG bình thường, không dùng hành động, không lỗi; agent_executions không tăng khi AGENT_ENABLED=false.
- Bước 7: đăng nhập/đăng xuất bình thường; trạng thái xử lý tài liệu cập nhật realtime (namespace mặc định của khung quản trị không bị ảnh hưởng bởi namespace /widget).
```

**Lưu ý cho người kiểm thử:** kết quả LLM phân tích không xác định 100% (tên/độ tin cậy hành động có thể khác nhau giữa các lần). Chỉ cần đạt các kỳ vọng về hành vi
(không tự động verified, ngưỡng, chặn thanh toán, kết quả thật, lỗi trung thực); nếu một hành động cần thiết không được phân tích ra, bấm "Phân tích lại" một lần rồi thử tiếp.
