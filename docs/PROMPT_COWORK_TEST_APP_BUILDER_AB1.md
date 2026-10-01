TÊN TÍNH NĂNG / BUG:
App Builder AB1 — phân quyền theo vai trò app cho thành viên nhóm + nhật ký thao tác dữ liệu (audit). (Chạy SAU hoặc CÙNG với prompt AB0.)

MỤC TIÊU KIỂM THỬ:
Xác nhận: Spec do AI sinh có vai trò + ma trận quyền hợp lý; Chủ nhóm/Quản trị viên gán vai trò cho thành viên; MỖI thao tác xem/thêm/sửa/xoá bị SERVER chặn đúng theo vai trò (không chỉ ẩn nút);
thành viên chưa được gán không vào được; mọi thao tác ghi đều có nhật ký đúng người, đúng giá trị cũ/mới.

ĐIỀU KIỆN TIÊN QUYẾT:
- Đã chạy `env\Scripts\flask.exe --app run.py db upgrade` trên DB chính (có dòng "Running upgrade a1b2c3d4e5f6 -> b2c3d4e5f6a7"); tồn tại bảng builder_app_members, builder_app_audit.
- Các điều kiện của prompt AB0 (DeepSeek key, MySQL quyền tạo database, server chạy).
- Team 1 có: A (Chủ nhóm hoặc Quản trị viên), B và B2 (hai Thành viên "Member"). Tài khoản C thuộc team 2 khác. Mời B, B2 bằng chức năng "Cài đặt nhóm" sẵn có.
- Tạo MỚI 1 app bằng tài khoản A (không dùng app AB0 cũ vì Spec cũ không có vai trò): mô tả "Quản lý kho cho cửa hàng điện máy: sản phẩm, kho, phiếu nhập, phiếu xuất. Thủ kho chỉ xem và nhập kho, quản lý được xuất kho và xoá." -> duyệt Spec -> chờ "Đã sẵn sàng".

CASE 1 — ORIGINAL BUG (người dùng có quyền thấp vẫn sửa/xoá được mọi thứ trong app)
Bước thực hiện:
1. Tài khoản A mở trang chi tiết app: khối "Vai trò & quyền" liệt kê >= 2 vai trò (vd Thủ kho, Quản lý) với quyền từng collection (xem/thêm/sửa/xoá). Ghi lại ma trận: Thủ kho KHÔNG có "xoá"; phiếu xuất của Thủ kho không có "thêm/sửa" (hoặc theo đúng mô tả).
2. Khối "Phân vai trò cho thành viên": gán B = "Thủ kho", B2 để "— Không có quyền —". Bấm Lưu cho B -> thông báo "Đã cập nhật vai trò".
3. Đăng nhập B, mở app: menu chỉ hiện màn hình B được xem; ở màn có quyền xem+thêm, thấy nút "+ Thêm mới" nhưng KHÔNG có nút "Xoá" (và "Sửa" nếu vai trò không có sửa). Thêm 1 bản ghi hợp lệ thành công.
4. Vẫn ở B, mở F12 -> Console và chạy: `PF.records.remove('<tên collection>', 1)` (tên và id có thật) và `PF.records.update('<collection không có quyền sửa>', 1, {...})`, `PF.records.create('<collection không có quyền thêm>', {...})`.
Kết quả mong đợi:
Các lệnh ở bước 4 bị từ chối (HTTP 403, thông báo "Bạn không có quyền thực hiện thao tác này."), bản ghi trong DB `pf_*` KHÔNG đổi. Bản ghi thêm ở bước 3 có trong DB.

CASE 2 — SIMILAR CASES (các đường vòng quyền khác)
Bước thực hiện:
1. B2 (chưa được gán): vào /builder -> danh sách không có app; mở trực tiếp /builder/apps/<id> -> 404; mở /apps/<public_id>/ -> 403; gọi /apps/<public_id>/_api/<collection> -> 403 JSON "Bạn chưa được gán vai trò".
2. B (Thủ kho) với collection mà vai trò KHÔNG có "view": mở trực tiếp URL `<tên collection>.html` trong app -> trang mở được nhưng bảng báo lỗi quyền/không có dữ liệu; gọi `PF.records.list('<collection đó>', {})` và `PF.records.aggregate('<collection đó>', {agg:'count'})` -> 403; `PF.schema()` chỉ trả các collection B được xem.
3. B gửi POST/PUT/DELETE thiếu header X-CSRF-Token (Postman, dùng cookie phiên của B) -> 403.
4. Mở F12 -> Elements trong trang app của B, sửa nội dung thẻ `<meta name="pf-perms">` thành toàn quyền rồi bấm nút Xoá hiện ra -> server vẫn trả 403, bản ghi còn.
5. A thu hồi vai trò của B (chọn "— Không có quyền —" -> Lưu): B tải lại app -> 403; gọi API -> 403.
6. Tài khoản C (team 2): /apps/<public_id>/ và /builder/apps/<id> -> 404; API -> 404.
Kết quả mong đợi:
Mọi đường truy cập trái quyền đều bị server chặn đúng mã; không có bản ghi nào đổi; không có lỗi 500.

CASE 3 — NORMAL CASE
Bước thực hiện:
1. A gán B2 = "Quản lý": B2 mở app thấy đủ màn hình, Thêm/Sửa/Xoá hoạt động trên các collection vai trò Quản lý được phép; màn thống kê hiển thị số liệu.
2. A (Chủ nhóm/Quản trị viên) mở app: toàn quyền mọi collection, không cần gán vai trò; trong khối phân vai trò, dòng của A hiện "Toàn quyền" (không có ô chọn).
3. Chọn phiếu có ô tham chiếu (vd phiếu nhập -> sản phẩm): form của vai trò có quyền hiển thị đủ danh sách sản phẩm để chọn; cột tham chiếu trong bảng hiện tên sản phẩm (không phải #id).
4. Đổi vai trò B từ Thủ kho sang Quản lý rồi tải lại app của B: quyền thay đổi ngay theo vai trò mới.
Kết quả mong đợi:
Quyền đúng theo ma trận, đổi vai trò có hiệu lực ở lần tải trang kế tiếp, form/bảng có tham chiếu dùng được.

CASE 4 — EDGE CASES
Bước thực hiện:
1. Thử gán vai trò cho chính A (Chủ nhóm) bằng cách gửi POST /builder/apps/<id>/members với user_id của A và role_id hợp lệ (dùng công cụ như Postman, kèm csrf_token) -> thông báo "luôn có toàn quyền, không cần gán vai trò", không tạo dòng builder_app_members cho A.
2. Gửi POST members với role_id không tồn tại (vd "abc") và với user_id của người thuộc team khác -> bị từ chối với thông báo lỗi, không tạo dòng nào.
3. B (Member) gửi POST /builder/apps/<id>/members -> 403.
4. Tạo app mới với mô tả không nhắc phân quyền ("danh sách việc cần làm, có tên việc và hạn chót"): Spec vẫn hợp lệ (có thể có hoặc không có vai trò). Nếu không có vai trò: khối phân vai trò báo "Spec chưa có vai trò nào"; chỉ A dùng được app; B không thấy app.
5. Spec do AI sinh vi phạm quy tắc quyền (vd vai trò có "create" nhưng thiếu "view", hoặc dùng collection có tham chiếu mà thiếu "view" collection được tham chiếu): hệ thống tự gọi lại AI để sửa; nếu vẫn sai thì báo "Có lỗi" kèm lý do + nút "Thử lại" (không lưu Spec cấp quyền sai). Ghi lại nếu gặp.
6. Lưu một bản ghi mà không đổi gì (mở Sửa -> bấm Lưu ngay) -> KHÔNG phát sinh dòng nhật ký "Sửa" mới.
7. Xoá một bản ghi rồi kiểm tra nhật ký: vẫn còn dòng "Xoá" với giá trị cũ đầy đủ (trong builder_app_audit.old_values).
Kết quả mong đợi:
Các yêu cầu sai bị từ chối an toàn; Spec không bao giờ cấp quyền thiếu "view"; nhật ký không bị nhiễu bởi lần lưu không đổi.

CASE 5 — REGRESSION
Bước thực hiện:
1. Nhật ký: A mở trang chi tiết, mục "Nhật ký thao tác dữ liệu" liệt kê thao tác của B/B2/A vừa làm: đúng người, đúng thao tác (Thêm/Sửa/Xoá), đúng bản ghi, nguồn "Giao diện". Đối chiếu bảng builder_app_audit: old_values/new_values đúng (sửa tên "A" -> "A2" có old tên "A", new tên "A2"; xoá có new_values NULL; thêm có old_values NULL). B (Member) không thấy mục nhật ký ở trang chi tiết.
2. Thử sửa/xoá trực tiếp bảng builder_app_audit bằng ứng dụng không thể (chỉ-thêm ở tầng ORM) — chỉ cần xác nhận giao diện không có chức năng nào xoá/sửa nhật ký.
3. Luồng AB0 vẫn đúng: tạo app, duyệt Spec, sinh app, thêm dữ liệu bằng tài khoản A; hạn mức dung lượng, cô lập team, CSP header như prompt AB0 (Case 4.3 và Case 5.2-5.3).
4. Chức năng cũ: Bảng điều khiển, Tin nhắn, Module, Cài đặt nhóm (mời thành viên, đổi vai trò, xoá thành viên), AI Credit & Thanh toán, chat thử widget — mỗi nơi 1 thao tác chính, không lỗi. Xoá B khỏi nhóm rồi thử mở app bằng B -> không vào được.
5. Chạy `env\Scripts\python.exe -m unittest discover -s tests -t .` -> chỉ còn đúng 3 lỗi đã có từ trước (2 test loopback trong test_agent_internal/test_agent_summarize và test_modules.SeedData nhãn "URL trang sản phẩm").
Kết quả mong đợi:
Nhật ký đầy đủ, chính xác, chỉ người quản lý xem; AB0 và chức năng cũ nguyên vẹn; không có lỗi test mới.
