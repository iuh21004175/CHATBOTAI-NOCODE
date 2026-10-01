# App Builder (AB0 spike + AB1 phân quyền)

Từ 1 mô tả tiếng Việt -> LLM sinh **Spec** (collection/field/màn hình) -> người dùng duyệt -> nền tảng cấp **database MySQL riêng** + dựng bảng -> LLM sinh code
từng màn hình (jQuery) -> app chạy tại `/apps/<public_id>/`. Định hướng đầy đủ: `dinh-huong-mo-rong-app-builder-ai-2026-10-01.md` (AB0–AB6).

## Phân vai (không để LLM quyết định bảo mật)
| Phần | Ai viết | Ở đâu |
| --- | --- | --- |
| Dữ liệu, kiểm tra kiểu/bắt buộc/ref, hạn mức, đăng nhập, CSRF | Nền tảng (cố định) | `app/builder/tenant_db.py`, `routes.py` |
| `platform-sdk.js`, UI Kit (jQuery), jQuery 3.7.1 ghim, khung HTML mọi trang | Nền tảng (cố định) | `app/static/builder/`, `codegen.build_page` |
| Logic hiển thị từng màn hình (1 file `<id>.js`) | LLM, qua quét tĩnh `scan.py` | `builder_app_versions.files` |

Code LLM sinh không nối DB: chỉ gọi `PF.records.*` -> `/apps/<id>/_api/...`. JS bị quét từ chối sau mọi vòng sửa (`BUILDER_MAX_FIX_ROUNDS`) thì được **thay bằng mẫu mặc định
của nền tảng** (cảnh báo hiện ở trang chi tiết) — không bao giờ chạy code bị từ chối.

## Database riêng mỗi app
- `pf_<16 hex>` + user MySQL cùng tên, quyền `SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX` chỉ trên database đó (không DROP/GRANT).
- Cấp phát bằng `TENANT_DB_ADMIN_URL` (mặc định `root` XAMPP — **production phải đặt tài khoản riêng**). Mật khẩu = HMAC(SECRET_KEY, tên db), không lưu: **đổi SECRET_KEY = mất kết nối dữ liệu các app đã tạo**.
- Hạn mức `TENANT_DB_QUOTA_MB` (500MB): đo `data_length + index_length`; đầy -> chặn thêm/sửa (HTTP 413), xoá vẫn được. `builder_apps.storage_quota_mb` là chỗ nâng gói về sau (AB6).
- Schema chỉ THÊM bảng/cột (cột mới nullable). Không bao giờ DROP/đổi kiểu. Không có chức năng xoá app/database trong AB0.

## Phân quyền (AB1) — dùng lại thành viên nhóm
- Người dùng cuối của app = thành viên nhóm sở hữu app (không có tài khoản thứ hai). Chủ nhóm/Quản trị viên (`manage_apps`) toàn quyền.
- Spec có `roles`: vai trò × collection × hành động (`view|create|update|delete`; ghi luôn kèm `view`; dùng collection có `ref` thì phải `view` collection được ref — Spec vi phạm bị `spec.validate` từ chối, không tự nới quyền). Logic ở `app/builder/access.py`.
- Chủ nhóm/Quản trị viên gán vai trò app cho từng thành viên (`builder_app_members`) ở trang chi tiết. Thành viên chưa gán = không thấy app (danh sách ẩn, `/apps/<id>/` 403, API 403, trang quản trị 404). Vai trò bị bỏ khỏi Spec = mất quyền.
- Server kiểm quyền ở MỌI API (`_need` trong `routes.py`); `PF.can()`/menu chỉ để ẩn nút, lấy từ `<meta name="pf-perms">` server điền lúc phục vụ trang. `_schema` chỉ trả collection được `view`, không lộ ma trận vai trò.
- Nhật ký `builder_app_audit` (chỉ-thêm, ở DB chính): ai/lúc nào/giá trị cũ-mới/nguồn (`ui`; `chatbot` dành cho AB5). Nằm ngoài database của app nên người dùng app không tự xoá được. Nhật ký ghi sau thao tác dữ liệu (2 database khác nhau, không chung transaction): lỗi ghi nhật ký được log ERROR kèm đủ nội dung, không đảo ngược thao tác.
- App sinh ở AB0 (Spec không có `roles`) chỉ Chủ nhóm/Quản trị viên dùng được; thêm vai trò cần Spec mới (sửa Spec = AB2).

## Giới hạn đã biết (làm ở phase sau)
- Chưa có khách ngoài nhóm đăng nhập vào app, và chưa có phân quyền theo bản ghi/field (chỉ theo collection). Chưa có workflow/ràng buộc nghiệp vụ (AB2): phiếu xuất không tự trừ tồn.
- App chạy cùng origin với nền tảng, CSP chặt nhưng chưa domain con (AB4). Quét tĩnh là heuristic (regex).
- Chưa có kiểm tra bằng headless browser (AB3b: cần đề xuất dependency riêng), chưa sửa bằng diff/rollback (AB4), chưa chatbot cầu nối (AB5), chưa trừ Credit (AB6) — chi phí LLM thật chỉ được GHI vào `builder_app_versions.llm_cost_vnd` để đo.
- Biểu đồ chưa có trong UI Kit (chỉ bảng + thẻ số liệu).
