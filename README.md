# Ứng dụng quản lý Chatbot AI

Kiến trúc & lộ trình chi tiết: xem [.claude/Đề xuất kiến trúc hệ thống & công nghệ — Ứng dụng quản lý Chatbot AI.md](.claude/Đề xuất kiến trúc hệ thống & công nghệ — Ứng dụng quản lý Chatbot AI.md).

## Yêu cầu hạ tầng

Ứng dụng cần các service sau chạy sẵn trước khi start:

- **MySQL/MariaDB** — port 3306
- **Redis** — port 6379
- **ChromaDB** — port 8000
- **MinIO** — port 9000 (API), 9001 (console)

## Cài đặt lần đầu

```bash
# 1. Kích hoạt virtualenv (đã có sẵn ở thư mục env/)
env\Scripts\activate          # Windows cmd/PowerShell
source env/Scripts/activate   # Git Bash

# 2. Cài dependency
pip install -r requirements.txt

# 3. Tạo file .env từ mẫu, điền giá trị thật (DB, Redis, MinIO, DeepSeek API key...)
cp .env.example .env

# 4. Tạo schema MySQL
flask db upgrade

# 5. (Tùy chọn) Tạo user demo để test đăng nhập
python scripts/seed_admin.py
# -> tạo admin@example.com / Admin@123
```

## Chạy dự án

```bash
python run.py
```

Chạy bằng `python run.py` (không dùng `flask run`): server dev của Flask không hỗ trợ WebSocket nên trạng thái xử lý
tài liệu ở Bước 2 không cập nhật trực tiếp được. `run.py` gọi `eventlet.monkey_patch()` đầu tiên — cần thiết khi
Socket.IO dùng eventlet + Redis.

Mặc định chạy tại `http://localhost:5000`. Trang đăng nhập: `http://localhost:5000/auth/login`.

## Khi thay đổi model (SQLAlchemy)

```bash
flask db migrate -m "mô tả thay đổi"
flask db upgrade
```

## Worker xử lý tài liệu (vector hóa)

`python run.py` **tự khởi động worker** cùng app: nạp model embedding ngay lúc khởi động, xử lý luôn các tài liệu
đang chờ rồi tiếp tục nhận tài liệu mới — không cần bật thêm tiến trình nào. Phần tính toán nặng chạy ở luồng riêng
nên server vẫn phản hồi bình thường khi đang embed.

Production nên tách riêng để không chia CPU với server web:

```bash
# .env: EMBEDDED_WORKER=false
python -m workers.process_documents   # embedding tài liệu cơ sở tri thức
```

Chỉ 1 worker hoạt động tại 1 thời điểm (khoá Redis), bật cả hai cách cũng không xử lý trùng.

```bash
python workers/send_followups.py      # gửi FollowUp theo lịch (chạy bằng cron / Task Scheduler)
```

## Cấu trúc thư mục

```
app/            blueprint theo chức năng (auth, bots, knowledge, inbox, followup,
                customers, reports, api_tokens, profile, widget) + templates/, static/
core/           rag_engine, llm_client (DeepSeek), storage_service (MinIO)
workers/        job nền (embedding, followup)
extensions.py   khởi tạo client dùng chung (MySQL, Redis, ChromaDB, MinIO, SocketIO)
config.py       đọc cấu hình từ .env
scripts/        script tiện ích (seed dữ liệu test)
templates/      giao diện mockup gốc (.zip, tham khảo thiết kế — không phải Jinja template)
```
