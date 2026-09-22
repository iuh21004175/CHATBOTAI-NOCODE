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
    # KHÔNG CÒN ĐƯỢC ĐỌC: engine dùng rag_distance_threshold (bên dưới). Giữ cột để migration/rollback không mất dữ liệu.
    min_similarity = db.Column(db.Float, nullable=False, default=0.25, server_default="0.25")
    # ---- Context & Response Decision Engine (xem core/context_engine). Mặc định khai báo ở đúng 1 nơi:
    # core/context_engine/settings.py:DEFAULTS — server_default dưới đây phải khớp (kiểm tra bằng test). ----
    config_tier = db.Column(
        db.Enum("basic", "advanced", "expert", name="bot_config_tier"), nullable=False, default="basic", server_default="basic"
    )  # quyết định trường nào được sửa ở Bước 1; trường không được sửa dùng mặc định (SettingsTier)
    rag_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")  # công tắc Knowledge Base (tier basic)
    recent_message_limit = db.Column(db.Integer, nullable=False, default=10, server_default="10")
    recent_token_limit = db.Column(db.Integer, nullable=False, default=2000, server_default="2000")
    summary_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    summary_trigger_tokens = db.Column(db.Integer, nullable=False, default=4000, server_default="4000")
    summary_max_tokens = db.Column(db.Integer, nullable=False, default=500, server_default="500")
    structured_memory_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    memory_max_items = db.Column(db.Integer, nullable=False, default=30, server_default="30")
    memory_min_confidence = db.Column(db.Float, nullable=False, default=0.70, server_default="0.70")
    intent_tracking_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    intent_confidence_threshold = db.Column(db.Float, nullable=False, default=0.70, server_default="0.70")
    slot_filling_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    slot_completion_threshold = db.Column(db.Float, nullable=False, default=0.80, server_default="0.80")
    rag_top_k = db.Column(db.Integer, nullable=False, default=8, server_default="8")
    rag_rerank_top_n = db.Column(db.Integer, nullable=False, default=5, server_default="5")
    # NGƯỠNG KHOẢNG CÁCH (bình phương L2 của Chroma, vector đã chuẩn hóa: d = 2·(1−cos)): chunk có d LỚN HƠN ngưỡng bị loại.
    # Thay thế min_similarity (cosine, cột cũ giữ lại nhưng không còn được engine đọc).
    rag_distance_threshold = db.Column(db.Float, nullable=False, default=1.50, server_default="1.50")
    rag_max_context_tokens = db.Column(db.Integer, nullable=False, default=3000, server_default="3000")
    max_candidate_count = db.Column(db.Integer, nullable=False, default=5, server_default="5")
    clarification_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    max_clarification_turns = db.Column(db.Integer, nullable=False, default=2, server_default="2")
    context_pressure_warning = db.Column(db.Float, nullable=False, default=0.80, server_default="0.80")
    context_pressure_hard_limit = db.Column(db.Float, nullable=False, default=0.90, server_default="0.90")
    low_confidence_reply_mode = db.Column(
        db.Enum("decline", "ask_clarify", name="low_confidence_reply_mode"),
        nullable=False,
        default="ask_clarify",
        server_default="ask_clarify",
    )
    low_confidence_decline_message = db.Column(db.Text, nullable=True)  # trống -> dùng câu mặc định theo ngôn ngữ
    low_confidence_clarify_message = db.Column(db.Text, nullable=True)
    max_context_tokens = db.Column(db.Integer, nullable=False, default=8000, server_default="8000")

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
    # Chỉ có ở tin của bot do Decision Engine tạo (NULL với tin khách/nhân viên và tin cũ)
    decision_trace = db.Column(db.JSON, nullable=True)
    # Usage của lệnh gọi DeepSeek CHÍNH của lượt (lệnh gọi phụ nằm trong decision_trace["extra_llm_calls"])
    usage_prompt_tokens = db.Column(db.Integer, nullable=True)
    usage_completion_tokens = db.Column(db.Integer, nullable=True)
    usage_cache_hit_tokens = db.Column(db.Integer, nullable=True)
    usage_cache_miss_tokens = db.Column(db.Integer, nullable=True)

    conversation = db.relationship("Conversation", back_populates="messages")


class ConversationState(db.Model):
    """Trạng thái hội thoại (1-1 với Conversation), tạo lười khi cần — xem core/context_engine/state.py."""

    __tablename__ = "conversation_state"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, unique=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    current_intent = db.Column(db.String(100), nullable=True)
    previous_intent = db.Column(db.String(100), nullable=True)
    intent_confidence = db.Column(db.Float, nullable=True)
    intent_changed = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    slots = db.Column(db.JSON, nullable=True)  # {"product": "...", "budget": null, ...}
    slot_completion = db.Column(db.Float, nullable=False, default=1.0, server_default="1")
    summary = db.Column(db.Text, nullable=True)
    summary_updated_at = db.Column(db.DateTime, nullable=True)
    last_summarized_message_id = db.Column(db.Integer, nullable=True)
    # Cờ việc nền: đặt khi các tin chưa tóm tắt vượt summary_trigger_tokens, worker (workers/context_jobs.py) xử lý rồi xóa
    summary_pending = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    clarification_turns_used = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StructuredMemory(db.Model):
    __tablename__ = "structured_memory"
    __table_args__ = (
        db.UniqueConstraint("conversation_id", "category", "mem_key", name="uq_structured_memory_item"),
    )

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    category = db.Column(
        db.Enum("requirement", "preference", "entity", "constraint", "confirmed_fact", name="memory_category"),
        nullable=False,
    )
    mem_key = db.Column(db.String(100), nullable=False)  # tên cột "key" là từ khóa SQL nên đặt mem_key
    value = db.Column(db.Text, nullable=False)
    confidence = db.Column(db.Float, nullable=False)
    source_message_id = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BotIntentConfig(db.Model):
    """Ý định (intent) do chủ bot khai báo — tùy chọn; bot không khai báo thì intent là nhãn tự do của AI."""

    __tablename__ = "bot_intent_config"
    __table_args__ = (db.UniqueConstraint("bot_id", "intent_name", name="uq_bot_intent_name"),)

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    intent_name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    required_slots = db.Column(db.JSON, nullable=True)  # ["product", "budget"]
    optional_slots = db.Column(db.JSON, nullable=True)


class ConversationMessageEmbedding(db.Model):
    """Đánh dấu tin nhắn đã được embed vào collection Chroma "history_<bot_id>" (vector nằm ở Chroma, không ở MySQL).
    Tin chưa có dòng ở đây = việc nền còn phải làm (worker quét, xem workers/context_jobs.py)."""

    __tablename__ = "conversation_message_embeddings"

    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=False, unique=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


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
