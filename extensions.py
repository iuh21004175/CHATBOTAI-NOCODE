"""Mỗi client hạ tầng (MySQL, Realtime, Redis, ChromaDB, MinIO) tạo đúng 1 lần ở đây,
dùng chung toàn app qua application factory (app/__init__.py:create_app).
Không để blueprint/route tự mở kết nối riêng.
"""
import os

from dotenv import load_dotenv

# Phải load .env trước khi đọc bất kỳ os.environ.get() nào bên dưới — module này có thể được
# import trước config.py (vd. app/__init__.py import extensions trước khi nạp config.Config),
# nên load_dotenv() ở config.py là chưa đủ, phải gọi lại ở đây.
load_dotenv()

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from authlib.integrations.flask_client import OAuth
from flask_mail import Mail
import redis
import chromadb
from minio import Minio

db = SQLAlchemy()
migrate = Migrate()
socketio = SocketIO()
login_manager = LoginManager()
limiter = Limiter(key_func=get_remote_address)
oauth = OAuth()
mail = Mail()

oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID", ""),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET", ""),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Facebook không hỗ trợ OIDC discovery (không có server_metadata_url) như Google, nên phải khai
# báo tay 3 endpoint của Graph API.
oauth.register(
    name="facebook",
    client_id=os.environ.get("FACEBOOK_CLIENT_ID", ""),
    client_secret=os.environ.get("FACEBOOK_CLIENT_SECRET", ""),
    access_token_url="https://graph.facebook.com/v19.0/oauth/access_token",
    authorize_url="https://www.facebook.com/v19.0/dialog/oauth",
    api_base_url="https://graph.facebook.com/v19.0/",
    client_kwargs={"scope": "email public_profile"},
)

# Client thuần (không phải Flask extension) — khởi tạo lười (lazy) để import module này
# không tự kết nối ngay khi service chưa chạy; kết nối thật diễn ra ở lần gọi đầu tiên.
redis_client = redis.Redis.from_url(
    os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
)

chroma_client = chromadb.HttpClient(
    host=os.environ.get("CHROMA_HOST", "localhost"),
    port=int(os.environ.get("CHROMA_PORT", "8000")),
)

_minio_host = os.environ.get("MINIO_HOST", "localhost")
_minio_port = os.environ.get("MINIO_PORT", "9000")

minio_client = Minio(
    f"{_minio_host}:{_minio_port}",  # SDK minio nhận endpoint dạng "host:port" gộp, không nhận host/port tách riêng
    access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
    secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
    secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
)
