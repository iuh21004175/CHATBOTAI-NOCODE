import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "mysql+pymysql://root:@localhost:3306/aichatbot"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    CHROMA_HOST = os.environ.get("CHROMA_HOST", "localhost")
    CHROMA_PORT = int(os.environ.get("CHROMA_PORT", "8000"))

    MINIO_HOST = os.environ.get("MINIO_HOST", "localhost")
    MINIO_PORT = os.environ.get("MINIO_PORT", "9000")
    MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
    MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")
    MINIO_SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "aichatbot")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    FACEBOOK_CLIENT_ID = os.environ.get("FACEBOOK_CLIENT_ID", "")
    FACEBOOK_CLIENT_SECRET = os.environ.get("FACEBOOK_CLIENT_SECRET", "")

    MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", os.environ.get("MAIL_USERNAME", ""))
    # Đặt true để tắt gửi mail thật (test, hoặc môi trường chưa cấu hình SMTP) — Flask-Mail sẽ
    # chỉ ghi nhận vào outbox nội bộ thay vì kết nối SMTP.
    MAIL_SUPPRESS_SEND = os.environ.get("MAIL_SUPPRESS_SEND", "false").lower() == "true"

    DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    EMBEDDING_MODEL_NAME = os.environ.get(
        "EMBEDDING_MODEL_NAME", "AITeamVN/Vietnamese_Embedding"
    )
    # Model đã tải sẵn (ONNX export) trong thư mục dự án — không cần tải lại từ HuggingFace Hub.
    EMBEDDING_MODEL_PATH = os.environ.get(
        "EMBEDDING_MODEL_PATH", os.path.join(os.path.dirname(__file__), "models", "Vietnamese_Embedding")
    )

    RATELIMIT_STORAGE_URI = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Kênh Redis của Socket.IO: mọi tiến trình (web + worker) phải dùng chung 1 tên. Đổi tên khi nhiều
    # môi trường (dev/test) dùng chung 1 Redis để sự kiện không lẫn sang nhau.
    SOCKETIO_CHANNEL = os.environ.get("SOCKETIO_CHANNEL", "flask-socketio")

    # Cơ sở tri thức: giới hạn từng tệp và tổng dung lượng MỖI trợ lý (mỗi bot có quota riêng)
    # Worker xử lý tài liệu chạy NGAY TRONG server web (python run.py) — không cần bật thêm tiến trình.
    # Production nên đặt false và chạy riêng `python -m workers.process_documents` để tách tải CPU.
    EMBEDDED_WORKER = os.environ.get("EMBEDDED_WORKER", "true").lower() == "true"
    KNOWLEDGE_MAX_FILE_MB = int(os.environ.get("KNOWLEDGE_MAX_FILE_MB", "5"))
    KNOWLEDGE_STORAGE_LIMIT_MB = int(os.environ.get("KNOWLEDGE_STORAGE_LIMIT_MB", "50"))
    # 1 request upload tối đa bằng quota của 1 bot (+1MB cho phần form) — chặn request khổng lồ tốn RAM
    MAX_CONTENT_LENGTH = (KNOWLEDGE_STORAGE_LIMIT_MB + 1) * 1024 * 1024
