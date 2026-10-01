# Test

Dùng `unittest` của thư viện chuẩn (project chưa có test framework; không thêm pytest).

```powershell
# Toàn bộ test KHÔNG cần DB (settings, decision, builder, retrieval, engine, structured/cost, state, history)
env\Scripts\python.exe -m unittest discover -s tests -t .

# Thêm các test có DB (luồng lưu trạng thái, worker, route Bước 1): cần 1 DB THỬ riêng
```

## DB thử

Test có DB **xóa sạch dữ liệu** các bảng trong `setUp`, nên chỉ chạy khi `DATABASE_URL` trỏ tới DB có tên kết thúc
bằng `_test`. Không có biến này thì các test đó tự bỏ qua (skip) — tuyệt đối không chạy trên DB thật `aichatbot`.

```powershell
# 1. tạo DB thử (một lần)
env\Scripts\python.exe -c "import pymysql; c=pymysql.connect(host='localhost',user='root',password=''); c.cursor().execute('CREATE DATABASE IF NOT EXISTS aichatbot_engine_test CHARACTER SET utf8mb4')"

# 2. dựng schema bằng chính các migration (đồng thời kiểm tra chuỗi migration chạy được từ đầu)
$env:DATABASE_URL = "mysql+pymysql://root:@localhost:3306/aichatbot_engine_test"
env\Scripts\flask.exe --app run.py db upgrade

# 3. chạy toàn bộ
env\Scripts\python.exe -m unittest discover -s tests -t .
```

Test không gọi DeepSeek, không đụng ChromaDB thật (dùng ChromaDB trong bộ nhớ + embedding giả tất định), nhưng dùng Redis
thật (khóa worker, thời gian chờ sau lỗi) và tokenizer thật của model embedding (chỉ đọc `tokenizer.json`, không nạp model ONNX).

## Bản đồ

| File | Kiểm chứng |
| --- | --- |
| `test_decision.py` | Cây quyết định Bước C — mỗi nhánh, thứ tự ưu tiên, giới hạn lượt hỏi làm rõ, AmbiguityDetector |
| `test_retrieval.py` | Ngưỡng **khoảng cách**, công thức cosine, loại trùng, candidate_count/gap, rerank_top_n, ngân sách token |
| `test_builder.py` | Chọn tin gần đây theo token, ngân sách, áp lực, 5 bước nén đúng thứ tự, dựng message theo role |
| `test_engine.py` | Toàn pipeline A→B→C→F: đúng 1 lệnh gọi chính, lệnh gọi phụ tách riêng, trace |
| `test_structured_cost.py` | Parse JSON chặt + gọi lại 1 lần; usage/cache-hit đọc phòng thủ |
| `test_state.py`, `test_settings.py` | Slot/intent/bộ nhớ; tier + mặc định khớp cột DB |
| `test_history.py` | Historical Retrieval (collection riêng, lọc theo hội thoại) |
| `test_db_flow.py` | Bước D/E có DB, summary, embed nền, widget, multi-tenant, chi phí theo team |
| `test_setup.py` | Form Bước 1 theo tier (thuần + qua route thật), CSRF, cách ly team |
| `test_worker.py` | Khoá worker, thời gian chờ sau lỗi, cuộc đua tạo state |
| `test_settings_effect.py` | Mỗi cột `bot_settings` (Bước 1) đổi đúng hành vi chatbot qua đường sản phẩm thật: tham số LLM, prompt, lịch sử, truy xuất, ngưỡng quyết định; công tắc cố định không bị giá trị lưu ghi đè |
| `test_message_format.py` | `format_message`: danh sách "**Tên** — mô tả" dựng box, bảng markdown vẫn là bảng, chống XSS; quy tắc định dạng trong prompt |
| `test_warmup.py` | Khởi động app dựng sẵn client LLM (lượt đầu không gánh ~3 giây khởi tạo) |
| `test_widget_public.py` | Widget công khai bằng `public_id` (id số → 404), nhiều domain/bot, thêm/xóa domain ở Bước 3, mã nhúng |
| `test_history_review.py` | Lịch sử chat: lọc theo quyết định, nhãn diễn giải, thống kê; slot `contact_*` → `Customer` |
| `test_team.py` | Nhóm (team): ma trận quyền Owner/Admin/Member trên route thật, xác thực lại `session["team_id"]` (xóa thành viên → mất quyền ngay), đổi vai trò/xóa/rời nhóm (luôn còn ≥1 chủ nhóm), lời mời qua email và nhận lời mời sau mọi kiểu đăng nhập, chuyển giữa nhiều nhóm, tạo/đổi tên nhóm |
| `test_agent_protocol.py` | Hợp đồng agent: token ký (đúng bot+lượt, hết hạn, giả mạo), việc gửi worker, tóm tắt event của dsh (usage, công cụ, quyết định) |
| `test_agent_mcp.py` | Máy chủ MCP của agent: giao thức, giới hạn tra cứu theo lượt, danh tính không do model truyền, UTF-8 qua stdio (tiến trình con thật) |
| `test_agent_backend.py` | Lớp bọc Harness với harness giả: patch gỡ shell/tắt tải log, dọn phiên, giới hạn lượt/công cụ/thời gian |
| `test_agent_worker.py` | Worker agent + pool tiến trình (Redis thật): LRU, tuần tự, dọn rảnh, bỏ việc quá hạn, FIFO, không vượt `AGENT_MAX_PROCESSES` |
| `test_agent_runtime.py` | Phía Flask: dựng persona/input, đầu ra agent → StructuredOutput, gộp tra cứu, AgentRunner qua Redis, và **cây quyết định vẫn chặn đầu ra của agent** |
| `test_agent_internal.py` | Route nội bộ `/internal/rag/search`: chỉ loopback, token ký theo bot+lượt, không lộ lý do từ chối, tham số theo cấu hình bot |
| `test_agent_cache.py` | Cache tra cứu: khóa theo bot/cấu hình, vô hiệu khi tài liệu đổi (hook huấn luyện/xóa), TTL |
| `test_agent_flow.py` | Luồng thật ở chế độ agent: `agent_executions` mọi lượt (kể cả lỗi), usage, slot → Customer, lỗi không tạo câu trả lời giả |
| `test_credit.py` | Phase D: giá vốn từ usage thật, giữ chỗ/hoàn/quyết toán, số dư không âm, sổ cái cộng dồn khớp, Credit dùng thử đúng 1 lần, trần chi phí/lượt, lượt lỗi vẫn quyết toán, trang /profile |
| `test_website_actions.py` | Phase M (thuần): DOM nhẹ + bộ khớp selector tập con, cấu trúc `selector_spec`, sàn rủi ro cứng (AI không hạ được), giải nén .zip an toàn (zip bomb/slip/allowlist/số file), tự đoán domain từ HTML, phân tích LLM giả + loại selector bịa |
| `test_modules.py` | Phase M (DB): seed loại module, khai báo theo loại (validate từ DB), duyệt + ngưỡng + công tắc thanh toán, tool cho agent, phân tích nền + trừ Credit, route/quyền |
| `test_doc_reader.py` | Module "Đọc tài liệu" (thuần): định dạng/chữ ký tệp, markitdown (PDF/Word/Excel/PowerPoint, không OCR), PDF bản scan chuyển sang DeepSeek vision đúng lúc (không sớm/muộn), usage ghi vào tracker để trừ AI Credit |
| `test_agent_actions.py` | Phase M (agent): công cụ hành động trong MCP, kết quả thật không bịa "đã xong", `process_key`/run file/env, persona, `AgentRunner` nhận hành động qua callback |
| `test_widget_actions.py` | Phase M (widget): kênh Socket.IO `/widget` (public_id + Origin, không đăng nhập), giao lệnh + nhận kết quả, bảo mật domain/visitor/token, thanh toán cần công tắc + khách xác nhận |
| `test_payos.py` | Nạp Credit qua payOS: chữ ký/định dạng gọi API (không mạng thật), tạo đơn, webhook (chữ ký, idempotent, lệch số tiền), trang quay lại (không tin query), giao diện /profile |
| `test_settings_effect_agent.py` | A3 ở chế độ agent (bỏ `temperature`): cấu hình bot đổi đúng persona/input của agent và quyết định; bộ nhớ hội thoại dựng lại từ DB mỗi lượt (không dùng session Harness); truy xuất ban đầu vẫn vào prompt đầu; ngưỡng/chốt an toàn vẫn thắng ý agent |
| `test_auth_logout.py` | Đăng xuất: nút + token CSRF trong sidebar mọi trang có shell; POST hủy phiên thật; CSRF sai/thiếu báo lỗi (không bỏ qua im lặng); bấm 2 lần/chưa đăng nhập không lỗi và không `next=/auth/logout`; thiết bị khác giữ phiên; đóng các kết nối Socket.IO của đúng phiên trình duyệt đã đăng xuất |
| `test_agent_summarize.py` | C2 — công cụ `summarize_conversation`: giao thức, MCP (cổng từ chối sớm, danh tính không do model truyền), route nội bộ với DB+Redis thật (xác thực, ngữ cảnh do Flask giữ, 1 lần/lượt, khóa chung với job nền, lỗi không bị nuốt), AgentRunner (usage vào giá vốn), engine truyền mức áp lực |
| `test_agent_live.py` | KIỂM CHỨNG THẬT end-to-end (worker + dsh + DeepSeek + HTTP); chỉ chạy khi `AGENT_LIVE_TEST=1` (tốn tiền API). Có proxy đếm lệnh gọi `chat/completions` thật: số lệnh gọi/lượt phải BẰNG số bước agent ghi nhận (không có lệnh gọi ngầm); model không được thấy công cụ shell |


## Phần widget chạy trong trình duyệt (embed.js) — kiểm thử thủ công

Repo chưa có framework test JS (và không tự thêm). Thao tác DOM của widget (`runAction` trong `app/widget/embed.js`) được kiểm bằng tay: dựng 1 trang HTML thử có
sản phẩm/nút thêm giỏ/form, lưu trang đó thành .zip ("Webpage, Complete") và tải lên module, xác nhận domain, khai báo domain đó ở Bước 3, duyệt hành động,
bật `AGENT_ENABLED` và chat trên trang thử — xem mục "TEST PROCEDURE" trong báo cáo Phase M.
