import os
from decimal import Decimal

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

    # ---- AI Agent (Phase C): Harness chạy trong worker riêng (workers/agent_worker.py), xem core/context_engine/agent ----
    # Mặc định TẮT: engine dùng đúng 1 lệnh gọi DeepSeek/lượt như trước. Bật = agent tự quyết định tra cứu thêm và kết thúc bằng
    # finish_answer / ask_clarification / decline; cần chạy worker (python -m workers.agent_worker) hoặc EMBEDDED_AGENT_WORKER=true.
    AGENT_ENABLED = os.environ.get("AGENT_ENABLED", "false").lower() == "true"
    EMBEDDED_AGENT_WORKER = os.environ.get("EMBEDDED_AGENT_WORKER", "true").lower() == "true"  # run.py tự bật worker khi AGENT_ENABLED
    AGENT_REDIS_PREFIX = os.environ.get("AGENT_REDIS_PREFIX", "agent")  # đổi khi dev/test dùng chung 1 Redis
    # Mỗi tiến trình dsh ~130 MB RAM, chỉ tốn CPU khi đang chạy lượt; mặc định 1 để máy ít nhân vẫn ổn (các lượt xếp hàng tuần tự)
    AGENT_MAX_PROCESSES = int(os.environ.get("AGENT_MAX_PROCESSES", "1"))
    AGENT_IDLE_SECONDS = int(os.environ.get("AGENT_IDLE_SECONDS", "300"))  # tiến trình dsh rảnh quá lâu thì đóng để nhả RAM
    AGENT_MODEL = os.environ.get("AGENT_MODEL", "deepseek-v4-flash")  # id model phía Harness (khác tên "deepseek-flash" của llm_client)
    AGENT_INTERNAL_URL = os.environ.get("AGENT_INTERNAL_URL", "http://127.0.0.1:5000")  # Flask mà công cụ tra cứu gọi (loopback)
    AGENT_HOME = os.environ.get("AGENT_HOME", os.path.join(os.path.dirname(__file__), "var", "agent"))  # DSH_HOME + workspace + tệp ngữ cảnh
    AGENT_MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "3"))  # số lần tra cứu thêm tối đa mỗi lượt
    AGENT_MAX_ITERATIONS = int(os.environ.get("AGENT_MAX_ITERATIONS", "4"))  # số lượt suy luận (lệnh gọi LLM) tối đa mỗi lượt
    AGENT_MAX_RUNTIME_SECONDS = float(os.environ.get("AGENT_MAX_RUNTIME_SECONDS", "25"))
    # Cache kết quả tra cứu của công cụ agent theo (bot, câu tìm): 0 = tắt. Tự vô hiệu khi tài liệu của bot đổi (core/context_engine/agent/cache.py)
    AGENT_RAG_CACHE_SECONDS = int(os.environ.get("AGENT_RAG_CACHE_SECONDS", "300"))

    # ---- AI Credit (Phase D): đo chi phí + trừ Credit theo từng lượt chạy agent (app/credits, core/context_engine/execution_cost.py) ----
    # Đơn vị hiển thị cho khách là VND ("AI Credit"), không bao giờ là token. Chưa tích hợp cổng thanh toán (chưa có nạp Credit).
    TRIAL_CREDIT_VND = Decimal(os.environ.get("TRIAL_CREDIT_VND", "10000"))  # cấp 1 lần khi tạo team mới
    # Giá bán = giá vốn × hệ số nền tảng. PLACEHOLDER 1.0 (bán đúng giá vốn) cho tới khi có dữ liệu sử dụng thật để chốt hệ số.
    PLATFORM_MARKUP_MULTIPLIER = Decimal(os.environ.get("PLATFORM_MARKUP_MULTIPLIER", "1.0"))
    # Chi phí hạ tầng cố định tính vào mỗi lượt (VND). PLACEHOLDER 0 — chưa có số liệu để ước lượng.
    INFRA_COST_PER_EXECUTION_VND = Decimal(os.environ.get("INFRA_COST_PER_EXECUTION_VND", "0"))
    # Câu bot trả khi team hết Credit; chủ bot đổi được bằng bot_settings.out_of_credit_message (chưa có UI)
    DEFAULT_OUT_OF_CREDIT_MESSAGE = os.environ.get(
        "DEFAULT_OUT_OF_CREDIT_MESSAGE", "Bot hiện đã hết lượt trả lời trong hạn mức, vui lòng liên hệ quản trị viên."
    )
    # Câu bot trả khi ước tính chi phí lượt vượt bot_settings.max_cost_per_execution_vnd
    DEFAULT_MAX_COST_MESSAGE = os.environ.get(
        "DEFAULT_MAX_COST_MESSAGE", "Yêu cầu này vượt giới hạn xử lý của trợ lý, bạn vui lòng hỏi ngắn gọn hoặc cụ thể hơn."
    )

    # ---- Website Action Engine (Phase M): app/modules, core/website_actions, workers/module_analysis.py ----
    EMBEDDED_MODULE_WORKER = os.environ.get("EMBEDDED_MODULE_WORKER", "true").lower() == "true"  # run.py tự bật worker phân tích module
    MODULE_MAX_URLS = int(os.environ.get("MODULE_MAX_URLS", "6"))                   # số URL (file .zip) tối đa mỗi module
    # Khai báo module = tải lên .zip trang đã lưu ("Webpage, Complete"), không còn crawl URL thật (xem core/website_actions/zip_extract.py)
    MODULE_ZIP_MAX_BYTES = int(os.environ.get("MODULE_ZIP_MAX_BYTES", str(50 * 1024 * 1024)))  # cỡ file .zip tối đa
    MODULE_ZIP_MAX_FILES = int(os.environ.get("MODULE_ZIP_MAX_FILES", "500"))                    # số file tối đa bên trong 1 .zip
    # Chống zip bomb: tổng kích thước SAU khi giải nén (đọc từ ZipInfo, không cần giải nén thật) không được vượt trần này
    MODULE_ZIP_MAX_UNCOMPRESSED_BYTES = int(os.environ.get("MODULE_ZIP_MAX_UNCOMPRESSED_BYTES", str(200 * 1024 * 1024)))
    MODULE_LLM_INPUT_CHARS = int(os.environ.get("MODULE_LLM_INPUT_CHARS", "40000"))  # trần HTML rút gọn gửi DeepSeek mỗi URL
    MODULE_ANALYSIS_MAX_TOKENS = int(os.environ.get("MODULE_ANALYSIS_MAX_TOKENS", "3000"))
    # Hành động trên website trong 1 lượt agent: số lần gọi tối đa và thời gian chờ widget báo kết quả (giây; < AGENT_MAX_RUNTIME_SECONDS)
    AGENT_MAX_ACTION_CALLS = int(os.environ.get("AGENT_MAX_ACTION_CALLS", "2"))
    AGENT_ACTION_WAIT_SECONDS = float(os.environ.get("AGENT_ACTION_WAIT_SECONDS", "8"))

    # ---- Module "Đọc tài liệu" (app/attachments, core/doc_reader, core/attachment_rag.py): khách gửi tệp trong widget ----
    # md/txt/csv đọc trực tiếp (miễn phí); PDF/Word/Excel/PowerPoint do markitdown đọc (miễn phí, cài thẳng vào môi trường chính — không ép
    # phiên bản huggingface_hub/openai như MinerU trước đây nên không cần venv riêng, xem docs/DOCUMENT_READER.md); PDF dạng bản scan (không có
    # text layer) và ảnh (.png/.jpg/...) đọc bằng DeepSeek vision (core/doc_reader/vision_reader.py) — TỐN AI Credit thật, trừ qua
    # credit_transactions type 'attachment_vision_charge' giống hệt module_analysis_charge của Phase M.
    ATTACHMENT_MAX_BYTES = int(os.environ.get("ATTACHMENT_MAX_BYTES", str(10 * 1024 * 1024)))
    ATTACHMENT_MAX_PER_MESSAGE = int(os.environ.get("ATTACHMENT_MAX_PER_MESSAGE", "3"))
    ATTACHMENT_MAX_PER_CONVERSATION = int(os.environ.get("ATTACHMENT_MAX_PER_CONVERSATION", "6"))  # tệp còn dùng được trong 1 hội thoại
    ATTACHMENT_MAX_TEXT_CHARS = int(os.environ.get("ATTACHMENT_MAX_TEXT_CHARS", "300000"))  # trần văn bản trích ra từ 1 tệp (quá thì cắt + báo)
    ATTACHMENT_TOP_K = int(os.environ.get("ATTACHMENT_TOP_K", "6"))  # số đoạn tệp gần câu hỏi nhất đưa vào ngữ cảnh mỗi lượt
    ATTACHMENT_STALE_SECONDS = float(os.environ.get("ATTACHMENT_STALE_SECONDS", "900"))  # quá hạn này mà còn "processing" = tiến trình đã chết
    VISION_MAX_TOKENS = int(os.environ.get("VISION_MAX_TOKENS", "2000"))        # trần token trả lời của 1 lệnh gọi DeepSeek vision
    VISION_IMAGE_TOKENS = int(os.environ.get("VISION_IMAGE_TOKENS", "1024"))    # token quy đổi tối đa cho 1 ảnh (theo tài liệu DeepSeek vision)
    VISION_MAX_PDF_PAGES = int(os.environ.get("VISION_MAX_PDF_PAGES", "20"))    # trần số trang đọc bằng vision cho 1 PDF dạng bản scan
    VISION_CONCURRENCY = int(os.environ.get("VISION_CONCURRENCY", "2"))        # số lệnh gọi vision đồng thời trong 1 tiến trình web; tệp khác xếp hàng

    # ---- Nạp Credit qua payOS (app/payments, core/payos_client.py) ----
    # Khóa lấy từ trang quản trị payOS, CHỈ đặt trong .env (không commit). Thiếu 1 trong 3 -> trang /profile hiện nút Nạp Credit ở trạng thái vô hiệu.
    PAYOS_CLIENT_ID = os.environ.get("PAYOS_CLIENT_ID", "")
    PAYOS_API_KEY = os.environ.get("PAYOS_API_KEY", "")
    PAYOS_CHECKSUM_KEY = os.environ.get("PAYOS_CHECKSUM_KEY", "")
    PAYOS_API_BASE = os.environ.get("PAYOS_API_BASE", "https://api-merchant.payos.vn")
    # Địa chỉ công khai của web (returnUrl/cancelUrl gửi payOS). Trống -> lấy theo request hiện tại.
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    # Gói nạp theo bảng giá (VND; 1đ = 1 AI Credit). Gói "1.000.000+": khách nhập số tiền tự do, tối thiểu TOPUP_CUSTOM_MIN_VND, bội số TOPUP_STEP_VND.
    TOPUP_PACKAGES_VND = (50_000, 200_000, 500_000)
    TOPUP_CUSTOM_MIN_VND = 1_000_000
    TOPUP_MAX_VND = 100_000_000
    TOPUP_STEP_VND = 1_000
    PAYOS_LINK_EXPIRE_MINUTES = int(os.environ.get("PAYOS_LINK_EXPIRE_MINUTES", "15"))

    # ---- App Builder (AB0): app/builder — LLM sinh Spec + code HTML/jQuery, dữ liệu mỗi app nằm trong 1 database MySQL riêng ----
    # Tài khoản MySQL CÓ QUYỀN tạo database/user (CHỈ dùng để cấp phát database cho app — tách khỏi DATABASE_URL của app chính). Mặc định
    # trùng DATABASE_URL mặc định (root XAMPP, dev); production PHẢI đặt riêng trong .env. Mật khẩu từng database app suy ra từ SECRET_KEY
    # (HMAC) nên không lưu ở đâu — đổi SECRET_KEY = app đã tạo mất kết nối dữ liệu.
    TENANT_DB_ADMIN_URL = os.environ.get("TENANT_DB_ADMIN_URL", "mysql+pymysql://root:@localhost:3306/")
    TENANT_DB_USER_HOST = os.environ.get("TENANT_DB_USER_HOST", "localhost")  # phần @host của user MySQL từng app (% nếu MySQL ở máy khác)
    TENANT_DB_QUOTA_MB = int(os.environ.get("TENANT_DB_QUOTA_MB", "500"))      # hạn mức miễn phí mỗi app; vượt = chặn ghi (xoá vẫn được)
    BUILDER_MAX_APPS_PER_TEAM = int(os.environ.get("BUILDER_MAX_APPS_PER_TEAM", "5"))
    BUILDER_MAX_COLLECTIONS = int(os.environ.get("BUILDER_MAX_COLLECTIONS", "12"))
    BUILDER_MAX_FIELDS = int(os.environ.get("BUILDER_MAX_FIELDS", "30"))       # field mỗi collection
    BUILDER_MAX_SCREENS = int(os.environ.get("BUILDER_MAX_SCREENS", "12"))
    BUILDER_MAX_FIX_ROUNDS = int(os.environ.get("BUILDER_MAX_FIX_ROUNDS", "2"))  # số vòng LLM tự sửa mỗi màn hình khi quét tĩnh báo lỗi
    BUILDER_SPEC_MAX_TOKENS = int(os.environ.get("BUILDER_SPEC_MAX_TOKENS", "4000"))
    BUILDER_SCREEN_MAX_TOKENS = int(os.environ.get("BUILDER_SCREEN_MAX_TOKENS", "5000"))
    BUILDER_PROMPT_MAX_CHARS = int(os.environ.get("BUILDER_PROMPT_MAX_CHARS", "4000"))
    BUILDER_STALE_SECONDS = float(os.environ.get("BUILDER_STALE_SECONDS", "900"))  # "đang sinh" quá hạn này = tiến trình nền đã chết
    BUILDER_API_MAX_PER_PAGE = int(os.environ.get("BUILDER_API_MAX_PER_PAGE", "100"))
