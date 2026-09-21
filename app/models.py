"""SQLAlchemy models — theo bảng dữ liệu trong tài liệu kiến trúc.
Mọi bảng nghiệp vụ đều gắn team_id để lọc theo nguyên tắc multi-tenant.
"""
from datetime import datetime

from extensions import db


class Team(db.Model):
    __tablename__ = "teams"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    plan = db.Column(db.String(50), default="free")  # gói cước
    plan_expires_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    members = db.relationship("TeamMember", back_populates="team")
    bots = db.relationship("Bot", back_populates="team")


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    # NULL với user chỉ đăng nhập qua Google (không có mật khẩu nội bộ)
    password_hash = db.Column(db.String(255), nullable=True)
    full_name = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    memberships = db.relationship("TeamMember", back_populates="user")

    # Flask-Login yêu cầu is_authenticated/is_active/is_anonymous/get_id
    @property
    def is_authenticated(self):
        return True

    @property
    def is_active(self):
        return True

    @property
    def is_anonymous(self):
        return False

    def get_id(self):
        return str(self.id)


class TeamMember(db.Model):
    __tablename__ = "team_members"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    role = db.Column(db.Enum("Owner", "Admin", "Member", name="team_role"), default="Member")

    team = db.relationship("Team", back_populates="members")
    user = db.relationship("User", back_populates="memberships")


class Bot(db.Model):
    __tablename__ = "bots"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    team = db.relationship("Team", back_populates="bots")
    settings = db.relationship("BotSettings", back_populates="bot", uselist=False)
    documents = db.relationship("Document", back_populates="bot")


class BotSettings(db.Model):
    __tablename__ = "bot_settings"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, unique=True)
    greeting = db.Column(db.Text)  # lời chào
    instructions = db.Column(db.Text)  # hướng dẫn/tính cách
    language = db.Column(db.String(20), default="vi")
    ai_model = db.Column(db.String(50), default="deepseek-chat")  # không còn dùng: model cố định trong core/llm_client.py
    temperature = db.Column(db.Float, default=0.7)
    max_tokens = db.Column(db.Integer, nullable=False, default=500, server_default="500")  # token đầu ra tối đa
    chunk_size = db.Column(db.Integer, nullable=False, default=450, server_default="450")  # token/chunk (Bước 2)
    chunk_overlap = db.Column(db.Integer, nullable=False, default=60, server_default="60")
    # Độ giống (cosine 0-1) tối thiểu để đoạn tài liệu được đưa cho AI (Bước 1); xem rag_engine.DEFAULT_MIN_SIMILARITY
    min_similarity = db.Column(db.Float, nullable=False, default=0.25, server_default="0.25")
    forward_to_staff = db.Column(db.Boolean, default=True)
    collect_customer_info = db.Column(db.Boolean, default=True)
    away_message = db.Column(db.Text)
    widget_domain = db.Column(db.String(255))  # domain website được phép nhúng Web Widget
    widget_icon = db.Column(db.String(20), nullable=False, default="chat", server_default="chat")  # key trong app/widget/icons.py, hoặc "custom" (xem widget_icon_path)
    widget_icon_path = db.Column(db.String(500), nullable=True)  # object key MinIO của ảnh icon tự tải lên khi widget_icon="custom"
    widget_color = db.Column(db.String(7), nullable=False, default="#1D4ED8", server_default="#1D4ED8")  # màu chủ đạo #RRGGBB
    widget_size = db.Column(db.Integer, nullable=False, default=56, server_default="56")  # đường kính nút chat (px)
    widget_shape = db.Column(db.String(10), nullable=False, default="round", server_default="round")  # round|rounded
    widget_position = db.Column(db.String(10), nullable=False, default="right", server_default="right")  # right|left
    widget_window = db.Column(db.String(4), nullable=False, default="md", server_default="md")  # sm|md|lg (cỡ khung chat)

    bot = db.relationship("Bot", back_populates="settings")


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(500), nullable=False)  # object key trong MinIO
    status = db.Column(
        db.Enum("pending", "processing", "trained", "failed", "draft", name="document_status"),
        default="draft",  # draft: đã tải lên, chờ người dùng cấu hình chunk rồi mới huấn luyện
    )
    chunk_size = db.Column(db.Integer, nullable=True)  # cấu hình chunk riêng của tài liệu; NULL = dùng mặc định của trợ lý
    chunk_overlap = db.Column(db.Integer, nullable=True)
    size_bytes = db.Column(db.Integer)
    chunk_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")  # số chunk đã ghi vào ChromaDB
    error_message = db.Column(db.Text, nullable=True)  # lý do khi status=failed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bot = db.relationship("Bot", back_populates="documents")


class Conversation(db.Model):
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=True)
    channel = db.Column(db.String(50), default="web_widget")  # web_widget/facebook/zalo/...
    visitor_id = db.Column(db.String(255))  # danh tính khách vãng lai (widget)
    status = db.Column(db.String(50), default="open")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    messages = db.relationship("Message", back_populates="conversation")


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False)
    sender = db.Column(db.Enum("customer", "bot", "staff", name="message_sender"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    conversation = db.relationship("Conversation", back_populates="messages")


class Followup(db.Model):
    __tablename__ = "followups"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, nullable=False)
    schedule_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(50), default="pending")  # pending/sent/cancelled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    name = db.Column(db.String(255))
    phone = db.Column(db.String(50))
    email = db.Column(db.String(255))
    # Giai đoạn chăm sóc: new (Mới) | lead (Tiềm năng) | won (Đã chốt); xem app/customers/service.STAGES
    stage = db.Column(db.String(20), nullable=False, default="new", server_default="new")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    conversations = db.relationship("Conversation", backref="customer_ref")


class ApiToken(db.Model):
    __tablename__ = "api_tokens"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    name = db.Column(db.String(255))
    token_hash = db.Column(db.String(255), nullable=False)  # hash, không lưu plaintext
    scopes = db.Column(db.JSON, default=list)  # ["Xem", "Tạo", "Cập nhật", "Xóa", "message"]
    expiry = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
