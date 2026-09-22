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
