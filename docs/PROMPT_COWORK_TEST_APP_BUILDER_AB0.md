TÊN TÍNH NĂNG / BUG:
App Builder AB0 — tạo ứng dụng quản lý bằng AI (Spec -> duyệt -> database MySQL riêng 500MB -> code jQuery -> chạy thật qua SDK).

MỤC TIÊU KIỂM THỬ:
Xác nhận luồng từ 1 mô tả tiếng Việt tới app chạy thật hoạt động đúng, dữ liệu nằm trong database riêng của app, server tự kiểm tra dữ liệu/hạn mức/CSRF/quyền team, code AI sinh
không chứa mẫu nguy hiểm, và các chức năng cũ không bị ảnh hưởng. Ghi lại chi phí AI thật + chất lượng giao diện để quyết định AB1+.

ĐIỀU KIỆN TIÊN QUYẾT:
- Chạy `env\Scripts\flask.exe --app run.py db upgrade` trên DB chính `aichatbot` (thêm 2 bảng builder_apps, builder_app_versions). Nhật ký có dòng "Running upgrade a4c6e8b0d2f1 -> a1b2c3d4e5f6".
- .env có DEEPSEEK_API_KEY hợp lệ; MySQL/XAMPP đang chạy; tài khoản MySQL trong TENANT_DB_ADMIN_URL (mặc định root, không mật khẩu) có quyền CREATE DATABASE/CREATE USER/GRANT.
- Redis (và Chroma, MinIO như thường lệ) đang chạy; khởi động `python run.py`.
- 2 tài khoản thuộc 2 team khác nhau: A (Chủ nhóm hoặc Quản trị viên của team 1), B (Thành viên của team 1, vai trò "Member"), C (team 2 khác).
- Có công cụ xem DB (phpMyAdmin) để kiểm tra `SHOW DATABASES`, bảng `builder_apps`, `builder_app_versions`.

CASE 1 — ORIGINAL BUG (luồng chính end-to-end)
Bước thực hiện:
1. Đăng nhập tài khoản A. Sidebar có mục "Ứng dụng AI" -> bấm vào (URL /builder).
2. Ô "Bạn cần ứng dụng gì?" nhập: "Quản lý kho cho cửa hàng điện máy: có sản phẩm, kho, phiếu nhập, phiếu xuất và xem tồn kho." -> bấm "Thiết kế ứng dụng".
3. Trang chi tiết hiện nhãn "Đang thiết kế Spec"; chờ (tối đa ~1 phút) trang tự tải lại.
4. Đọc Spec: có các bảng (kho, sản phẩm, phiếu nhập, phiếu xuất...) với field/kiểu hợp lý, có màn hình danh sách + màn hình thống kê. Bấm "Duyệt Spec & sinh ứng dụng".
5. Chờ nhãn chuyển "Đang sinh ứng dụng" -> "Đã sẵn sàng" (vài phút). Quan sát khối "Phiên bản 1": có chi phí AI (đ) và số lệnh gọi; khối "Dung lượng dữ liệu" hiện x MB / 500MB.
6. Trong khung xem trước (hoặc "Mở ứng dụng ở tab mới"): thanh điều hướng bên trái có đủ màn hình; mở màn "Kho" -> "+ Thêm mới" -> nhập "Kho A" -> Lưu -> dòng mới hiện trong bảng. Tạo tiếp 1 sản phẩm chọn Kho A trong ô chọn "Kho", giá "1500000".
7. Mở phpMyAdmin: `SHOW DATABASES` có 1 database dạng `pf_<16 ký tự hex>`; bảng `kho`, `san_pham`... nằm TRONG database đó và có đúng bản ghi vừa tạo; database chính `aichatbot` KHÔNG có bảng kho/san_pham. Bảng `builder_apps` có dòng của app với db_name khớp, status = ready.
Kết quả mong đợi:
Tất cả bước chạy suôn; dữ liệu ở database riêng `pf_*`; số tiền hiển thị theo định dạng Việt Nam (1.500.000); không có lỗi đỏ trên màn hình app; console trình duyệt (F12) không có lỗi JS.

CASE 2 — SIMILAR CASES (cùng nguyên tắc: server kiểm tra, không tin giao diện)
Bước thực hiện:
1. Trong app đã tạo, màn "Sản phẩm": bấm Thêm, để trống ô bắt buộc (vd Tên) -> Lưu. Quan sát lỗi đỏ dưới đúng ô và toast lỗi.
2. Với dữ liệu sai kiểu: dùng console F12 của tab app chạy `PF.records.create('san_pham', {ten: 'X', gia: 'abc'})` -> bị từ chối (400) với lỗi ở field "gia"; thử `PF.records.create('san_pham', {ten: 'X', khong_co_field: 1})` -> bị từ chối "field không tồn tại"; thử ref không tồn tại (kho_id = 99999) -> lỗi "không tìm thấy bản ghi".
3. Cũng trong console: `PF.records.list('khong_co', {})` -> lỗi 404 "Collection không tồn tại". Thử `PF.records.list('san_pham', {sort: 'x; DROP TABLE kho'})` -> lỗi 400 "Không sắp xếp được", và bảng `kho` trong DB vẫn còn.
4. Gửi request ghi KHÔNG có token CSRF (dùng công cụ như Postman/curl với cookie phiên của A): POST /apps/<public_id>/_api/kho body {"ten":"Hack"} không header X-CSRF-Token -> 403.
Kết quả mong đợi:
Mọi trường hợp trên bị server từ chối với thông báo tiếng Việt, không có bản ghi sai nào được lưu, không có lỗi 500.

CASE 3 — NORMAL CASE
Bước thực hiện:
1. Màn danh sách: tìm kiếm theo tên, bấm tiêu đề cột để đổi sắp xếp, tạo > 20 bản ghi để thấy phân trang (nút ‹ ›).
2. Sửa 1 bản ghi (đổi tên) -> lưu -> bảng cập nhật; xoá 1 bản ghi -> hộp xác nhận -> Đồng ý -> biến mất; bấm Huỷ ở hộp xác nhận -> bản ghi còn.
3. Màn thống kê hiển thị số liệu khớp với số bản ghi thực (đối chiếu trong phpMyAdmin).
4. Nhập tên có ký tự đặc biệt: `<script>alert(1)</script>` và `"><img src=x onerror=alert(1)>` vào tên sản phẩm -> Lưu -> chúng hiển thị nguyên văn như chữ trong bảng, KHÔNG có hộp thoại alert nào.
Kết quả mong đợi:
Thao tác đúng, số liệu khớp DB, không có XSS (không alert), không lỗi console.

CASE 4 — EDGE CASES
Bước thực hiện:
1. Mô tả quá ngắn: ở /builder nhập "kho" -> thông báo "Hãy mô tả ứng dụng (ít nhất 10 ký tự)", không tạo app.
2. Mô tả không liên quan/mơ hồ ("xin chào bạn khoẻ không") -> hệ thống vẫn kết thúc ở trạng thái "Chờ bạn duyệt Spec" với 1 Spec hợp lý, hoặc "Có lỗi" kèm nút "Thử lại" (không treo vĩnh viễn ở "Đang thiết kế"). Bấm "Thử lại" (nếu có) chạy lại được.
3. Hạn mức dung lượng: tạm đặt `TENANT_DB_QUOTA_MB=0` trong .env (hoặc cập nhật `builder_apps.storage_quota_mb` = 0 cho app thử qua phpMyAdmin), khởi động lại server, rồi trong app bấm Thêm -> Lưu: bị chặn với toast "Ứng dụng đã dùng hết ... dung lượng"; bấm Xoá một bản ghi vẫn được. Trả lại giá trị cũ sau khi thử.
4. Tạo tối đa 5 app trong team (BUILDER_MAX_APPS_PER_TEAM) -> app thứ 6 bị từ chối bằng thông báo, nút tạo bị vô hiệu.
5. Bấm "Duyệt Spec & sinh ứng dụng" 2 lần liên tiếp thật nhanh (hoặc mở 2 tab bấm cùng lúc) -> chỉ có 1 lượt sinh chạy (không tạo 2 database `pf_*` cho cùng app; bảng builder_app_versions chỉ có 1 phiên bản mới mỗi lần).
6. Tắt server giữa lúc "Đang sinh ứng dụng", bật lại, chờ > 15 phút (hoặc đặt BUILDER_STALE_SECONDS=30) và mở lại trang chi tiết: hiện "Có lỗi" "gián đoạn" + nút "Thử lại"; bấm Thử lại -> tái sinh thành công, vẫn dùng đúng database `pf_*` cũ (không tạo database thứ 2), dữ liệu cũ còn.
7. Ở trang chi tiết khối "Phiên bản", nếu có cảnh báo vàng "dùng mẫu mặc định" thì ghi lại nội dung (nghĩa là code AI bị quét từ chối); màn đó vẫn phải chạy được bằng mẫu mặc định.
Kết quả mong đợi:
Mọi trường hợp biên xử lý đúng như mô tả, không treo, không tạo database mồ côi, không mất dữ liệu.

CASE 5 — REGRESSION (cô lập team + chức năng cũ)
Bước thực hiện:
1. Đăng nhập tài khoản B (Member team 1): vào /builder thấy danh sách app của team 1, KHÔNG có form "Tạo ứng dụng mới"; mở app đã sẵn sàng và dùng được (thêm/sửa dữ liệu); gửi POST /builder/apps (qua công cụ) -> 403.
2. Đăng nhập tài khoản C (team 2): mở trực tiếp URL /apps/<public_id>/ và /builder/apps/<id> của app team 1 -> 404; gọi /apps/<public_id>/_api/kho -> 404. Đăng xuất rồi mở /apps/<public_id>/ -> chuyển về trang đăng nhập; gọi /apps/<public_id>/_api/kho khi chưa đăng nhập -> 401 JSON.
3. Kiểm tra header của /apps/<public_id>/ (F12 -> Network): có `Content-Security-Policy` chứa `script-src 'self'`, có `X-Content-Type-Options: nosniff`.
4. Chức năng cũ: Bảng điều khiển, Tin nhắn (Inbox), Module, Cài đặt nhóm, AI Credit & Thanh toán, tạo trợ lý mới, chat thử widget: mở từng trang và thực hiện 1 thao tác chính, không lỗi. Số dư AI Credit KHÔNG bị trừ khi tạo/sinh app (AB0 chỉ ghi chi phí để đo, đối chiếu bảng credit_transactions không có dòng mới).
5. Chạy bộ test tự động: `env\Scripts\python.exe -m unittest discover -s tests -t .` -> chỉ còn đúng 3 lỗi đã tồn tại từ trước (2 test loopback trong test_agent_internal/test_agent_summarize và test_modules.SeedData nhãn "URL trang sản phẩm"), không có lỗi mới.
Kết quả mong đợi:
Cô lập team chặt (404/401/403 đúng), CSP có mặt, chức năng cũ nguyên vẹn, Credit không đổi, không lỗi test mới.

GHI NHẬN THÊM (phục vụ quyết định AB1+):
Với mỗi app thử, ghi: chi phí AI (đ) từng phiên bản, thời gian sinh Spec / sinh code, số màn hình bị thay bằng mẫu mặc định, và nhận xét chất lượng giao diện (đủ dùng / cần sửa gì).
