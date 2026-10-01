"""SQLAlchemy models — theo bảng dữ liệu trong tài liệu kiến trúc.
Mọi bảng nghiệp vụ đều gắn team_id để lọc theo nguyên tắc multi-tenant.
"""
import secrets
from datetime import datetime

from sqlalchemy import event

from extensions import db


def new_public_id() -> str:
    """Định danh CÔNG KHAI của bot (mã nhúng widget, URL /widget/api/...): ngẫu nhiên, không đoán/liệt kê được. `bots.id`
    tuần tự chỉ dùng nội bộ (khóa chính, khóa ngoại) — không bao giờ đưa ra ngoài."""
    return secrets.token_urlsafe(24)


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
    joined_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=True)  # NULL với thành viên có từ trước khi có cột này

    team = db.relationship("Team", back_populates="members")
    user = db.relationship("User", back_populates="memberships")


class TeamInvitation(db.Model):
    """Lời mời tham gia team qua email. Link mời mang token ký (itsdangerous) chứa id lời mời; chỉ dùng được khi lời mời còn,
    chưa hết hạn/chưa nhận, và người nhận đăng nhập bằng ĐÚNG email được mời. Hủy lời mời = xóa dòng."""

    __tablename__ = "team_invitations"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False)  # chữ thường
    role = db.Column(db.String(20), nullable=False, default="Member")  # Admin | Member (Owner chỉ được gán bởi Owner khác, không qua lời mời)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    accepted_at = db.Column(db.DateTime, nullable=True)

    team = db.relationship("Team")
    invited_by = db.relationship("User")


class Bot(db.Model):
    __tablename__ = "bots"

    id = db.Column(db.Integer, primary_key=True)
    # Định danh công khai (xem new_public_id): dùng ở mã nhúng và API widget công khai thay cho id tuần tự
    public_id = db.Column(db.String(64), unique=True, index=True, nullable=False, default=new_public_id)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    team = db.relationship("Team", back_populates="bots")
    settings = db.relationship("BotSettings", back_populates="bot", uselist=False)
    documents = db.relationship("Document", back_populates="bot")
    domains = db.relationship("BotDomain", back_populates="bot", order_by="BotDomain.id", cascade="all, delete-orphan")


class BotDomain(db.Model):
    """Domain website được phép nhúng widget của bot (1 bot có thể nhiều domain không cùng gốc; subdomain của 1 domain đã
    khai báo được chấp nhận tự động — xem app/widget/domains.py). domain lưu dạng đã chuẩn hóa (chữ thường, không www)."""

    __tablename__ = "bot_domains"
    __table_args__ = (db.UniqueConstraint("bot_id", "domain", name="uq_bot_domain"),)

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    bot = db.relationship("Bot", back_populates="domains")


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
    # Chế độ Bước 1: "basic" (mặc định) / "advanced" ("expert" là giá trị còn lại của enum cũ, coi như advanced) — xem
    # core/context_engine/settings.py:normalize_tier và docs/CONTEXT_ENGINE.md mục 6.
    config_tier = db.Column(
        db.Enum("basic", "advanced", "expert", name="bot_config_tier"), nullable=False, default="basic", server_default="basic"
    )
    # rag_enabled, summary_enabled, structured_memory_enabled, intent_tracking_enabled, slot_filling_enabled,
    # clarification_enabled: KHÔNG CÒN ĐƯỢC ĐỌC — engine dùng giá trị cố định (core/context_engine/settings.py:
    # FIXED_TOGGLES), không phụ thuộc các cột này nữa. Giữ cột để migration/rollback không mất dữ liệu.
    rag_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
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
    # KHÔNG CÒN ĐƯỢC ĐỌC: không còn trần số lượt hỏi làm rõ liên tiếp (AI Agent tự quyết định khi nào dừng hỏi).
    # Giữ cột để migration/rollback không mất dữ liệu.
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
    # ---- Phase D (AI Credit): trần chi phí/lượt chạy agent theo bot (VND, tính theo giá BÁN: đã gồm hệ số nền tảng) và câu hết Credit ----
    max_cost_per_execution_vnd = db.Column(db.Numeric(12, 2), nullable=False, default=1000, server_default="1000")
    out_of_credit_message = db.Column(db.Text, nullable=True)  # trống -> Config.DEFAULT_OUT_OF_CREDIT_MESSAGE
    # ---- Phase M (Website Action Engine): công tắc riêng cho hành động rủi ro cao nhất (thanh toán). MẶC ĐỊNH TẮT: khi tắt, action
    # risk_level='payment' không duyệt được và không bao giờ được giao cho agent (xem core/website_actions/risk.py). ----
    allow_agent_payment_actions = db.Column(db.Boolean, nullable=False, default=False, server_default="0")

    forward_to_staff = db.Column(db.Boolean, default=True)
    collect_customer_info = db.Column(db.Boolean, default=True)
    away_message = db.Column(db.Text)
    # KHÔNG CÒN ĐƯỢC ĐỌC: danh sách domain được phép nhúng nằm ở bảng bot_domains (BotDomain, nhiều domain/bot). Cột được giữ
    # (đã backfill sang bot_domains) để migration/rollback không mất dữ liệu.
    widget_domain = db.Column(db.String(255))
    widget_icon = db.Column(db.String(20), nullable=False, default="chat", server_default="chat")  # key trong app/widget/icons.py, hoặc "custom" (xem widget_icon_path)
    widget_icon_path = db.Column(db.String(500), nullable=True)  # object key MinIO của ảnh icon tự tải lên khi widget_icon="custom"
    widget_color = db.Column(db.String(7), nullable=False, default="#1D4ED8", server_default="#1D4ED8")  # màu chủ đạo #RRGGBB
    widget_size = db.Column(db.Integer, nullable=False, default=56, server_default="56")  # đường kính nút chat (px)
    widget_shape = db.Column(db.String(10), nullable=False, default="round", server_default="round")  # round|rounded
    widget_position = db.Column(db.String(10), nullable=False, default="right", server_default="right")  # right|left
    widget_window = db.Column(db.String(4), nullable=False, default="md", server_default="md")  # sm|md|lg (cỡ khung chat)

    # ---- Tối ưu tốc độ cảm nhận (xem docs/CONTEXT_ENGINE.md mục 10) — MẶC ĐỊNH BẬT cả 3, tắt được riêng từng kỹ thuật ----
    speed_progress_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")  # "Ảo giác lao động": dòng tiến trình thật
    speed_fillers_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")  # "Câu đệm": câu mồi hiện ngay khi khách gửi
    speed_async_enabled = db.Column(db.Boolean, nullable=False, default=True, server_default="1")  # "Trả lời bất đồng bộ": báo nhận + mở lại ô nhập khi lâu
    # Câu hiển thị cho từng bước tiến trình (trống = câu mặc định theo ngôn ngữ bot, xem app/widget/service.py:UI_TEXTS). Khoá đúng theo mã bước thật
    # mà agent/Flask phát ra (core/context_engine/agent/protocol.py:PROGRESS_* + STEP_ANALYZING) — không tự đặt khoá khác.
    speed_progress_text_analyzing = db.Column(db.Text, nullable=True)
    speed_progress_text_searching = db.Column(db.Text, nullable=True)
    speed_progress_text_acting = db.Column(db.Text, nullable=True)
    speed_progress_text_composing = db.Column(db.Text, nullable=True)

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


class AgentExecution(db.Model):
    """Mỗi lượt trả lời khách ở chế độ AI Agent = 1 dòng (kể cả lượt lỗi/hết giờ): số liệu vận hành của lượt chạy. Chi phí KHÔNG lưu ở đây
    (Phase D: đọc usage_* của tin bot / bảng chi phí riêng). job_id = mã lượt chạy phía worker (duy nhất, dùng để đối chiếu log)."""

    __tablename__ = "agent_executions"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(64), nullable=False, unique=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=True, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=True)  # tin bot được tạo ra (NULL nếu lượt lỗi)
    started_at = db.Column(db.DateTime, nullable=False)
    finished_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(30), nullable=False)  # completed | failed | timeout | max_iterations_reached | max_tool_calls_reached
    iterations_used = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    tool_calls_used = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    total_llm_calls = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    stop_reason = db.Column(db.String(100), nullable=True)
    error_message = db.Column(db.Text, nullable=True)


class ExecutionCost(db.Model):
    """Chi phí của 1 lượt chạy agent (1-1 với agent_executions; tách bảng để agent_executions không phình bởi chi tiết tài chính).
    total_cost_vnd = GIÁ VỐN (LLM + tool + hạ tầng); billed_vnd = giá bán = giá vốn × hệ số nền tảng (số Credit phải trừ);
    charged_vnd = số thực trừ (≤ số dư — số dư không bao giờ âm), uncollected_vnd = phần không thu được nếu số dư không đủ."""

    __tablename__ = "execution_costs"

    id = db.Column(db.Integer, primary_key=True)
    execution_id = db.Column(db.Integer, db.ForeignKey("agent_executions.id"), nullable=False, unique=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    llm_input_tokens = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    llm_output_tokens = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    llm_cache_hit_tokens = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    llm_cache_miss_tokens = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    llm_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    tool_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    infra_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    total_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    markup_multiplier = db.Column(db.Numeric(8, 4), nullable=False, default=1, server_default="1")
    billed_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    charged_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    uncollected_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    usage_reported = db.Column(db.Boolean, nullable=False, default=True, server_default="1")  # False: có lệnh gọi LLM không đo được usage
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class CreditAccount(db.Model):
    """Số dư AI Credit của team (1-1 với team). balance_vnd không bao giờ âm."""

    __tablename__ = "credit_accounts"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, unique=True)
    balance_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CreditTransaction(db.Model):
    """Sổ cái Credit: CHỈ THÊM, không sửa/xóa (chặn ở tầng ORM bên dưới). amount_vnd âm khi trừ, dương khi cộng;
    balance_after_vnd = số dư SAU giao dịch để đối soát không cần cộng dồn từ đầu."""

    __tablename__ = "credit_transactions"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    execution_id = db.Column(db.Integer, db.ForeignKey("agent_executions.id"), nullable=True, index=True)
    type = db.Column(
        db.Enum(
            "trial_grant", "execution_charge", "reserve", "release", "topup", "adjustment", "module_analysis_charge",
            "attachment_vision_charge", name="credit_transaction_type"
        ),
        nullable=False,
    )
    amount_vnd = db.Column(db.Numeric(14, 4), nullable=False)
    balance_after_vnd = db.Column(db.Numeric(14, 4), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    note = db.Column(db.String(255), nullable=True)


@event.listens_for(CreditTransaction, "before_update")
@event.listens_for(CreditTransaction, "before_delete")
def _credit_ledger_is_append_only(_mapper, _connection, _target):
    raise RuntimeError("credit_transactions là sổ cái chỉ-thêm: không được sửa hoặc xóa giao dịch (dùng giao dịch adjustment để điều chỉnh)")


class PaymentOrder(db.Model):
    """Đơn nạp Credit qua payOS. order_code là mã số nguyên gửi payOS (duy nhất). Credit chỉ được cộng vào sổ cái khi đơn chuyển sang
    'paid' (đúng 1 lần — xem app/payments/service.py:apply_paid); credit_vnd = amount_vnd (1đ = 1 AI Credit)."""

    __tablename__ = "payment_orders"

    id = db.Column(db.Integer, primary_key=True)
    order_code = db.Column(db.BigInteger, nullable=False, unique=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    amount_vnd = db.Column(db.BigInteger, nullable=False)
    credit_vnd = db.Column(db.Numeric(14, 4), nullable=False)
    status = db.Column(
        db.Enum("pending", "paid", "cancelled", "expired", "failed", name="payment_order_status"),
        nullable=False, default="pending", server_default="pending",
    )
    payment_link_id = db.Column(db.String(64), nullable=True)
    checkout_url = db.Column(db.String(500), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True)
    paid_at = db.Column(db.DateTime, nullable=True)
    note = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ---------------------------------------------------------------- Phase M: Website Action Engine
# Hạ tầng cho phép agent thao tác thật trên website của khách qua widget (xem docs/WEBSITE_ACTION_ENGINE.md). Server chỉ crawl + phân tích
# (worker nền, workers/module_analysis.py) và tạo kịch bản hành động; TRÌNH DUYỆT của khách (widget) mới là nơi thực thi trên DOM.

MODULE_URL_ROLES = ("product_listing", "product_detail", "cart", "checkout", "other")
MODULE_ACTION_TYPES = ("navigate", "click", "fill_form", "add_to_cart", "read_info")
MODULE_RISK_LEVELS = ("read_only", "cart", "payment")
MODULE_URL_UPLOAD_STATUSES = ("awaiting_upload", "uploaded", "extracted", "domain_confirmed", "failed")
# 'awaiting_domain_confirmation': đã phân tích xong (module_actions đã có) nhưng còn URL bắt buộc chưa qua bước xác nhận domain
# (module_urls.upload_status='domain_confirmed') — xem app/modules/service.py:_recompute_module_status. Không tự động -> 'ready'.
BOT_MODULE_STATUSES = ("pending", "analyzing", "awaiting_domain_confirmation", "ready", "failed")


class ModuleType(db.Model):
    """Danh mục LOẠI module (dữ liệu cấu hình, không phải dữ liệu đã crawl dùng chung): loại đầu tiên là 'sales_support'. Thêm loại mới =
    thêm dòng ở đây + module_type_url_roles, không đổi schema/code."""

    __tablename__ = "module_types"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), nullable=False, unique=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")

    url_roles = db.relationship("ModuleTypeUrlRole", back_populates="module_type", order_by="ModuleTypeUrlRole.display_order", cascade="all, delete-orphan")


class ModuleTypeUrlRole(db.Model):
    """Vai trò URL mà 1 loại module yêu cầu khai báo (nhãn hiển thị, bắt buộc hay tuỳ chọn) — server validate theo bảng này."""

    __tablename__ = "module_type_url_roles"
    __table_args__ = (db.UniqueConstraint("module_type_id", "url_role", name="uq_module_type_url_role"),)

    id = db.Column(db.Integer, primary_key=True)
    module_type_id = db.Column(db.Integer, db.ForeignKey("module_types.id"), nullable=False, index=True)
    url_role = db.Column(db.Enum(*MODULE_URL_ROLES, name="module_url_role"), nullable=False)
    label = db.Column(db.String(120), nullable=False)
    is_required = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    display_order = db.Column(db.Integer, nullable=False, default=0, server_default="0")

    module_type = db.relationship("ModuleType", back_populates="url_roles")


class BotModule(db.Model):
    """1 lần khách khai báo tích hợp cho 1 bot (mỗi bot tự khai báo và crawl riêng). analysis_* là chi phí lần phân tích gần nhất (giá vốn và
    số Credit thực thu — sổ cái credit_transactions type 'module_analysis_charge')."""

    __tablename__ = "bot_modules"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    module_type_id = db.Column(db.Integer, db.ForeignKey("module_types.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    status = db.Column(
        db.Enum(*BOT_MODULE_STATUSES, name="bot_module_status"), nullable=False, default="pending", server_default="pending"
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_analyzed_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    analysis_note = db.Column(db.Text, nullable=True)  # vd "bỏ 2 hành động vì selector không khớp trang" — minh bạch với chủ bot
    progress_done = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    progress_total = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    analysis_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")      # giá vốn LLM
    analysis_charged_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")   # Credit đã thu

    bot = db.relationship("Bot")
    module_type = db.relationship("ModuleType")
    urls = db.relationship("ModuleUrl", back_populates="module", order_by="ModuleUrl.id", cascade="all, delete-orphan")
    actions = db.relationship("ModuleAction", back_populates="module", order_by="ModuleAction.id", cascade="all, delete-orphan")


class ModuleUrl(db.Model):
    """1 trang của module — khai báo bằng cách TẢI LÊN file .zip trang đã lưu ("Webpage, Complete"), không còn crawl URL thật (M2 vá, xem
    docs/WEBSITE_ACTION_ENGINE.md). source_url là domain NGƯỜI DÙNG ĐÃ XÁC NHẬN (sau khi xem/sửa detected_domain) — CHỈ giá trị này được dùng để so
    khớp Origin của widget trước khi thực thi hành động (app/modules/dispatch.py, app/modules/runner.py); detected_domain chỉ là gợi ý tự động,
    không dùng cho việc so khớp bảo mật đó.
    """

    __tablename__ = "module_urls"

    id = db.Column(db.Integer, primary_key=True)
    module_id = db.Column(db.Integer, db.ForeignKey("bot_modules.id"), nullable=False, index=True)
    url_role = db.Column(db.Enum(*MODULE_URL_ROLES, name="module_url_role"), nullable=False, default="other", server_default="other")
    source_url = db.Column(db.String(255), nullable=True)      # domain đã XÁC NHẬN — dùng để so khớp bảo mật (M3); NULL cho tới khi xác nhận
    detected_domain = db.Column(db.String(255), nullable=True)  # domain hệ thống TỰ ĐOÁN từ nội dung file — chỉ để gợi ý + thống kê chất lượng
    upload_storage_key = db.Column(db.String(500), nullable=True)   # key file .zip GỐC (chưa giải nén) trên MinIO
    entry_html_filename = db.Column(db.String(255), nullable=True)  # tên file .html/.htm chính, xác định lúc giải nén (worker)
    upload_status = db.Column(
        db.Enum(*MODULE_URL_UPLOAD_STATUSES, name="module_url_upload_status"), nullable=False, default="awaiting_upload", server_default="awaiting_upload"
    )
    error_message = db.Column(db.Text, nullable=True)  # lỗi giải nén/phân tích RIÊNG url này (zip hỏng, thiếu file html chính, LLM lỗi...)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    extracted_at = db.Column(db.DateTime, nullable=True)  # lúc worker giải nén + phân tích xong (thay cho "crawled_at" bản gốc)

    module = db.relationship("BotModule", back_populates="urls")


class ModuleAction(db.Model):
    """1 hành động agent có thể thực hiện thay khách. verified=True chỉ sau khi (a) đạt ngưỡng confidence theo risk_level VÀ (b) chủ bot bấm duyệt
    (app/modules/service.py:approve_action) — không bao giờ tự bật. failure_reason: lần thực thi gần nhất báo selector không còn khớp."""

    __tablename__ = "module_actions"
    __table_args__ = (db.UniqueConstraint("module_id", "action_name", name="uq_module_action_name"),)

    id = db.Column(db.Integer, primary_key=True)
    module_id = db.Column(db.Integer, db.ForeignKey("bot_modules.id"), nullable=False, index=True)
    url_id = db.Column(db.Integer, db.ForeignKey("module_urls.id"), nullable=False, index=True)
    action_type = db.Column(db.Enum(*MODULE_ACTION_TYPES, name="module_action_type"), nullable=False)
    action_name = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    selector_spec = db.Column(db.JSON, nullable=False)
    confidence = db.Column(db.Float, nullable=False, default=0, server_default="0")
    risk_level = db.Column(db.Enum(*MODULE_RISK_LEVELS, name="module_risk_level"), nullable=False, default="read_only", server_default="read_only")
    verified = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    failure_reason = db.Column(db.String(50), nullable=True)
    failed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    module = db.relationship("BotModule", back_populates="actions")
    url = db.relationship("ModuleUrl")


class PendingWidgetAction(db.Model):
    """Hàng đợi lệnh đẩy xuống widget: backend không có DOM thật nên ghi lệnh + đẩy qua Socket.IO, widget thực thi rồi báo kết quả qua HTTP POST.
    token: bí mật của RIÊNG lệnh này (chỉ gửi qua kênh Socket.IO tới đúng khách) — route nhận kết quả đòi khớp. action_id NULL nếu hành động đã bị
    xoá sau đó (giữ lịch sử); action_name lưu sẵn để đọc lại."""

    __tablename__ = "pending_widget_actions"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=False, index=True)
    action_id = db.Column(db.Integer, db.ForeignKey("module_actions.id", ondelete="SET NULL"), nullable=True, index=True)
    action_name = db.Column(db.String(80), nullable=False)
    token = db.Column(db.String(64), nullable=False)
    params = db.Column(db.JSON, nullable=True)
    requires_confirm = db.Column(db.Boolean, nullable=False, default=False, server_default="0")
    status = db.Column(db.Enum("pending", "done", "failed", name="pending_widget_action_status"), nullable=False, default="pending", server_default="pending")
    result = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime, nullable=True)


ATTACHMENT_STATUSES = ("processing", "ready", "failed")


class MessageAttachment(db.Model):
    """Tệp khách gửi trong widget (module "Đọc tài liệu", xem docs/DOCUMENT_READER.md). Tệp gốc ở MinIO (storage_path); văn bản trích ra được cắt chunk + embed vào
    collection Chroma RIÊNG của bot (core/attachment_rag.py, KHÔNG lẫn vào Cơ sở tri thức) và chỉ dùng cho đúng hội thoại này.
    conversation_id/message_id NULL khi mới tải lên (khách có thể tải tệp trước khi có hội thoại/tin nhắn); được gắn vào lúc khách gửi tin.
    visitor_id: chủ của tệp — mọi truy cập của widget phải khớp (cùng cách bảo vệ hội thoại)."""

    __tablename__ = "message_attachments"

    id = db.Column(db.Integer, primary_key=True)
    bot_id = db.Column(db.Integer, db.ForeignKey("bots.id"), nullable=False, index=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey("conversations.id"), nullable=True, index=True)
    message_id = db.Column(db.Integer, db.ForeignKey("messages.id"), nullable=True, index=True)
    visitor_id = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    content_type = db.Column(db.String(100), nullable=True)
    size_bytes = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    storage_path = db.Column(db.String(500), nullable=False)
    status = db.Column(db.Enum(*ATTACHMENT_STATUSES, name="attachment_status"), nullable=False, default="processing", server_default="processing")
    error_message = db.Column(db.Text, nullable=True)  # lý do khi status=failed (tiếng Việt, hiện được cho khách)
    extract_method = db.Column(db.String(30), nullable=True)  # "text" (đọc trực tiếp) | "markitdown" | "vision" (DeepSeek vision — tốn Credit)
    char_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    chunk_count = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    truncated = db.Column(db.Boolean, nullable=False, default=False, server_default="0")  # văn bản trích ra vượt trần ATTACHMENT_MAX_TEXT_CHARS nên bị cắt
    vision_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")      # giá vốn LLM thật (0 nếu extract_method != "vision")
    vision_charged_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")   # Credit đã thu (xem app/credits/service.py)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    processed_at = db.Column(db.DateTime, nullable=True)


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


BUILDER_APP_STATUSES = ("spec_generating", "spec_ready", "generating", "ready", "failed")


class BuilderApp(db.Model):
    """Ứng dụng do người dùng tạo bằng App Builder (AB0, xem app/builder). `spec` là NGUỒN SỰ THẬT (collection + màn hình): schema DB và code giao diện
    đều sinh từ đây. Dữ liệu nghiệp vụ của app KHÔNG nằm trong database chính mà trong database MySQL riêng `db_name` (tạo lúc duyệt Spec).
    public_id: định danh trong URL chạy app (/apps/<public_id>/), không đoán được — `id` tuần tự chỉ dùng nội bộ."""

    __tablename__ = "builder_apps"

    id = db.Column(db.Integer, primary_key=True)
    team_id = db.Column(db.Integer, db.ForeignKey("teams.id"), nullable=False, index=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    public_id = db.Column(db.String(64), unique=True, nullable=False, default=new_public_id)
    name = db.Column(db.String(255), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    status = db.Column(db.Enum(*BUILDER_APP_STATUSES, name="builder_app_status"), nullable=False, default="spec_generating", server_default="spec_generating")
    error_message = db.Column(db.Text, nullable=True)  # lý do khi status=failed (hiện cho người dùng)
    spec = db.Column(db.JSON, nullable=True)
    db_name = db.Column(db.String(64), unique=True, nullable=True)  # NULL tới khi duyệt Spec
    storage_quota_mb = db.Column(db.Integer, nullable=False, default=500, server_default="500")
    current_version_id = db.Column(db.Integer, nullable=True)  # không FK (tránh vòng phụ thuộc với builder_app_versions)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class BuilderAppVersion(db.Model):
    """Một lần sinh code của app: bộ file tĩnh (path -> nội dung) + Spec lúc sinh + chi phí LLM thật (giá vốn VND, để đo chi phí ở AB0)."""

    __tablename__ = "builder_app_versions"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("builder_apps.id"), nullable=False, index=True)
    number = db.Column(db.Integer, nullable=False)
    spec = db.Column(db.JSON, nullable=False)
    files = db.Column(db.JSON, nullable=False)
    llm_calls = db.Column(db.JSON, nullable=True)  # usage từng lệnh gọi (LLMUsageTracker.calls) kèm kind
    llm_cost_vnd = db.Column(db.Numeric(14, 4), nullable=False, default=0, server_default="0")
    cost_reported = db.Column(db.Boolean, nullable=False, default=True, server_default="1")  # False: có lệnh gọi không đo được usage
    scan_warnings = db.Column(db.JSON, nullable=True)  # vi phạm quét tĩnh còn lại sau khi hết vòng tự sửa (rỗng khi sạch)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("app_id", "number", name="uq_builder_version_number"),)


class BuilderAppMember(db.Model):
    """Vai trò của 1 THÀNH VIÊN NHÓM trong 1 app (AB1): người dùng cuối của app chính là thành viên team sở hữu app (dùng lại đăng nhập + lời mời của team,
    không có hệ thống tài khoản thứ hai). role_id là id vai trò trong Spec (builder_apps.spec["roles"]) — Spec là nguồn sự thật nên không có bảng vai trò riêng.
    Chủ nhóm/Quản trị viên luôn toàn quyền và không cần dòng ở đây; thành viên (Member) KHÔNG có dòng = không thấy app."""

    __tablename__ = "builder_app_members"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("builder_apps.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    role_id = db.Column(db.String(40), nullable=False)
    assigned_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (db.UniqueConstraint("app_id", "user_id", name="uq_builder_member_app_user"),)


BUILDER_AUDIT_ACTIONS = ("create", "update", "delete")
BUILDER_AUDIT_SOURCES = ("ui", "chatbot")


class BuilderAppAudit(db.Model):
    """Nhật ký MỌI thao tác ghi dữ liệu app (AB1): ai, lúc nào, giá trị cũ/mới, qua giao diện hay chatbot (AB5). CHỈ THÊM — chặn sửa/xoá ở tầng ORM như sổ cái Credit.
    Nằm ở DB chính (không phải database riêng của app) nên người dùng app không có đường nào tự xoá dấu vết."""

    __tablename__ = "builder_app_audit"

    id = db.Column(db.Integer, primary_key=True)
    app_id = db.Column(db.Integer, db.ForeignKey("builder_apps.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    action = db.Column(db.Enum(*BUILDER_AUDIT_ACTIONS, name="builder_audit_action"), nullable=False)
    collection = db.Column(db.String(40), nullable=False)
    record_id = db.Column(db.BigInteger, nullable=False)
    old_values = db.Column(db.JSON, nullable=True)
    new_values = db.Column(db.JSON, nullable=True)
    source = db.Column(db.Enum(*BUILDER_AUDIT_SOURCES, name="builder_audit_source"), nullable=False, default="ui", server_default="ui")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


@event.listens_for(BuilderAppAudit, "before_update")
@event.listens_for(BuilderAppAudit, "before_delete")
def _builder_audit_is_append_only(_mapper, _connection, _target):
    raise RuntimeError("builder_app_audit là nhật ký chỉ-thêm: không được sửa hoặc xóa")
