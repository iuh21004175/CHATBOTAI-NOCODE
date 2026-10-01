# TEST PROCEDURE PROMPT — Xác minh sửa lỗi "Không có cách đăng xuất" (dành cho Claude Cowork)

> Dán toàn bộ nội dung này vào Claude Cowork. Nguồn: bug Cowork báo ngày 2026-09-25 (Kịch bản A). Claude Code đã sửa; **bạn là người xác minh trên giao diện thật**.
> Trạng thái hiện tại của bản sửa: **AWAITING FUNCTIONAL VERIFICATION** (chưa được coi là FIXED cho tới khi bạn báo PASS).

## 0. Bạn là ai, làm gì
Bạn là QA xác minh chức năng đăng xuất trên ứng dụng thật `http://localhost:5000`. **Không sửa code/cấu hình.** Nam đang xem màn hình bạn: nói to từng bước
("làm X, kỳ vọng Y → thực tế Z → ĐÚNG/SAI"), chụp màn hình mọi điểm SAI, chép nguyên văn thông báo, ghi thời gian nếu có độ trễ. Không đoán nguyên nhân.

**Điều kiện bắt buộc trước khi test:** server phải chạy **bản code mới** (đã khởi động lại `python run.py` sau bản sửa). Nếu bạn không thấy nút "Đăng xuất" ở C1.1
thì hỏi Nam "đã khởi động lại server chưa?" trước khi kết luận lỗi.

**Tài khoản:** tài khoản thật của Nam (Nam đã cho phép). Bạn **không được lưu/chép mật khẩu**; mỗi lần cần đăng nhập lại, để **Nam tự gõ mật khẩu** (hoặc Nam cho phép bạn dùng
trình quản lý mật khẩu của trình duyệt). Không tạo/xóa dữ liệu nào của Nam trong bài này.

## 1. Bản sửa gồm những gì (để bạn biết cái gì cần thấy)
1. **Nút "Đăng xuất"** mới ở sidebar bên trái, **ngay dưới khung tên/email người dùng**, có biểu tượng mũi tên ra cửa. Có ở mọi trang có sidebar (Tổng quan, Team, Inbox, Khách hàng, Thiết lập bot…).
2. Bấm nút → về `/auth/login` và hiện thông báo **"Đã đăng xuất."**; truy cập lại `/dashboard` bằng URL trực tiếp phải bị đẩy về `/auth/login`.
3. Nếu yêu cầu đăng xuất **thiếu/sai token bảo mật** → KHÔNG còn im lặng: ở lại `/dashboard` và hiện thông báo lỗi **"Phiên làm việc đã hết hạn, vui lòng thử đăng xuất lại."** (trước đây bị đẩy về login rồi bật ngược về dashboard nên trông như "không làm gì").
4. Bấm đăng xuất lần 2/khi đã đăng xuất → không lỗi, về `/auth/login` (không còn `?next=/auth/logout`).
5. Đăng xuất còn **đóng các kết nối realtime** (Inbox) của các tab cùng trình duyệt.
6. `GET /auth/logout` vẫn trả **405** — đây là **thiết kế đúng** (đăng xuất chỉ nhận POST để chống bị ép đăng xuất qua liên kết/ảnh), KHÔNG phải lỗi.

## 2. CASE 1 — Original bug (bắt buộc)
| # | Việc làm | Kỳ vọng | Kết quả |
|---|---|---|---|
| 1.1 | Đăng nhập (Nam gõ mật khẩu). Mở `/dashboard`. Nhìn sidebar | Thấy nút **Đăng xuất** dưới khung tên/email; chữ rõ, không bị che/lệch; rê chuột thì nền đổi | PASS / FAIL |
| 1.2 | Mở lần lượt `/team`, `/inbox`, `/customers`, trang Thiết lập của 1 bot | Trang nào có sidebar cũng có nút Đăng xuất | PASS / FAIL (ghi trang thiếu) |
| 1.3 | Bấm **Đăng xuất** ở `/dashboard` | Chuyển tới `/auth/login`, thấy **"Đã đăng xuất."** | PASS / FAIL |
| 1.4 | Gõ trực tiếp URL `http://localhost:5000/dashboard` | Bị đẩy về `/auth/login`, **không** vào được dashboard | PASS / FAIL |
| 1.5 | Thử thêm `/team`, `/inbox`, `/bots/new`, `/auth/me` bằng URL trực tiếp | Tất cả bị đẩy về `/auth/login` (riêng `/auth/me` cũng vậy) | PASS / FAIL |
| 1.6 | Bấm nút Back của trình duyệt sau khi đăng xuất | Có thể thấy trang cũ từ bộ nhớ đệm của trình duyệt, nhưng **bấm bất kỳ liên kết/tải lại (F5) đều phải về `/auth/login`**. Ghi lại chính xác bạn thấy gì | PASS / FAIL |

## 3. CASE 2 — Similar cases (cùng gốc "phiên chưa bị hủy thật")
| # | Việc làm | Kỳ vọng | Kết quả |
|---|---|---|---|
| 2.1 | Mở URL `http://localhost:5000/auth/logout` bằng thanh địa chỉ (GET) khi đang đăng nhập | **405 Method Not Allowed** (đúng thiết kế) và **vẫn đang đăng nhập** (vào `/dashboard` được) | PASS / FAIL |
| 2.2 | **Nhiều tab, cùng trình duyệt:** mở 3 tab cùng đăng nhập (`/dashboard`, `/inbox`, `/team`). Đăng xuất ở tab 1. Sang tab 2 và 3, **F5** | Cả hai tab đều về `/auth/login` | PASS / FAIL |
| 2.3 | **Realtime Inbox:** đăng nhập lại, mở `/inbox` ở tab 2 (giữ nguyên, không F5), đăng xuất ở tab 1. Nếu có thể, cho một "khách" nhắn vào widget của bot (dùng trang thử widget nếu Nam có sẵn) | Tab 2 **không nhận thêm** tin mới theo thời gian thực sau khi đăng xuất (cửa sổ dev tools: kết nối websocket đóng). Nếu không dựng được khách thì ghi "KHÔNG LÀM ĐƯỢC" cùng lý do, không đoán | PASS / FAIL / N/A |
| 2.5 | **Thiết bị khác:** mở cửa sổ **ẩn danh**, đăng nhập cùng tài khoản (Nam gõ mật khẩu). Ở cửa sổ thường, đăng xuất | Cửa sổ ẩn danh **vẫn đăng nhập** (đăng xuất chỉ kết thúc phiên của trình duyệt đó — đúng thiết kế, ghi rõ trong báo cáo) | PASS / FAIL |
| 2.6 | **Token sai:** đăng nhập, mở DevTools (F12) → Console. Chạy đúng dòng: `document.querySelector('form.sidebar-logout input[name=csrf_token]').value='sai-token'` rồi bấm nút **Đăng xuất** | KHÔNG đăng xuất. Ở lại `/dashboard` và thấy **"Phiên làm việc đã hết hạn, vui lòng thử đăng xuất lại."** | PASS / FAIL |
| 2.7 | Tải lại trang (F5) rồi bấm **Đăng xuất** (token đúng trở lại) | Đăng xuất thành công như 1.3 | PASS / FAIL |

## 4. CASE 3 — Normal case (đăng nhập bình thường vẫn chạy)
| # | Việc làm | Kỳ vọng | Kết quả |
|---|---|---|---|
| 3.1 | Sau khi đăng xuất, tại `/auth/login` nhờ Nam đăng nhập lại | Vào `/dashboard`, đúng tài khoản/nhóm của Nam, không thông báo lỗi lạ | PASS / FAIL |
| 3.2 | Nhấp lần lượt: Tổng quan → 1 bot → Thiết lập → Tri thức → Xuất bản → Lịch sử chat | Mọi trang tải bình thường, sidebar và nút Đăng xuất hiển thị đúng ở từng trang | PASS / FAIL |
| 3.3 | Nhập **sai mật khẩu** 1 lần ở trang đăng nhập (sau khi vừa đăng xuất) | Báo lỗi đăng nhập thông thường, không sập, không "văng" vào dashboard | PASS / FAIL |
| 3.4 | Đăng nhập, đăng xuất, đăng nhập lại **3 vòng liên tiếp** | Mỗi vòng đều đúng; không kẹt/không lỗi sau vòng 2–3 (chú ý: đăng nhập sai quá nhiều có thể bị giới hạn tốc độ 10 lần/phút — không phải lỗi) | PASS / FAIL |

## 5. CASE 4 — Edge cases
| # | Việc làm | Kỳ vọng | Kết quả |
|---|---|---|---|
| 4.1 | **Bấm đúp** nhanh nút Đăng xuất | Kết quả cuối: đã đăng xuất, đang ở `/auth/login`; **không** trang lỗi 500/405 | PASS / FAIL |
| 4.2 | Mở 2 tab cùng đăng nhập. Tab 1 đăng xuất. **Ở tab 2 (chưa F5) bấm nút Đăng xuất** (token cũ, phiên đã hết) | Về `/auth/login`, **không** lỗi 500, **không** có thông báo "Vui lòng đăng nhập…" gây nhầm; URL **không** chứa `?next=%2Fauth%2Flogout` | PASS / FAIL |
| 4.3 | Sau 4.2, đăng nhập lại từ trang login đó | Vào `/dashboard` bình thường (không bị đẩy tới trang báo 405) | PASS / FAIL |
| 4.4 | Trong DevTools → Application → Cookies: xem cookie `session` **trước** và **sau** khi đăng xuất | Cookie thay đổi/không còn chứa phiên đăng nhập cũ. (Chỉ ghi "đổi hay không"; **không chép giá trị cookie** vào báo cáo) | PASS / FAIL |
| 4.5 | Thu nhỏ cửa sổ về cỡ điện thoại (~375px), mở menu sidebar | Nút Đăng xuất vẫn nhìn thấy và bấm được trong sidebar di động | PASS / FAIL |
| 4.6 | Dùng bàn phím: Tab tới nút Đăng xuất rồi nhấn Enter | Đăng xuất được; nút hiện viền/nền khi được focus | PASS / FAIL |

## 6. CASE 5 — Regression (các luồng cần đăng nhập vẫn chạy)
Đăng nhập lại rồi kiểm tra: (5.1) `/dashboard` hiển thị đúng danh sách bot; (5.2) mở Thiết lập bot, **Lưu** một thay đổi vô hại (ví dụ thêm 1 dấu cách vào lời chào rồi khôi phục) — lưu thành công, form khác không báo lỗi token;
(5.3) Xuất bản: thêm rồi xóa một domain thử `regress-test.example`; (5.4) `/team`: đổi qua lại giữa các nhóm nếu Nam có nhiều nhóm; (5.5) **Không** tạo team/bot mới trong tài khoản thật của Nam trừ khi Nam đồng ý; nếu Nam đồng ý, tạo bot `Bot kiểm thử đăng xuất` rồi báo Nam để xóa.
Kỳ vọng: tất cả hoạt động như trước, **không** form nào bị báo "Phiên làm việc đã hết hạn" khi bấm bình thường.

| # | Kết quả |
|---|---|
| 5.1 – 5.5 | PASS / FAIL (ghi từng mục) |

## 7. Điều KHÔNG phải lỗi (đừng báo)
- `GET /auth/logout` trả 405 (thiết kế đúng).
- Sau đăng xuất, nút Back có thể hiện trang cũ **từ bộ nhớ đệm**; chỉ cần mọi thao tác/tải lại đều bị chặn.
- Đăng xuất ở một trình duyệt **không** đăng xuất trình duyệt/thiết bị khác của cùng tài khoản (phiên lưu trong cookie của từng trình duyệt).
- Đăng nhập sai nhiều lần bị giới hạn tốc độ (10 lần/phút).

## 8. Báo cáo (bắt buộc theo mẫu)
Với mỗi mục FAIL:
```
LỖI #<số>  Mức độ: NGHIÊM TRỌNG / CAO / TRUNG BÌNH / THẤP     Case: <mã, ví dụ 2.2>
Đã làm gì (từng bước): 1) … 2) …
Kỳ vọng: …          Thực tế (chép NGUYÊN VĂN, kèm URL đang đứng): "…"
Tái hiện: luôn / thỉnh thoảng (x/y)        Ảnh chụp: <mô tả>
```
Mức NGHIÊM TRỌNG = vẫn đăng nhập được sau khi đã đăng xuất, hoặc thấy dữ liệu sau khi đăng xuất.
Cuối cùng: **bảng PASS/FAIL theo từng mã (1.1 … 5.5)** và kết luận một trong: `VERIFIED FIXED` (mọi mục bắt buộc của Case 1, 2.1, 2.2, 2.6, 4.1, 4.2 PASS) / `NOT FIXED` / `PARTIAL` (nêu rõ mục nào FAIL). Lưu vào `bao-cao-cowork-dang-xuat-<ngày>.md` (không chứa mật khẩu/cookie/khóa).
