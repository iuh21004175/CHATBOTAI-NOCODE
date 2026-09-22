# eventlet.monkey_patch() PHẢI chạy trước mọi import khác. Socket.IO dùng eventlet + Redis làm cầu nối
# giữa worker và server web; thiếu dòng này server báo "Redis requires a monkey patched socket library".
import eventlet

eventlet.monkey_patch()

import os  # noqa: E402

from app import create_app  # noqa: E402
from extensions import socketio  # noqa: E402
from workers.context_jobs import start_embedded as start_context_jobs  # noqa: E402
from workers.process_documents import start_embedded  # noqa: E402

app = create_app()

if __name__ == "__main__":
    debug = True
    # Chế độ debug chạy file này 2 lần (tiến trình giám sát để tự nạp lại code + tiến trình chạy thật): chỉ
    # tiến trình chạy thật (WERKZEUG_RUN_MAIN=true) mới khởi động model + worker, kẻo nạp model 2 lần.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_embedded(app)
        start_context_jobs(app)  # rolling summary + embed lịch sử chat (việc nền của Decision Engine)

    # socketio.run thay vì app.run để WebSocket (Inbox realtime, trạng thái xử lý tài liệu) hoạt động đúng.
    # Chạy bằng `python run.py` (không dùng `flask run`: server dev của Flask không hỗ trợ WebSocket).
    socketio.run(app, host="0.0.0.0", port=5000, debug=debug)
