# Báo cáo kỹ thuật — Ứng dụng quản lý Chatbot AI (No-code)

> **Ngày lập:** 2026-09-19 · **Cập nhật gần nhất:** 2026-09-22 — thay toàn bộ Pha B bằng **Context & Response Decision Engine**
> (xem [CONTEXT_ENGINE.md](CONTEXT_ENGINE.md)) và Bước 1 có thêm **chi phí ước tính mỗi câu hỏi** (xem [BANG_GIA_API_AI.md](BANG_GIA_API_AI.md)).
> **Phạm vi:** toàn bộ mã nguồn trong repo tại thời điểm viết · **Người viết:** Claude Code (đọc trực tiếp source, chạy kiểm thử và đo đạc trên máy phát triển)
>
> Tài liệu này dành cho chủ dự án cần nắm **hệ thống thực sự làm gì, dùng công nghệ gì, chạy ra sao, tốn tài nguyên bao nhiêu và đâu là rủi ro**. Mọi sơ đồ dùng **Mermaid** — GitHub hiển thị trực tiếp, không cần cài thêm gì.
>
> **Phạm vi của lần cập nhật 2026-09-22:** đối chiếu lại mã nguồn cho mọi phần liên quan tới **Context & Response Decision Engine**
> (thay thế luồng RAG cũ mô tả trước đây ở mục 4.3) và giao diện Bước 1 vừa sửa (gộp card cấu hình, tooltip, chi phí ước tính). Số đo
> phần cứng ở [mục 6](#6-tài-nguyên-phần-cứng) và phần hạ tầng/triển khai ở [mục 7](#7-cài-đặt-triển-khai-và-đưa-lên-github) **không**
> đo lại lần này (không đổi so với 2026-09-19) trừ chỗ ghi rõ. Vài route quản trị mới xuất hiện trong `app/dashboard/routes.py`/
> `app/widget/routes.py` ngoài phạm vi Context Engine (`/inbox`, `/customers`, `/customers/export.csv`, `/bots/<id>/publish/icon`,
> `/widget/api/<id>/icon`, `/widget/api/<id>/staff-messages`) **chưa được kiểm tra kỹ trong lần cập nhật này** — [Phụ lục A](#phụ-lục-a--bảng-route-đầy-đủ) chỉ đối chiếu lại các route thuộc Bước 1.

**Quy ước độ tin cậy** (dùng xuyên suốt tài liệu):

| Nhãn | Ý nghĩa |
| --- | --- |
| **[CODE]** | Đọc trực tiếp từ mã nguồn, có dẫn chiếu `file:dòng` |
| **[ĐO]** | Đo thật trên máy phát triển ngày 2026-09-19 (trừ khi ghi rõ ngày khác) |
| **[TEST]** | Đã chạy kiểm thử tự động và đạt |
| **[ƯỚC TÍNH]** | Suy luận từ kiến trúc/số đo, **chưa** đo trực tiếp — cần kiểm chứng trước khi dựa vào |

**Ký hiệu trạng thái chức năng:** ✅ hoàn chỉnh · 🟡 làm một phần · ⬜ mới có khung/trang giữ chỗ.

---

## Mục lục

1. [Tóm tắt điều hành](#1-tóm-tắt-điều-hành)
2. [Công nghệ và thư viện đã sử dụng](#2-công-nghệ-và-thư-viện-đã-sử-dụng)
3. [Kiến trúc tổng thể](#3-kiến-trúc-tổng-thể)
4. [**Luồng xử lý lõi (RAG)**](#4-luồng-xử-lý-lõi-rag)
5. [Chức năng giao diện — chi tiết từng màn hình](#5-chức-năng-giao-diện--chi-tiết-từng-màn-hình)
6. [Tài nguyên phần cứng](#6-tài-nguyên-phần-cứng)
7. [Cài đặt, triển khai và đưa lên GitHub](#7-cài-đặt-triển-khai-và-đưa-lên-github)
8. [**Rủi ro và nợ kỹ thuật**](#8-rủi-ro-và-nợ-kỹ-thuật)
9. [Lộ trình xử lý đề xuất](#9-lộ-trình-xử-lý-đề-xuất)
10. [Phụ lục](#10-phụ-lục)

---

## 1. Tóm tắt điều hành

### 1.1 Sản phẩm là gì

Nền tảng cho phép một doanh nghiệp (**Team**) tạo nhiều **trợ lý AI (Bot)**, nạp tài liệu riêng của mình (TXT/MD/CSV), rồi nhúng một khung chat vào website bằng đúng **một thẻ `<script>`**. Khi khách hỏi, hệ thống tìm đoạn tài liệu liên quan nhất rồi nhờ mô hình ngôn ngữ (DeepSeek) soạn câu trả lời — kỹ thuật này gọi là **RAG** (Retrieval-Augmented Generation: *truy xuất rồi mới sinh câu trả lời*).

Trợ lý được dựng qua **4 bước**: `1 Thiết lập → 2 Cơ sở tri thức → 3 Xuất bản → 4 Lịch sử chat`.

### 1.2 Hai luồng lõi (chi tiết ở [mục 4](#4-luồng-xử-lý-lõi-rag))

```mermaid
flowchart LR
    subgraph A["PHA A — Nạp tri thức (chạy nền, tốn CPU)"]
        A1["Upload tệp"] --> A2["Chuẩn hóa sang markdown"]
        A2 --> A3["Xem trước và chọn cấu hình chunk"]
        A3 --> A4["Cắt chunk theo token"]
        A4 --> A5["Embedding: chunk thành vector 1024 chiều"]
        A5 --> A6[("ChromaDB<br/>1 collection mỗi bot")]
    end
    subgraph B["PHA B — Context & Response Decision Engine (realtime)"]
        B1["Khách gõ câu hỏi<br/>trên widget"] --> B2["Bước A (không LLM):<br/>chọn tin gần đây + summary + memory<br/>RAG (khoảng cách, candidate_count)<br/>tính áp lực ngữ cảnh, nén nếu cần"]
        B2 --> B3["Bước B: ĐÚNG 1 lệnh gọi DeepSeek<br/>JSON có cấu trúc (intent, slots, memory,<br/>proposed_answer/clarification)"]
        B3 --> B4["Bước C (không LLM):<br/>cây quyết định ANSWER / CLARIFY / DECLINE"]
        B4 --> B5{"needs_history_lookup?"}
        B5 -- "hiếm" --> B5b["Bước F: tìm lại lịch sử<br/>cùng hội thoại (lệnh gọi LLM phụ)"]
        B5 -- "không" --> B6
        B5b --> B6["Bước D: lưu tin bot + decision_trace<br/>+ usage, cập nhật state/memory"]
    end
    A6 -. "collection bot_&lt;id&gt;" .-> B2
```

> Chi tiết đầy đủ (6 pha A–F, cấu hình theo tier, chi phí) ở [mục 4.3](#43-pha-b--context--response-decision-engine) và
> [CONTEXT_ENGINE.md](CONTEXT_ENGINE.md).

### 1.3 Mức độ hoàn thiện

| Nhóm | Trạng thái | Ghi chú |
| --- | --- | --- |
| Đăng nhập/Đăng ký (mật khẩu, Magic Link, Google, Facebook) | ✅ | Google/Facebook cần khóa OAuth; Magic Link cần SMTP |
| Bảng điều khiển, tạo trợ lý | ✅ | Không có xóa trợ lý |
| Bước 1 — Thiết lập (11 mẫu trợ lý, trình soạn chỉ dẫn markdown, nút Tối ưu, cấu hình Context & Response Decision Engine theo 3 tier, chi phí ước tính) | 🟡 | Còn **3 công tắc chưa có tác dụng** (chuyển tiếp nhân viên, thu thập thông tin, tin vắng mặt — [R17](#r-nhom-b)); ô chọn model đã bỏ ([R16](#r-nhom-b)) |
| Bước 2 — Cơ sở tri thức + cấu hình chunk riêng từng tệp | ✅ | Chỉ TXT/MD/CSV; PDF/DOCX chưa hỗ trợ |
| Bước 3 — Xuất bản Web Widget | ✅ | Facebook/Zalo/WhatsApp chỉ là thẻ "đang phát triển" |
| Bước 4 — Lịch sử chat | 🟡 | Chỉ xem; không trả lời thủ công |
| Web Widget công khai | ✅ | Có rủi ro chi phí ([R5](#r-nhom-a)) |
| Inbox, FollowUp, Khách hàng, Báo cáo, API Tokens, Hồ sơ | ⬜ | Trang "đang xây dựng"; API `/api/*` là khung trả HTTP 500 |
| Kiểm thử tự động trong repo | ⬜ | Không có tệp test nào |

### 1.4 Những điều cần biết ngay

1. **Chưa an toàn để mở ra Internet.** `SECRET_KEY` trong `.env` đang trùng khóa mặc định công khai; `run.py` chạy `debug=True` trên `0.0.0.0`; Redis/ChromaDB/MinIO mở cổng không mật khẩu. Xem [R1–R4](#r-nhom-a).
2. **Điểm nghẽn thật là CPU, không phải RAM/đĩa.** Mô hình embedding 2,13 GiB chạy trên CPU; huấn luyện 1 MB tài liệu ước tính mất **hàng phút đến hàng chục phút**, và trong lúc đó chat cũng chậm đi. Xem [mục 6](#6-tài-nguyên-phần-cứng).
3. **Đã xử lý [R15], nay đi xa hơn nữa:** bot từng luôn nhét top 5 đoạn gần nhất dù lạc đề, dễ "trả lời bừa". Luồng trả lời hiện tại là **Context & Response Decision Engine** (thay hẳn `search`/`build_prompt`/`answer` cũ): lọc theo **ngưỡng khoảng cách** (`rag_distance_threshold`, mặc định 1,50 ≡ cosine 0,25 — cùng ngưỡng đã đo cho `min_similarity` trước đây), cộng thêm bộ nhớ hội thoại có cấu trúc, tóm tắt tự động, theo dõi ý định/thông tin bắt buộc và cây quyết định ANSWER/CLARIFY/DECLINE tường minh; không đoạn nào đạt ngưỡng thì AI **không** tự trả lời mà dùng câu chủ bot cấu hình sẵn. Xem [mục 4.3](#43-pha-b--context--response-decision-engine), [CONTEXT_ENGINE.md](CONTEXT_ENGINE.md) và [R15](#r-nhom-b).
4. **Thư mục `models/` nặng 2,13 GiB không được ignore** — không thể đẩy lên GitHub như hiện tại ([R41](#r-nhom-e)).
5. **`workers/send_followups.py` đánh dấu "đã gửi" mà không gửi gì** ([R35](#r-nhom-d)) — chưa nguy hiểm vì chưa có UI tạo FollowUp, nhưng không được chạy theo lịch.
6. **Lỗi nghiêm trọng đã phát hiện khi chạy thử thật và đã sửa ([R42](#r-nhom-c)):** trên môi trường này, **mọi lệnh gọi DeepSeek từ server (`python run.py`) đều lỗi** `RecursionError` (xung đột `eventlet` × `truststore`), nên trước đây chat, chat thử và nút Tối ưu **chưa từng chạy được** trong server (DB xác nhận: 0 hội thoại từng được ghi). Chạy riêng lẻ ngoài server thì bình thường nên bộ test cũ không phát hiện.

---

## 2. Công nghệ và thư viện đã sử dụng

### 2.1 Hạ tầng (dịch vụ phải chạy sẵn)

| Dịch vụ | Phiên bản thực tế | Chạy ở đâu (máy dev) | Vai trò trong hệ thống |
| --- | --- | --- | --- |
| **MariaDB** (tương thích MySQL) | 10.4.32 **[ĐO]** | `mysqld` chạy cục bộ trên Windows (có vẻ là bản kèm XAMPP — chưa xác minh), cổng 3306 | Dữ liệu nghiệp vụ: team, user, bot, cài đặt, tài liệu, hội thoại, tin nhắn |
| **Redis** | `redis:latest` | Docker, cổng 6379 | (1) Rate limit; (2) cầu nối Socket.IO web↔worker; (3) khóa phân tán của worker; (4) đánh dấu Magic Link đã dùng |
| **ChromaDB** (server) | **1.0.0** **[ĐO]** | Docker `chromadb/chroma`, cổng 8000, dữ liệu ở `C:\chromadb\data` | Kho vector: nội dung chunk + vector embedding |
| **MinIO** (tương thích S3) | `quay.io/minio/minio` | Docker, cổng 9000/9001, volume `minio-data` | Lưu **tệp gốc** người dùng upload |
| **DeepSeek API** | `deepseek-flash` (cố định trong code) | Dịch vụ ngoài (Internet) | Sinh câu trả lời |
| **SMTP** (Gmail) | smtp.gmail.com:587 TLS | Dịch vụ ngoài | Gửi email Magic Link |
| **Google / Facebook OAuth** | OIDC / Graph API v19.0 | Dịch vụ ngoài | Đăng nhập mạng xã hội |

> ⚠️ **Lệch phiên bản đáng chú ý [ĐO]:** thư viện client `chromadb` cài **1.5.9** nhưng server Docker là **1.0.0** — khác phiên bản chính. Hiện chạy được, nhưng `requirements.txt` không ghim phiên bản nên rất dễ vỡ khi nâng cấp ([R22](#r-nhom-b)).

### 2.2 Thư viện Python (backend)

Ngôn ngữ: **Python 3.13** (theo đường dẫn môi trường ảo). Phiên bản dưới đây là bản **đang cài** trong `env/` **[ĐO]**.

| Thư viện | Phiên bản | Ghim trong requirements? | Vai trò | Dùng ở đâu |
| --- | --- | --- | --- | --- |
| **Flask** | 3.1.3 | Có | Web framework, application factory, blueprint | toàn bộ `app/` |
| **Flask-SQLAlchemy** (+ SQLAlchemy 2.0.54) | 3.1.1 | Có | ORM truy cập MariaDB | `app/models.py`, mọi `service.py` |
| **Flask-Migrate** (+ Alembic 1.20.0) | 4.0.7 | Có | Quản lý phiên bản schema DB | `migrations/` |
| **PyMySQL** | 1.1.1 | Có | Driver kết nối MySQL/MariaDB | `config.py` (`mysql+pymysql://`) |
| **Flask-Login** | 0.6.3 | Có | Trạng thái đăng nhập, `@login_required` | `app/auth`, mọi trang quản trị |
| **Flask-SocketIO** (+ python-socketio 5.17.0) | 5.6.1 | Có | WebSocket: đẩy trạng thái huấn luyện tài liệu | `app/dashboard/events.py`, `workers/` |
| **eventlet** | 0.38.0 | Có | Máy chủ bất đồng bộ (greenlet) cho Socket.IO | `run.py`, `core/rag_engine.py` |
| **Flask-Limiter** | 3.9.2 | Có | Giới hạn tần suất (lưu ở Redis) | `app/auth`, `app/widget`, `app/dashboard` |
| **Flask-Mail** | 0.10.0 | Có | Gửi email Magic Link qua SMTP | `app/auth/service.py` |
| **Authlib** | 1.3.2 | Có | Đăng nhập Google (OIDC) và Facebook (OAuth2) | `extensions.py`, `app/auth/routes.py` |
| **redis** (redis-py) | 5.2.1 | Có | Client Redis | `extensions.py`, `workers/`, `app/auth` |
| **minio** | 7.2.12 | Có | Client MinIO/S3 | `core/storage_service.py` |
| **chromadb** | client 1.5.9 | **Không ghim** | Client ChromaDB (HTTP) | `extensions.py`, `core/rag_engine.py` |
| **langchain-deepseek** | 1.1.0 | **Không ghim** | Gọi DeepSeek (`ChatDeepSeek`) | `core/llm_client.py` |
| **langchain-text-splitters** | 1.1.2 | **Không ghim** | `RecursiveCharacterTextSplitter` — cắt khối quá dài | `core/rag_engine.py` |
| **transformers** | 4.57.6 | **Không ghim** | **Chỉ dùng tokenizer** (`AutoTokenizer`) để đếm token thật | `core/rag_engine.py` |
| **onnxruntime** | 1.30.0 | **Không ghim** | Chạy mô hình embedding trên CPU | `core/rag_engine.py` |
| **numpy** | 2.5.3 | Không (phụ thuộc gián tiếp) | Chuẩn hóa vector | `core/rag_engine.py` |
| **python-dotenv** | 1.0.1 | Có | Nạp `.env` | `config.py`, `extensions.py` |
| **Werkzeug** 3.1.8 / **itsdangerous** 2.2.0 | (đi kèm Flask) | Không | Băm mật khẩu; ký token Magic Link | `app/auth/service.py` |

**Khai báo trong `requirements.txt` nhưng không được import ở bất kỳ đâu trong `app/`, `core/`, `workers/` [CODE]:** `langchain` (1.4.1), `langchain-experimental` (0.4.2), `gunicorn` (23.0.0). (Chưa xóa — xem [R32](#r-nhom-c).)

**Có trong môi trường ảo nhưng không có trong `requirements.txt` [ĐO]:** `torch` (502 MB), `scipy`, `scikit-learn`, `sympy`, `onnx`… — nguồn gốc không rõ, mã nguồn không import `torch`.

### 2.3 Mô hình AI

| Mô hình | Vai trò | Thông số | Nơi lưu |
| --- | --- | --- | --- |
| **AITeamVN/Vietnamese_Embedding** (bản xuất ONNX, fp32) | Biến văn bản → vector | Kiến trúc XLM-RoBERTa, **24 tầng, hidden 1024** → vector **1024 chiều**; giới hạn dùng 2048 token/lần; tệp **2,13 GiB** | `models/Vietnamese_Embedding/` (chạy hoàn toàn cục bộ, không gọi API) |
| **DeepSeek** (`deepseek-flash`, **cố định** ở `core/llm_client.py`, người dùng không chọn) | Sinh câu trả lời và tối ưu chỉ dẫn | Gọi qua LangChain `ChatDeepSeek`; **tắt thinking mode** (`extra_body`); `temperature` và `max_tokens` lấy theo từng bot | Dịch vụ ngoài |

### 2.4 Frontend

| Thành phần | Công nghệ | Ghi chú |
| --- | --- | --- |
| Render trang | **Jinja2** (server-side), tự động escape HTML | 14 template |
| Tương tác | **JavaScript thuần** (không framework, không bundler) | Nằm ngay trong template + `embed.js` |
| Giao diện | **CSS thuần** (5 tệp trong `app/static/css/`) | Không Tailwind/Bootstrap |
| Realtime | **Socket.IO client 4.7.5** (bản nhúng sẵn `app/static/js/socket.io.min.js`) | Trạng thái huấn luyện tài liệu |
| Phông chữ | **Google Fonts** (Be Vietnam Pro) qua CDN | Phụ thuộc Internet phía người dùng |
| Widget nhúng | `app/widget/embed.js` — **Shadow DOM**, không phụ thuộc thư viện | Xem [mục 5.8](#58-web-widget-công-khai) |

### 2.5 Công cụ phát triển

- **Alembic** — 13 phiên bản migration ([mục 3.7](#37-các-migration)).
- `scripts/seed_admin.py` — tạo tài khoản demo `admin@example.com`.
- `templates/*.zip` — 14 bản thiết kế giao diện (HTML demo) làm tài liệu tham chiếu; không được app sử dụng.
- **Không có:** bộ test tự động, CI/CD, Dockerfile cho chính ứng dụng, cấu hình logging.

---

## 3. Kiến trúc tổng thể

### 3.1 Sơ đồ thành phần

```mermaid
flowchart TB
    subgraph Browser["Trình duyệt"]
        Admin["Trang quản trị<br/>(Jinja2 + JS thuần)"]
        Widget["Widget trên website khách<br/>(embed.js, Shadow DOM)"]
    end

    subgraph Proc["1 tiến trình Python: python run.py (eventlet)"]
        Flask["Flask app<br/>blueprints: auth, dashboard, widget, ..."]
        SIO["Flask-SocketIO"]
        Worker["Worker nền (greenlet)<br/>vòng lặp quét tài liệu pending"]
        RAG["core/rag_engine<br/>chunk, embed, search, prompt"]
        ONNX["ONNX Runtime + tokenizer<br/>Vietnamese_Embedding 2,13 GiB"]
        Pool["Luồng hệ điều hành thật<br/>(eventlet.tpool)"]
    end

    DB[("MariaDB<br/>dữ liệu nghiệp vụ")]
    Redis[("Redis<br/>rate limit, pub/sub, khóa")]
    Chroma[("ChromaDB<br/>vector, 1 collection/bot")]
    MinIO[("MinIO<br/>tệp gốc")]
    DeepSeek["DeepSeek API"]
    SMTP["SMTP Gmail"]
    OAuth["Google / Facebook"]

    Admin -->|"HTTP + WebSocket"| Flask
    Admin <-->|"trạng thái huấn luyện"| SIO
    Widget -->|"HTTP JSON, công khai"| Flask
    Flask --> DB
    Flask --> Redis
    Flask --> RAG
    Flask --> MinIO
    Flask --> SMTP
    Flask --> OAuth
    SIO <--> Redis
    Worker --> DB
    Worker --> MinIO
    Worker --> RAG
    Worker -->|"emit trạng thái"| Redis
    RAG --> Pool --> ONNX
    RAG --> Chroma
    RAG --> DeepSeek
```

### 3.2 Mô hình tiến trình (điều ít người để ý nhưng rất quan trọng)

**[CODE]** `run.py` khởi động như sau:

1. `eventlet.monkey_patch()` **phải chạy trước mọi import khác** — nếu thiếu, Socket.IO+Redis báo lỗi.
2. `create_app()` khởi tạo extension và đăng ký blueprint.
3. `start_embedded(app)` chạy **các tác vụ nền cùng tiến trình web**: `rag_engine.warm_up` (nạp model ~20 giây, **luôn chạy**) và `run_forever` (worker huấn luyện tài liệu, **chỉ khi `EMBEDDED_WORKER=true`**).
4. `socketio.run(app, host="0.0.0.0", port=5000, debug=True)`.

Hệ quả: **web, worker và mô hình embedding cùng sống trong một tiến trình**. Để việc tính toán nặng (embedding, tokenizer, cắt chunk) không làm "đóng băng" mọi request, code đẩy chúng sang **luồng hệ điều hành thật** qua `eventlet.tpool` (hàm `run_blocking`, [rag_engine.py:44](../core/rag_engine.py#L44)). Đây là một **thỏa hiệp kiến trúc** ([R26](#r-nhom-c)).

Có thể tách worker riêng (`EMBEDDED_WORKER=false` rồi `python -m workers.process_documents`) — nhưng khi đó **cả hai tiến trình đều nạp một bản mô hình** ([mục 6](#6-tài-nguyên-phần-cứng)).

### 3.3 Cấu trúc thư mục và phân tầng

```
run.py                 điểm khởi động (eventlet + worker nhúng)
config.py              đọc biến môi trường -> class Config
extensions.py          tạo 1 lần các client dùng chung (db, redis, chroma, minio, socketio, oauth...)
app/
  __init__.py          application factory, đăng ký blueprint
  models.py            SQLAlchemy models
  csrf.py              CSRF token thủ công dùng chung
  auth/                đăng nhập/đăng ký/OAuth/Magic Link            ✅
  dashboard/           Bảng điều khiển + 4 bước của trợ lý           ✅ (phần lớn logic ở đây)
    routes.py            nhận request, validate, gọi service
    service.py           logic nghiệp vụ
    events.py            Socket.IO: phòng "bot:<id>", đẩy trạng thái tài liệu
  widget/              API công khai + embed.js + cấu hình giao diện  ✅
  bots/ knowledge/ inbox/ followup/ customers/ reports/ api_tokens/ profile/   ⬜ chỉ có khung
  templates/ static/   giao diện
core/
  rag_engine.py        chunk, embedding, ChromaDB, prompt, trả lời    (trái tim hệ thống)
  llm_client.py        gọi DeepSeek (điểm duy nhất)
  storage_service.py   lớp bọc MinIO
workers/
  process_documents.py huấn luyện tài liệu (đang dùng)               ✅
  send_followups.py    gửi FollowUp theo lịch                        ⬜ chưa hoàn thiện
migrations/            Alembic
models/                mô hình embedding ONNX (2,13 GiB)
scripts/seed_admin.py  tạo user demo
```

Quy ước phân tầng: **`routes.py` (HTTP) → `service.py` (nghiệp vụ) → `core/` (RAG, LLM, lưu trữ)**. Route không chứa logic nghiệp vụ. Mọi client hạ tầng được tạo **một lần** ở `extensions.py` và dùng chung.

### 3.4 Đa khách hàng (multi-tenant)

```mermaid
flowchart LR
    Team["Team<br/>(doanh nghiệp)"] --> Bot1["Bot A"]
    Team --> Bot2["Bot B"]
    Bot1 --> S1["BotSettings"]
    Bot1 --> D1["Documents"]
    Bot1 --> C1["Conversations"]
    Bot1 -.->|"collection bot_A"| V1[("Chroma: bot_A")]
    Bot2 -.->|"collection bot_B"| V2[("Chroma: bot_B")]
```

- **Lớp web:** mọi route quản trị gọi `_require_bot(bot_id)` ([routes.py:27](../app/dashboard/routes.py#L27)) — lấy bot **theo `team_id` trong session**, không thuộc team thì `404`. **[TEST]** truy cập bot của team khác đều trả 404 (trang cấu hình chunk, xem trước, huấn luyện).
- **Lớp vector:** **mỗi bot một collection ChromaDB riêng** (`bot_<id>`), không gộp chung rồi lọc bằng `where` — chọn đúng collection loại bỏ hẳn rủi ro lộ dữ liệu chéo bot do lọc sai.
- **Widget công khai:** xác định bot qua `bot_id` trong URL; mọi truy vấn lọc theo bot đó.
- **Giới hạn:** **không có kiểm tra vai trò** (Owner/Admin/Member) ở bất kỳ route nào — ai thuộc team đều làm được mọi việc; session luôn lấy **team đầu tiên** của user ([R38](#r-nhom-d)).

### 3.5 Mô hình dữ liệu

```mermaid
erDiagram
    TEAMS ||--o{ TEAM_MEMBERS : "có"
    USERS ||--o{ TEAM_MEMBERS : "thuộc"
    TEAMS ||--o{ BOTS : "sở hữu"
    TEAMS ||--o{ CUSTOMERS : "có"
    TEAMS ||--o{ API_TOKENS : "có"
    BOTS ||--|| BOT_SETTINGS : "cấu hình"
    BOTS ||--o{ DOCUMENTS : "có"
    BOTS ||--o{ CONVERSATIONS : "có"
    BOTS ||--o{ FOLLOWUPS : "có"
    BOTS ||--o{ BOT_INTENT_CONFIG : "khai báo ý định"
    CUSTOMERS ||--o{ CONVERSATIONS : "tham gia"
    CONVERSATIONS ||--o{ MESSAGES : "chứa"
    CONVERSATIONS ||--|| CONVERSATION_STATE : "trạng thái Decision Engine"
    CONVERSATIONS ||--o{ STRUCTURED_MEMORY : "bộ nhớ có cấu trúc"
    MESSAGES ||--o| CONVERSATION_MESSAGE_EMBEDDING : "đã embed cho Historical Retrieval"

    TEAMS {
        int id PK
        string name
        string plan
        datetime plan_expires_at
    }
    USERS {
        int id PK
        string email UK
        string password_hash "NULL nếu chỉ dùng OAuth hoặc Magic Link"
        string full_name
    }
    TEAM_MEMBERS {
        int id PK
        int team_id FK
        int user_id FK
        string role "Owner, Admin, Member"
    }
    BOTS {
        int id PK
        int team_id FK
        string name
    }
    BOT_SETTINGS {
        int id PK
        int bot_id FK
        text greeting
        text instructions
        string language
        string ai_model "không còn dùng, model cố định trong code"
        float temperature
        int max_tokens
        float min_similarity "cũ, engine không còn đọc — xem rag_distance_threshold"
        int chunk_size "mặc định 450"
        int chunk_overlap "mặc định 60"
        string config_tier "basic | advanced | expert (mức cấu hình Decision Engine, Bước 1)"
        bool rag_enabled "công tắc Cơ sở tri thức (RAG), tier basic"
        int recent_message_limit "mặc định 10"
        int recent_token_limit "mặc định 2000"
        bool summary_enabled
        int summary_trigger_tokens "mặc định 4000"
        int summary_max_tokens "mặc định 500"
        bool structured_memory_enabled
        int memory_max_items "mặc định 30"
        float memory_min_confidence "mặc định 0.70"
        bool intent_tracking_enabled
        float intent_confidence_threshold "mặc định 0.70"
        bool slot_filling_enabled
        float slot_completion_threshold "mặc định 0.80"
        int rag_top_k "mặc định 8"
        int rag_rerank_top_n "mặc định 5"
        float rag_distance_threshold "mặc định 1.50 (bình phương L2, tương đương cosine 0.25)"
        int rag_max_context_tokens "mặc định 3000"
        int max_candidate_count "mặc định 5"
        bool clarification_enabled
        int max_clarification_turns "mặc định 2"
        float context_pressure_warning "mặc định 0.80"
        float context_pressure_hard_limit "mặc định 0.90"
        string low_confidence_reply_mode "ask_clarify | decline"
        text low_confidence_decline_message "trống = câu mặc định theo ngôn ngữ"
        text low_confidence_clarify_message "trống = câu mặc định theo ngôn ngữ"
        int max_context_tokens "mặc định 8000"
        string widget_domain
        string widget_giao_dien "icon, màu, cỡ, hình dạng, vị trí, khung chat"
    }
    DOCUMENTS {
        int id PK
        int bot_id FK
        string filename
        string storage_path "khóa đối tượng MinIO"
        string status "draft, pending, processing, trained, failed"
        int chunk_size "riêng từng tệp, NULL = mặc định của bot"
        int chunk_overlap "riêng từng tệp"
        int size_bytes
        int chunk_count
        text error_message
    }
    CONVERSATIONS {
        int id PK
        int bot_id FK
        int customer_id FK
        string channel "web_widget"
        string visitor_id "khách vãng lai"
        string status
    }
    MESSAGES {
        int id PK
        int conversation_id FK
        string sender "customer, bot, staff"
        text content
        json decision_trace "NULL cho tin không qua Decision Engine; xem mục 4.3"
        int usage_prompt_tokens "usage thật từ DeepSeek (lệnh gọi chính), NULL nếu chưa đo được"
        int usage_completion_tokens
        int usage_cache_hit_tokens
        int usage_cache_miss_tokens
    }
    CONVERSATION_STATE {
        int id PK
        int conversation_id FK, UK "1-1 với Conversation, tạo lười"
        int bot_id FK
        string current_intent
        string previous_intent
        float intent_confidence
        bool intent_changed
        json slots "slot đã điền của intent hiện tại"
        float slot_completion
        text summary "bản tóm tắt lũy tiến, worker nền ghi"
        int last_summarized_message_id
        bool summary_pending "cờ việc nền: đang chờ tóm tắt"
        int clarification_turns_used "số lượt CLARIFY liên tiếp"
    }
    STRUCTURED_MEMORY {
        int id PK
        int bot_id FK
        int conversation_id FK
        string category
        string mem_key "cột 'key' trùng từ khóa SQL nên đặt mem_key"
        text value
        float confidence
        int source_message_id
        "UNIQUE(conversation_id, category, mem_key)"
    }
    BOT_INTENT_CONFIG {
        int id PK
        int bot_id FK
        string intent_name
        text description
        json required_slots
        json optional_slots
        "UNIQUE(bot_id, intent_name)"
    }
    CONVERSATION_MESSAGE_EMBEDDING {
        int id PK
        int message_id FK, UK "đã embed vào Chroma history_<bot_id> cho Historical Retrieval"
        int bot_id FK
        int conversation_id FK
    }
    FOLLOWUPS {
        int id PK
        int bot_id FK
        text content
        datetime schedule_at
        string status
    }
    CUSTOMERS {
        int id PK
        int team_id FK
        string name
        string phone
        string email
    }
    API_TOKENS {
        int id PK
        int team_id FK
        string token_hash "chỉ lưu băm"
        json scopes
    }
```

> `BOT_INTENT_CONFIG`, `CONVERSATION_STATE`, `STRUCTURED_MEMORY`, `CONVERSATION_MESSAGE_EMBEDDING` là 4 bảng mới của
> Context & Response Decision Engine (migration `1a2b3c4d5e01`–`04`, [mục 3.7](#37-các-migration)); chưa vẽ quan hệ
> `BOTS ||--o{ CONVERSATION_STATE`/`STRUCTURED_MEMORY` trực tiếp trong sơ đồ để đỡ rối — cả hai đều có cột `bot_id` riêng
> (không phải suy ra qua `Conversation`) để truy vấn theo bot không cần join.

**Dữ liệu nằm ở đâu:**

| Nơi lưu | Dữ liệu |
| --- | --- |
| **MariaDB** | Toàn bộ bảng trên (thông tin, cấu hình, trạng thái, hội thoại, trạng thái Decision Engine, bộ nhớ có cấu trúc) |
| **MinIO** (bucket `aichatbot`) | Tệp gốc, khóa đối tượng `{team_id}/{bot_id}/{document_id}/{tên tệp}` |
| **ChromaDB — collection `bot_<id>`** | Tri thức: nội dung từng chunk + vector; id `{document_id}-{chunk_index}`, metadata `{document_id, chunk_index}` |
| **ChromaDB — collection `history_<bot_id>`** | **Mới:** vector từng tin nhắn (khách/bot) cho Historical Retrieval (Bước F, hiếm dùng); id `msg-<message_id>` — collection RIÊNG với tri thức để 2 loại tìm kiếm không lẫn nhau |
| **Redis** | Dữ liệu tạm: bộ đếm rate limit, khóa worker, Magic Link đã dùng, hàng đợi Socket.IO |
| **Trình duyệt** (`localStorage`) | Widget lưu `visitorId`, `conversationId` và tối đa 40 tin gần nhất |
| **Cookie phiên** (ký bằng `SECRET_KEY`) | `_user_id`, `team_id`, `csrf_token` — **không có phiên lưu ở server** |

### 3.6 Redis được dùng vào 5 việc

1. **Rate limit** (Flask-Limiter) — `RATELIMIT_STORAGE_URI`.
2. **Message queue Socket.IO** — để worker (tiến trình khác) đẩy sự kiện tới trình duyệt qua web.
3. **Khóa worker huấn luyện tài liệu** `knowledge-worker-lock` (TTL 60 giây) — đảm bảo chỉ 1 worker hoạt động.
4. **Magic Link dùng một lần** — khóa `magic_link_used:<sha256(token)>`, TTL 15 phút.
5. **Mới — khóa worker việc nền của Decision Engine** `context-jobs-worker-lock` (TTL 60 giây, **riêng** với khóa #3 để 2 loại
   việc không chặn nhau) + khóa lùi lại `context-jobs:summary-backoff:<state_id>` (TTL 300 giây) khi tóm tắt một hội thoại lỗi,
   tránh thử lại ngay lập tức — [workers/context_jobs.py](../workers/context_jobs.py).

> Redis **không** dùng để lưu phiên đăng nhập (dù tài liệu kiến trúc cũ có đề xuất).

### 3.7 Các migration

| Revision | Nội dung |
| --- | --- |
| `ebc917e7a7a3` | Schema khởi tạo |
| `19b84b14d429` | Cho phép `password_hash` NULL (đăng nhập Google) |
| `ac79baccb0bb` | Thêm các trường cấu hình Bước 1 |
| `b3f1c2d4e5a6` | Thêm `max_tokens`, `chunk_size`, `chunk_overlap` vào `bot_settings` |
| `c4d2e6f8a1b3` | Icon + kích thước widget |
| `d5e3f7a9b2c4` | Màu widget |
| `e6f4a8b0c3d5` | Hình dạng, vị trí, cỡ khung chat widget |
| `f7a5b9c1d4e6` | `chunk_count`, `error_message` trên `documents` |
| `a8c6d0e2f4b7` | `chunk_size`, `chunk_overlap` riêng từng tài liệu + trạng thái `draft` |
| `b9d7e1f3a5c8` | `min_similarity` trên `bot_settings` (nay không còn được engine đọc) |
| `c1a8e4f6b2d7`, `d3f6a8c1e9b2` | `stage` của khách hàng; `widget_icon_path` |
| `1a2b3c4d5e01`–`04` | **Context & Response Decision Engine**: cấu hình engine + tier, `conversation_state`, `structured_memory`, `bot_intent_config`, `messages.decision_trace/usage_*`, `conversation_message_embeddings` — xem [CONTEXT_ENGINE.md](CONTEXT_ENGINE.md) |

**Tổng 13 migration** (9 migration gốc + 4 migration Context Engine `1a2b3c4d5e01`–`04`). `flask db upgrade` áp cả 13 theo đúng thứ tự;
migration Context Engine chỉ **thêm** cột/bảng (mọi cột mới có `server_default`) nên áp được lên DB dev đang có dữ liệu mà không mất gì —
xem chi tiết backfill `rag_distance_threshold` ở [CONTEXT_ENGINE.md §5](CONTEXT_ENGINE.md).

---

## 4. Luồng xử lý lõi (RAG)

Đây là phần quan trọng nhất của hệ thống, gồm **hai pha độc lập** dùng chung một collection ChromaDB: **Pha A** biến tài liệu thành tri thức tìm kiếm được; **Pha B** dùng tri thức đó để trả lời khách.

### 4.1 Từ điển thuật ngữ cần nắm

| Thuật ngữ | Giải thích ngắn |
| --- | --- |
| **Token** | Đơn vị nhỏ nhất mô hình đọc (tiếng Việt ≈ 3–4 ký tự/token). Giới hạn của mô hình tính bằng token, không phải ký tự. |
| **Chunk** | Đoạn tài liệu đã cắt ra; là đơn vị được lưu và tìm kiếm. |
| **Overlap** | Phần lặp lại giữa hai chunk liền kề để không mất ngữ cảnh tại điểm cắt. |
| **Embedding** | Chuyển một đoạn văn thành vector 1024 số; đoạn có nghĩa gần nhau thì vector gần nhau. |
| **Top-k** | Số chunk gần câu hỏi nhất được lấy ra ở bước tra cứu (mặc định k = 8), trước khi lọc theo ngưỡng khoảng cách và cắt còn `rag_rerank_top_n` (mặc định 5). |
| **Decision Engine** *(mục 4.3, mới)* | `core/context_engine/`: bộ điều phối 1 lượt trả lời — thay hẳn `rag_engine.search/build_prompt/answer` cũ. Nhận câu hỏi + trạng thái hội thoại, gọi ĐÚNG 1 lệnh DeepSeek chính, rồi tự quyết định ANSWER/CLARIFY/DECLINE bằng code (không phải LLM). |
| **Intent (ý định)** | Nhãn ngắn AI gán cho mục đích của khách trong lượt hiện tại (vd. `ask_price`, `product_search`), do bot tự khai báo ở `bot_intent_config`; có thể yêu cầu **slot bắt buộc**. |
| **Slot** | Một trường thông tin intent cần để xử lý (vd. `budget`, `product`); AI trích từ câu hỏi, thiếu slot bắt buộc thì bot hỏi lại thay vì đoán. |
| **Structured Memory (bộ nhớ có cấu trúc)** | Dữ kiện/sở thích/yêu cầu khách đã xác nhận, AI trích và lưu theo `(category, key, value, confidence)`, dùng lại ở các lượt sau kể cả khi tin cũ đã ra khỏi ngữ cảnh. |
| **Rolling summary (tóm tắt lũy tiến)** | Bản tóm tắt hội thoại do worker nền cập nhật khi các tin chưa tóm tắt vượt ngưỡng token; hợp nhất tóm tắt cũ + tin mới thành 1 bản mới. |
| **Ngưỡng khoảng cách** (`rag_distance_threshold`) | Thay cho `min_similarity` cũ: Chroma trả **bình phương** khoảng cách L2 (không phải cosine); đoạn có khoảng cách **lớn hơn** ngưỡng bị loại. Mặc định 1,50 ≈ cosine 0,25. |
| **Áp lực ngữ cảnh** (`context_pressure`) | Token đầu vào ước tính ÷ `max_context_tokens`; vượt ngưỡng thì hệ thống tự nén (bỏ đoạn trùng, đoạn điểm thấp, rút gọn, giảm lịch sử...) TRƯỚC khi gọi LLM. |
| **JSON có cấu trúc (structured output)** | Lệnh gọi DeepSeek chính luôn ở "JSON mode", trả đúng 1 đối tượng JSON (intent, slots, memory_updates, proposed_answer...) thay vì văn xuôi tự do — dễ parse và kiểm soát hơn 1 chuỗi prompt như trước. |
| **Cache hit / cache miss** | DeepSeek tự động cache phần đầu prompt **giống hệt** request trước (theo khối 128 token); phần cache hit tính phí rẻ hơn nhiều so với cache miss — xem [BANG_GIA_API_AI.md](BANG_GIA_API_AI.md). |

---

### 4.2 PHA A — Nạp tri thức

#### 4.2.1 Vòng đời của một tài liệu

```mermaid
stateDiagram-v2
    [*] --> draft: Upload thành công, chưa embed
    draft --> pending: Người dùng bấm Lưu cấu hình và huấn luyện
    pending --> processing: Worker nhận tài liệu
    processing --> trained: Embed và ghi ChromaDB xong
    processing --> failed: Có lỗi khi xử lý
    processing --> pending: Worker khởi động lại và khôi phục tài liệu kẹt
    trained --> pending: Cấu hình lại rồi huấn luyện lại
    failed --> pending: Cấu hình lại rồi huấn luyện lại
    draft --> [*]: Xóa
    trained --> [*]: Xóa
    failed --> [*]: Xóa
```

- Chỉ tài liệu ở `draft`, `trained`, `failed` mới được **cấu hình chunk** (route trả `409` với `pending`/`processing`) **[CODE, TEST]** — [routes.py:297](../app/dashboard/routes.py#L297).
- Trước phiên bản mới, tài liệu upload xong là **tự huấn luyện ngay**. Nay tài liệu dừng ở `draft` cho đến khi người dùng duyệt cấu hình chunk.

#### 4.2.2 Sơ đồ tuần tự đầu-cuối

```mermaid
sequenceDiagram
    autonumber
    actor U as Người dùng
    participant B as Trình duyệt
    participant F as Flask (dashboard)
    participant M as MinIO
    participant D as MariaDB
    participant W as Worker nền
    participant R as rag_engine
    participant C as ChromaDB
    participant X as Redis (Socket.IO)

    U->>B: Chọn tệp TXT/MD/CSV
    B->>F: POST /knowledge/upload (multipart, có CSRF)
    F->>F: Kiểm tra đuôi, kích thước, quota còn lại
    F->>M: Lưu tệp gốc
    F->>D: INSERT documents (status = draft)
    F-->>B: Chuyển tới trang Cấu hình chunk

    loop Mỗi lần người dùng đổi chunk size / overlap (sau 400 ms)
        B->>F: POST /chunks/preview {chunk_size, chunk_overlap}
        F->>M: Đọc lại tệp gốc
        F->>R: chunk_markdown(văn bản đã chuẩn hóa, size, overlap)
        F-->>B: Danh sách chunk + số token + thống kê
        B->>B: Tô màu từng chunk
    end

    U->>B: Bấm Lưu cấu hình và huấn luyện
    B->>F: POST /train
    F->>D: Lưu cấu hình riêng của tài liệu, status = pending
    F-->>B: Về danh sách tài liệu

    loop Mỗi 3 giây
        W->>D: Quét documents có status = pending
    end
    W->>D: UPDATE có điều kiện: pending thành processing
    W->>M: Tải tệp gốc
    W->>R: Chuẩn hóa, cắt chunk theo cấu hình riêng của tài liệu
    loop Từng lô 16 chunk
        W->>R: embed_texts (lô 8 chunk một lần)
        W->>C: collection.add (id, nội dung, vector, metadata)
        W->>X: emit document_status (đã xong X trên tổng Y)
        X-->>B: Cập nhật thanh tiến độ
    end
    W->>D: status = trained, chunk_count = N
    W->>X: emit trained
    X-->>B: Hiện thông báo hoàn tất
```

#### 4.2.3 Bước 1 — Upload

**[CODE]** [service.py:391](../app/dashboard/service.py#L391) `upload_document`:

| Kiểm tra | Giá trị | Hành vi khi vi phạm |
| --- | --- | --- |
| Đuôi tệp | `.txt`, `.md`, `.csv` | Báo lỗi "PDF/DOCX đang phát triển" |
| Kích thước 1 tệp | `KNOWLEDGE_MAX_FILE_MB` = **5 MB** | Đọc tối đa 5 MB + 1 byte để không nạp tệp khổng lồ vào RAM |
| Tệp rỗng | — | Báo lỗi |
| Dung lượng còn lại của bot | `KNOWLEDGE_STORAGE_LIMIT_MB` = **50 MB/bot** | Báo lỗi kèm số còn lại |
| Tổng 1 request | 51 MB (`MAX_CONTENT_LENGTH`) | Trả về trang danh sách kèm thông báo |
| CSRF | Token phiên | Từ chối |

Sau khi hợp lệ: tạo bản ghi `documents` (`status=draft`), lưu tệp gốc vào MinIO, cập nhật `storage_path`. Upload **1 tệp** → chuyển thẳng tới trang cấu hình chunk; **nhiều tệp** → về danh sách, mỗi tệp có nút "Cấu hình chunk".

#### 4.2.4 Bước 2 — Chuẩn hóa sang markdown

**[CODE]** [service.py `_extract_text`](../app/dashboard/service.py) — hiện thực **rất tối giản**:

| Loại tệp | Xử lý |
| --- | --- |
| `.md`, `.txt` | Giải mã UTF-8 (bỏ BOM), **giữ nguyên nội dung** |
| `.csv` | Chuyển thành **bảng markdown** (`\| cột \|` + hàng phân cách `\| --- \|`) |

**Điều cần biết:**

- Chuẩn hóa được thực hiện **theo yêu cầu** (mỗi lần xem trước và khi huấn luyện) từ tệp gốc; **không lưu riêng một tệp `.md`** — ưu điểm: không phát sinh dữ liệu trùng; nhược điểm: mỗi lần xem trước đọc lại MinIO ([R21](#r-nhom-b)).
- **TXT không có tiêu đề → không có cấu trúc để cắt theo mục**, chỉ gom theo đoạn văn. Hệ thống **chưa** tự nhận diện tiêu đề, và **chưa** có chỗ sửa nội dung `.md` trước khi huấn luyện ([R19](#r-nhom-b)).
- Giải mã dùng `errors="replace"`: tệp **không phải UTF-8** (ví dụ Windows-1258, TCVN3) sẽ bị thay ký tự lạ thành `�` **mà không báo lỗi** ([R18](#r-nhom-b)).
- PDF/DOCX: **chưa hỗ trợ** (cần thêm thư viện trích xuất — phải được chủ dự án duyệt theo quy định).

#### 4.2.5 Bước 3 — Xem trước và cấu hình chunk riêng từng tệp

**[CODE + TEST]** Endpoint `POST .../chunks/preview` gọi **đúng hàm `rag_engine.chunk_markdown` mà worker dùng khi huấn luyện** → chunk người dùng thấy là chunk sẽ được huấn luyện. Đã kiểm chứng: **số chunk huấn luyện = số chunk xem trước** (6/6 và 24/24 trong các ca thử).

| Tham số | Miền hợp lệ | Ý nghĩa |
| --- | --- | --- |
| `chunk_size` | **100 – 1000 token** (mặc định 450) | Kích thước tối đa mỗi chunk, **tính cả dòng tiêu đề đầu chunk** |
| `chunk_overlap` | **0 – 30% của chunk_size** (mặc định 60) | Phần lặp lại giữa hai chunk liền kề |

Giá trị sai (dưới min, trên max, chữ, số thập phân, thiếu, overlap âm hoặc >30%) bị **từ chối kèm thông báo rõ** (`400`) chứ **không tự ép** về khoảng hợp lệ — [service.py:316](../app/dashboard/service.py#L316). Mỗi tài liệu lưu cấu hình riêng (`documents.chunk_size/chunk_overlap`); nếu để trống (tài liệu cũ) thì dùng mặc định của bot (450/60).

#### 4.2.6 Bước 4 — Thuật toán cắt chunk

**[CODE]** [rag_engine.py:305 `chunk_markdown`](../core/rag_engine.py#L305). Mục tiêu: **không cắt giữa đoạn, giữ mỗi chunk tự đủ ngữ cảnh**.

```mermaid
flowchart TD
    T["Văn bản markdown"] --> S["1. Tách theo tiêu đề #, ##, ###<br/>bỏ qua dòng # nằm trong khối code"]
    S --> P["2. Mỗi mục: tạo đường dẫn tiêu đề<br/>ví dụ: Chính sách bảo hành > Thời hạn<br/>làm dòng đầu của MỌI chunk trong mục"]
    P --> BUD["3. budget = chunk_size - token của đường dẫn tiêu đề<br/>(tối thiểu chunk_size / 2)"]
    BUD --> AT["4. Chia thân mục thành các 'nguyên tử':<br/>đoạn văn/danh sách, bảng markdown, khối code"]
    AT --> BIG{"Nguyên tử có<br/>vượt budget?"}
    BIG -- "Không" --> PACK["5. Gom các nguyên tử vào chunk<br/>cho đến khi đầy budget"]
    BIG -- "Có, là bảng" --> TBL["Chia theo nhóm dòng<br/>LẶP LẠI dòng tiêu đề bảng ở mỗi chunk"]
    BIG -- "Có, là văn bản" --> RS["RecursiveCharacterTextSplitter<br/>cắt theo: đoạn, dòng, câu, dấu ; , từ"]
    TBL --> PACK
    RS --> PACK
    PACK --> OV["6. Overlap: chunk sau mở đầu bằng<br/>các NGUYÊN KHỐI cuối của chunk trước<br/>(tổng ≤ overlap, không cắt giữa khối)"]
    OV --> OUT["Danh sách chunk = tiêu đề + nội dung"]
```

Các quyết định thiết kế đáng chú ý:

- **Đếm token bằng tokenizer thật của model embedding** (không đếm ký tự) → không bao giờ vượt giới hạn 2048 token của model.
- Chỉ nhận tiêu đề **cấp 1–3** (`#`, `##`, `###`); `####` trở xuống được coi là nội dung thường.
- Tài liệu **không có tiêu đề** vẫn cắt được nhưng chunk **không có đường dẫn tiêu đề**.
- Chunk tự tạo được đo bằng `count_tokens` **sau khi đã gắn tiêu đề**.

**Ví dụ thật** (chạy `chunk_markdown(..., chunk_size=100, overlap=10)` trên một đoạn mẫu):

```text
Đầu vào:
# Chính sách bảo hành
## Thời hạn
Sản phẩm điện tử được bảo hành 12 tháng kể từ ngày mua. Phụ kiện đi kèm được bảo hành 6 tháng.
Khách hàng cần giữ hóa đơn hoặc phiếu bảo hành để được hỗ trợ nhanh nhất.
## Bảng phí sửa chữa
| Hạng mục | Phí |  ...(bảng 3 dòng)

Kết quả: 2 chunk
--- chunk 0 | 48 token | metadata {h1: Chính sách bảo hành, h2: Thời hạn}
Chính sách bảo hành > Thời hạn          <- dòng tiêu đề tự thêm
(2 đoạn văn nguyên vẹn)
--- chunk 1 | 60 token | metadata {h1: Chính sách bảo hành, h2: Bảng phí sửa chữa}
Chính sách bảo hành > Bảng phí sửa chữa
(bảng nguyên vẹn, không bị cắt giữa dòng)
```

**Bảng lớn** (40 dòng) với `chunk_size=100` → **6 chunk**, mỗi chunk đều mở đầu bằng `| Mã | Giá |` + `| --- | --- |` để chunk nào cũng biết cột nào là gì.

#### 4.2.7 Bước 5 — Worker huấn luyện

**[CODE]** [workers/process_documents.py](../workers/process_documents.py):

```mermaid
flowchart TD
    L["Vòng lặp vô hạn"] --> RB["db.session.rollback()<br/>(bắt buộc: MySQL REPEATABLE READ giữ ảnh chụp cũ)"]
    RB --> LK{"Lấy được khóa Redis?<br/>SET NX, TTL 60 giây"}
    LK -- "Không (worker khác đang chạy)" --> SL2["Ngủ 6 giây"] --> L
    LK -- "Có" --> REC{"Lần đầu?"}
    REC -- "Có" --> RESET["Đưa mọi tài liệu 'processing'<br/>về 'pending' (khôi phục sau sự cố)"]
    REC -- "Không" --> CL
    RESET --> CL["claim_next_document:<br/>UPDATE ... WHERE status='pending'<br/>(chống 2 worker nhận trùng)"]
    CL --> ANY{"Có tài liệu?"}
    ANY -- "Không" --> SL3["Ngủ 3 giây"] --> L
    ANY -- "Có" --> H["handle: tải tệp MinIO,<br/>process_document"]
    H --> OK{"Thành công?"}
    OK -- "Có" --> TR["status = trained<br/>chunk_count = N"] --> L
    OK -- "Không" --> FL["status = failed<br/>error_message ngắn gọn thân thiện"] --> L
```

Chi tiết quan trọng:

- **Mỗi lần chỉ xử lý 1 tài liệu**, để RAM/CPU không nghẽn.
- **Cấu hình chunk lấy từ chính tài liệu** (`chunk_params_for`), rơi về mặc định của bot nếu để trống.
- **Thông báo lỗi cho người dùng được rút gọn** (`failure_message`): lỗi MinIO, lỗi kết nối ChromaDB, lỗi mã hóa… được dịch ra câu tiếng Việt; chi tiết kỹ thuật chỉ nằm ở log worker.

#### 4.2.8 Bước 6 — Embedding

**[CODE]** [rag_engine.py:83 `_embed_batch`](../core/rag_engine.py#L83):

1. Tokenizer mã hóa lô văn bản (padding, cắt ở 2048 token).
2. Chạy **ONNX Runtime**, `CPUExecutionProvider`, lấy đầu ra `sentence_embedding` (mô hình xuất ONNX đã gồm bước pooling).
3. **Chuẩn hóa L2** mỗi vector (độ dài = 1). Nhờ vậy xếp hạng theo khoảng cách L2 của ChromaDB **tương đương** xếp hạng theo độ tương đồng cosine.
4. Xử lý theo **lô 8 chunk**; mô hình và tokenizer được **nạp đúng 1 lần** dù nhiều luồng cùng gọi (khóa `_load_lock`, dùng khóa luồng "thật" của hệ điều hành chứ không phải khóa greenlet).

#### 4.2.9 Bước 7 — Ghi ChromaDB

**[CODE]** [rag_engine.py:336 `upsert_chunks`](../core/rag_engine.py#L336):

1. **Xóa toàn bộ chunk cũ** của tài liệu (`where document_id = ...`) → thao tác **lặp lại được** (huấn luyện lại không nhân đôi dữ liệu).
2. Ghi theo **lô 16 chunk** (embed + `collection.add`), báo tiến độ sau mỗi lô. Ghi từng lô vì tài liệu 5 MB có thể tới hàng nghìn chunk, ghi một lần sẽ vượt giới hạn lô của Chroma.
3. Mỗi chunk: id `{document_id}-{chunk_index}`; metadata `{document_id, chunk_index}`; **vector đã tính sẵn** (Chroma không tự embed).
4. **Lỗi giữa chừng** → xóa phần đã ghi để không để lại "nửa tài liệu", rồi ném lỗi lên (trạng thái `failed`).

> ⚠️ Vì bước 1 xóa trước khi ghi mới, **trong lúc huấn luyện lại, tài liệu đó tạm thiếu trong kết quả tìm kiếm** ([R14](#r-nhom-b)).

#### 4.2.10 Trạng thái realtime tới trình duyệt

```mermaid
sequenceDiagram
    participant W as Worker
    participant X as Redis (message queue)
    participant S as Web (Flask-SocketIO)
    participant B as Trình duyệt (trang Cơ sở tri thức)

    B->>S: Kết nối WebSocket (kiểm tra Origin trùng host)
    B->>S: join_bot {bot_id}
    S->>S: Xác minh bot thuộc team đang đăng nhập
    S-->>B: ack ok, vào phòng bot:ID
    W->>X: emit document_status (processing, done, total)
    X->>S: chuyển sự kiện
    S-->>B: document_status tới đúng phòng bot:ID
    B->>B: Cập nhật thanh tiến độ, ước tính thời gian còn lại
    Note over B,S: Mất WebSocket thì tự chuyển sang hỏi GET /knowledge/status mỗi 5 giây
```

Mỗi bot một "phòng" (`bot:<id>`); trình duyệt chỉ vào được phòng của bot thuộc team mình → không nhận được tiến độ tài liệu của khách hàng khác. Có **dự phòng polling 5 giây** khi Socket.IO không dùng được.

#### 4.2.11 Xóa và huấn luyện lại

- **Xóa tài liệu:** xóa vector trong ChromaDB → xóa tệp MinIO → xóa bản ghi. Lỗi khi xóa tệp MinIO bị **bỏ qua im lặng** ([service.py:430](../app/dashboard/service.py#L430), [R29](#r-nhom-c)). Route xóa **không kiểm tra trạng thái** — xóa lúc worker đang xử lý có thể để lại vector mồ côi ([R13](#r-nhom-b)).
- **Huấn luyện lại:** mở lại "Cấu hình chunk" của tài liệu đã `trained`/`failed`, đổi tham số, bấm huấn luyện. Hoạt động với cả **tài liệu cũ** (chưa có cấu hình riêng). **[TEST]** huấn luyện lại thay hoàn toàn chunk cũ (6 → 6 → 6, không sót).

---

### 4.3 PHA B — Context & Response Decision Engine

> Thay hẳn `rag_engine.search/build_prompt/answer` mô tả ở các bản báo cáo trước (2026-09-19 trở về trước). Mã nguồn nằm ở
> `core/context_engine/` (7 tệp: `settings`, `builder`, `structured`, `decision`, `state`, `history_retrieval`, `engine`, `jobs`,
> `cost`) + `core/context_engine/cost_estimate.py` (chi phí ước tính, [mục 5.4](#54-bước-1--thiết-lập)); điều phối 1 lượt ở
> `engine.run_turn` ([engine.py:54](../core/context_engine/engine.py#L54)). Tài liệu kỹ thuật đầy đủ: [CONTEXT_ENGINE.md](CONTEXT_ENGINE.md).
> `core/rag_engine.py` **không bị xóa** — vẫn là nơi chunk/embed/ghi ChromaDB cho Pha A ([mục 4.2](#42-pha-a--nạp-tri-thức)) và cung
> cấp `count_tokens_many`/`retrieve`/`fit_passages_to_budget` cho Decision Engine dùng lại; chỉ 3 hàm cũ (`search`, `build_prompt`,
> `answer`) không còn được gọi từ luồng trả lời khách.

#### 4.3.1 Sơ đồ tuần tự

```mermaid
sequenceDiagram
    autonumber
    actor K as Khách truy cập
    participant W as Widget (embed.js)
    participant F as Flask (widget API)
    participant D as MariaDB
    participant E as core/context_engine<br/>(builder + decision, không LLM)
    participant C as ChromaDB<br/>(bot_ID + history_ID)
    participant A as DeepSeek API
    participant J as Worker context_jobs<br/>(nền, không chặn request)

    K->>W: Gõ câu hỏi, bấm Gửi
    W->>F: POST /widget/api/ID/messages {message, visitor_id, conversation_id}
    F->>F: Kiểm tra Origin thuộc domain đã khai báo; giới hạn 20 lượt/phút/IP; tin nhắn ≤ 1000 ký tự
    F->>D: Tìm/tạo Conversation (khớp bot + visitor_id); lưu tin khách (commit ngay)
    alt Nhân viên đang tiếp quản hội thoại (Inbox)
        F-->>W: {reply: null, ...} — bot im lặng, nhân viên trả lời qua Inbox
    else Bot trả lời
        F->>D: Đọc ConversationState + StructuredMemory + tin gần đây (theo recent_message_limit/token_limit)
        F->>E: run_turn(bot_id, câu hỏi, settings theo tier, snapshot, intents, recent_rows)
        E->>C: Bước A: truy vấn top_k trong bot_ID, lọc theo rag_distance_threshold, mở rộng lân cận
        E->>E: Tính áp lực ngữ cảnh; nén nếu vượt ngưỡng cảnh báo/nén mạnh
        E->>A: Bước B: ĐÚNG 1 lệnh gọi chính (JSON mode, thinking tắt)
        A-->>E: JSON {intent, slots, memory_updates, proposed_answer, proposed_clarification_question, needs_history_lookup...}
        E->>E: Bước C: cây quyết định ANSWER / CLARIFY / DECLINE (không gọi LLM)
        opt needs_history_lookup=true và quyết định ANSWER (hiếm)
            E->>C: Bước F: tìm lại trong history_ID (Historical Retrieval)
            E->>A: lệnh gọi LLM phụ (kind=history_lookup)
        end
        E-->>F: reply, decision, trace, usage
        F->>D: Bước D: lưu tin bot (decision_trace + usage_*), cập nhật state + memory
        F->>D: Bước E: token chưa tóm tắt vượt ngưỡng? chỉ đặt cờ summary_pending (không gọi LLM ở đây)
        F-->>W: {reply, conversation_id, visitor_id}
    end
    W-->>K: Hiển thị câu trả lời
    par Việc nền (không chặn lượt trả lời)
        J->>D: Quét summary_pending mỗi 3 giây -> tóm tắt hội thoại (lệnh gọi LLM riêng)
        J->>C: Embed tin nhắn mới vào history_ID cho lần Historical Retrieval sau
    end
```

#### 4.3.2 Các bước chi tiết

**(1) Xác thực yêu cầu công khai** — [widget/routes.py](../app/widget/routes.py), **không đổi** so với trước: **Origin phải trùng domain
đã khai báo ở Bước 3** (hoặc subdomain); CORS chỉ trả đúng origin đó; **20 lượt/phút/IP** cho `POST /messages`; tin nhắn tối đa
**1000 ký tự** (`service.MAX_MESSAGE_CHARS`). Kiểm tra Origin chỉ chặn được **trình duyệt** ([R5](#r-nhom-a)).

**(2) Hội thoại và tiếp quản của nhân viên** — [widget/service.py `receive_message`](../app/widget/service.py):

- `visitor_id` do trình duyệt sinh (UUID), cắt còn 64 ký tự; chỉ dùng lại `conversation_id` nếu **khớp đúng bot và đúng visitor_id**.
- **Mới:** nếu tin trả lời gần nhất (bỏ qua tin khách) là của **nhân viên** và hội thoại còn mở, bot **không trả lời** lượt này
  (`_staff_has_taken_over`) — nhân viên trả lời tiếp qua Inbox; khách nhắn lại thì hội thoại tự mở lại.
- **Mới:** bật "Thu thập thông tin khách hàng" (`collect_customer_info`) thì mỗi tin khách được quét **số điện thoại/email** bằng
  regex (`app/customers/service.py capture_contact`); có khớp thì tạo hoặc dùng lại `Customer` cùng team và gắn vào hội thoại
  (chỉ điền trường còn trống, không ghi đè). **Đây là thay đổi so với báo cáo trước** — trước đây công tắc này chưa có logic nào
  dùng; nay đã có, xem cập nhật [R17](#r-nhom-b). `forward_to_staff` và `away_message` **vẫn chưa có logic nào dùng**.
- **Tin của khách được lưu (commit) trước khi gọi Decision Engine** → lỗi LLM/Chroma thì tin khách vẫn còn.
- Lịch sử đưa vào Bước A **không còn cắt cứng "6 tin × 600 ký tự"**: lấy tối đa `recent_message_limit` tin (mặc định 10, cố định ở
  tier Cơ bản), dừng sớm nếu vượt `recent_token_limit` token thật (đếm bằng tokenizer, không phải ký tự) — xem (3).

**(3) Bước A — Context Builder + RAG, KHÔNG gọi LLM** — [builder.py](../core/context_engine/builder.py):

1. `RecentMessageSelector`: chọn tin gần đây theo (2), bỏ tin nhân viên và tin đã nằm trong tóm tắt, gộp 2 tin liền cùng vai.
2. Nếu bật "Cơ sở tri thức (RAG)": `rag_engine.retrieve` — embedding câu hỏi, `collection.query` trong `bot_<id>`, lọc theo
   **`rag_distance_threshold`** (Chroma trả **bình phương** khoảng cách L2; ngưỡng càng **nhỏ** càng khắt khe — thay hẳn
   `min_similarity`/cosine cũ), loại trùng ngữ nghĩa (`DUPLICATE_COSINE=0,97`), mở rộng chunk lân cận (`NEIGHBOR_WINDOW=1`) như
   trước, giữ tối đa `rag_rerank_top_n` **vùng nội dung** (đếm SAU khi gộp chunk liền kề, TRƯỚC khi cắt).
3. Ghép ngân sách: `AVAILABLE_RAG_TOKENS = max_context_tokens − system − memory − summary − recent − câu_hỏi − (max_tokens+600)`;
   trần RAG = `min(AVAILABLE_RAG_TOKENS, rag_max_context_tokens)`. Tài liệu tìm được **vượt** trần và còn lượt hỏi làm rõ → chỉ đưa
   **phần mở đầu của từng chunk** và yêu cầu LLM đặt 1 câu hỏi thu hẹp phạm vi (`allow_scope_narrowing`); hết lượt/tắt hỏi làm rõ →
   giữ các chunk liên quan nhất còn vừa ngân sách và trả lời thẳng (không hỏi vô hạn).
4. **Áp lực ngữ cảnh** = token đầu vào ước tính ÷ `max_context_tokens`. Dưới 0,60: bình thường. `0,60–warning`: nén nhẹ (chỉ RAG:
   bỏ đoạn trùng → bỏ chunk điểm thấp → nén nội dung chunk). `warning–hard_limit`: nén thêm — giảm dần tin lịch sử (tối đa còn một
   nửa) → dùng tóm tắt thay tin thô (nếu có). Việc nén này luôn chạy **TRƯỚC** khi gọi LLM nên không bao giờ là lý do CLARIFY ở
   Bước C — chỉ ghi vào `decision_trace.compression_steps`.

**(4) Bước B — ĐÚNG 1 lệnh gọi DeepSeek chính** — [structured.py](../core/context_engine/structured.py),
[llm_client.py](../core/llm_client.py): `ChatDeepSeek(model="deepseek-flash", temperature, max_tokens+600, extra_body={"thinking":
{"type":"disabled"}})`, JSON mode. **Tắt thinking mode vẫn bắt buộc** (lý do đã đo như báo cáo trước: `max_tokens` nhỏ + thinking
bật → câu trả lời rỗng). Model trả **một đối tượng JSON** (`intent`, `intent_confidence`, `slots`, `memory_updates`,
`needs_history_lookup`, `self_assessed_confidence`, `proposed_answer`, `proposed_clarification_question`) — khác hẳn văn xuôi tự do
trước đây, dễ kiểm soát và tách bạch "câu trả lời" khỏi "tín hiệu quyết định". JSON lỗi định dạng → gọi lại **đúng 1 lần** (`kind=
retry`, vẫn tính là lệnh gọi chính khi ghi `usage`); lỗi lần 2 → `StructuredOutputError` → route trả `502`. Message gửi đi **đã**
tách vai trò `system`/`user`/`assistant` xen kẽ ([builder.py `MessageBuilder`](../core/context_engine/builder.py)) — khác cách gộp
1 chuỗi trước đây, giảm một phần rủi ro ở [R10](#r-nhom-a) (chưa đóng hẳn, xem cập nhật R10 ở [mục 8](#8-rủi-ro-và-nợ-kỹ-thuật)).

**(5) Bước C — Decision Engine, KHÔNG gọi LLM** — [decision.py `decide`](../core/context_engine/decision.py), hàm **thuần** (test
độc lập từng nhánh). Thứ tự ưu tiên, nhánh đầu khớp thì dừng:

```text
1. RAG bật + có tri thức + không đoạn nào đạt ngưỡng
     -> CLARIFY (câu hỏi lại đã cấu hình) nếu còn lượt hỏi làm rõ, ngược lại DECLINE (câu từ chối đã cấu hình)
     -> KHÔNG dùng proposed_answer của LLM (không tự tin trả lời dựa trên ngữ cảnh dưới ngưỡng)
2. Tài liệu tìm được vượt ngân sách (scope_narrowing) -> CLARIFY thu hẹp phạm vi
3. intent_confidence < ngưỡng (bật "Theo dõi ý định") -> CLARIFY
4. Thiếu slot bắt buộc (bật "Thu thập thông tin bắt buộc") -> CLARIFY
5. Nhiều nguồn liên quan ngang nhau (bật RAG) -> CLARIFY thu hẹp
6. Còn lại -> ANSWER (proposed_answer của LLM; LLM tự thấy chưa đủ thì rơi về CLARIFY/DECLINE)
```

CLARIFY bị **ép thành ANSWER** khi hết `max_clarification_turns` lượt liên tiếp (kèm ghi chú lịch sự nếu LLM tự đánh giá độ chắc
chắn thấp) — riêng nhánh 1 luôn ép thành DECLINE, không bao giờ trả lời tự tin từ ngữ cảnh dưới ngưỡng.

**(6) Bước F — Historical Retrieval (hiếm)** — [history_retrieval.py](../core/context_engine/history_retrieval.py): chỉ chạy khi
LLM tự báo `needs_history_lookup=true` **và** quyết định là ANSWER **và** đang ở hội thoại thật (không chạy trong khung chat thử ở
Bước 1). Tìm trong collection `history_<bot_id>` (tin nhắn đã được worker nền embed từ trước), loại tin đã có nguyên văn trong
prompt; có kết quả mới gọi thêm **1 lệnh LLM phụ** để hoàn thiện `proposed_answer`; lệnh gọi phụ lỗi thì **giữ nguyên** câu trả lời
của lệnh gọi chính (không làm mất câu trả lời của khách), chỉ ghi log + trace.

**(7) Bước D — Ghi** — [dashboard/service.py `reply_to_customer`](../app/dashboard/service.py): lưu `Message` (bot) kèm
`decision_trace` (JSON — quyết định, lý do, tín hiệu, số liệu token, số lệnh gọi LLM...) và 4 cột `usage_*` (đọc thật từ response
DeepSeek, không phải ước tính); cập nhật `ConversationState` (intent, slots, số lượt CLARIFY liên tiếp) và ghi `StructuredMemory`
(chỉ mục có `confidence ≥ memory_min_confidence`, vượt `memory_max_items` thì loại mục thấp/cũ nhất). **Bước E** ngay sau đó chỉ
**đặt cờ** `summary_pending` nếu tin chưa tóm tắt vượt `summary_trigger_tokens` (đếm token, không gọi LLM) — việc tóm tắt thật do
`workers/context_jobs.py` làm ở nền.

**(8) Trả về** — `{reply, conversation_id, visitor_id, last_message_id}` (hoặc `reply: null` khi nhân viên đang tiếp quản). Lỗi
LLM/ChromaDB ở bất kỳ bước nào bên trên → HTTP `502` kèm câu xin lỗi theo ngôn ngữ của bot; tin khách vẫn được giữ (đã commit từ
bước (2)); state/memory/tin bot **chỉ ghi khi đã có câu trả lời** (không ghi nửa chừng).

#### 4.3.3 Cấu hình nào thực sự tác động tới câu trả lời

Bước 1 nay có **3 mức cấu hình** (`config_tier`: Cơ bản/Nâng cao/Chuyên gia — trường ngoài tier đang chọn luôn dùng **giá trị mặc
định của hệ thống**, không phải giá trị đã lưu, để hạ tier không để lại cấu hình ẩn). Danh sách đầy đủ 21 thông số + khoảng hợp lệ:
[CONTEXT_ENGINE.md §6](CONTEXT_ENGINE.md); nguồn duy nhất trong code: `core/context_engine/settings.py` (`DEFAULTS`, `RANGES`,
`TIER_FIELDS`).

| Cấu hình (Bước 1) | Tác dụng thực tế | Trạng thái |
| --- | --- | --- |
| Lời chào (`greeting`) | Hiện ở widget | ✅ |
| Chỉ dẫn (`instructions`, tối đa 10.000 ký tự, markdown, 11 mẫu) | Đầu prompt hệ thống (`system`) | ✅ |
| Ngôn ngữ (`language`) | Nhãn + chỉ dẫn ngôn ngữ trong prompt, chữ giao diện widget | ✅ |
| Nhiệt độ (`temperature`), Token đầu ra tối đa (`max_tokens`) | Truyền cho DeepSeek; `max_tokens` + 600 token dự phòng JSON | ✅ |
| **Mọi trường của tier đang chọn** (bộ nhớ hội thoại, tóm tắt, RAG, theo dõi ý định/slot, ngưỡng, câu phản hồi khi thiếu thông tin...) | Ảnh hưởng trực tiếp tới Bước A/B/C như mô tả ở 4.3.2 | ✅ **(mới)** |
| **Chi phí ước tính mỗi câu hỏi** (Bước 1, không lưu vào DB) | Tính ngay theo cấu hình đang chọn trên form; xem [mục 5.4](#54-bước-1--thiết-lập) | ✅ **(mới)** |
| Mô hình AI | **Cố định** `deepseek-flash` trong code; ô chọn ở Bước 1 đã bỏ ([R16](#r-nhom-b), đã xử lý) | ✅ |
| **Thu thập thông tin khách** (`collect_customer_info`) | Quét số điện thoại/email trong tin khách, tạo/gắn `Customer` | ✅ **(mới, xem cập nhật [R17](#r-nhom-b))** |
| **Chuyển tiếp cho nhân viên** (`forward_to_staff`) | **Vẫn không có logic nào dùng** | ❌ ([R17](#r-nhom-b)) |
| **Tin nhắn khi vắng mặt** (`away_message`) | **Vẫn không có logic nào dùng** | ❌ ([R17](#r-nhom-b)) |
| `bot_settings.min_similarity` (cũ) | Cột còn trong DB nhưng **engine không còn đọc** | ❌ (thay bằng `rag_distance_threshold`) |

#### 4.3.4 Hệ thống xử lý sự cố ra sao

| Sự cố | Hành vi hiện tại |
| --- | --- |
| Bot chưa có tài liệu / tắt "Cơ sở tri thức" | Vẫn gọi LLM **không có ngữ cảnh RAG** (quy tắc "không bịa" trong prompt vẫn có hiệu lực) |
| RAG bật nhưng không đoạn nào đạt `rag_distance_threshold` | KHÔNG dùng `proposed_answer` của LLM → CLARIFY (câu hỏi lại đã cấu hình) hoặc DECLINE (câu từ chối đã cấu hình), tùy còn lượt hỏi làm rõ |
| Tài liệu tìm được vượt ngân sách token | CLARIFY thu hẹp phạm vi (chỉ đưa phần mở đầu từng chunk); hết lượt hỏi làm rõ thì trả lời bằng các chunk liên quan nhất còn vừa ngân sách |
| LLM trả JSON sai định dạng | Gọi lại 1 lần; sai tiếp lần 2 → HTTP `502` + câu xin lỗi; tin khách đã lưu |
| DeepSeek lỗi/timeout ở lệnh gọi chính | HTTP 502 + câu xin lỗi theo ngôn ngữ bot; tin khách đã lưu; **chưa ghi** state/memory/tin bot của lượt này |
| Lệnh gọi LLM phụ ở Bước F (Historical Retrieval) lỗi | **Không** trả 502 — dùng nguyên câu trả lời hợp lệ của lệnh gọi chính, chỉ ghi log + `decision_trace.history_lookup.status="error"` |
| ChromaDB mất kết nối khi hỏi | HTTP 502 như trên |
| Việc nền tóm tắt hội thoại lỗi | Worker `rollback()`, đặt khóa lùi lại 5 phút cho đúng hội thoại đó (`context-jobs:summary-backoff:<id>`) rồi thử lại; cờ `summary_pending` giữ nguyên, không mất dữ liệu chờ tóm tắt |
| ChromaDB mất kết nối khi huấn luyện tài liệu | Tài liệu `failed` + "Không kết nối được ChromaDB…"; xóa phần đã ghi dở |
| MinIO không đọc được tệp | Tài liệu `failed` + "Không đọc được tệp gốc…" |
| Worker/server tắt giữa lúc huấn luyện tài liệu | Khi khởi động lại, tài liệu `processing` được đặt về `pending` và **làm lại từ đầu** |
| Redis/Socket.IO lỗi | Chỉ mất cập nhật realtime; huấn luyện vẫn chạy (ghi log cảnh báo) |

### 4.4 Kiểm thử đã chạy cho luồng lõi

#### 4.4a Pha A — Nạp tri thức (không đổi so với báo cáo trước)

Đã chạy **41 kiểm tra** (Flask test client trên DB/Redis/ChromaDB/MinIO thật, worker nhúng huấn luyện thật) — 40 đạt lần đầu; mục còn lại lỗi do **dữ liệu kiểm thử**, đã chạy lại với dữ liệu phù hợp và đạt. **Các script kiểm thử này không nằm trong repo** ([R31](#r-nhom-c), vẫn đúng riêng cho Pha A — xem 4.4b bên dưới về Pha B).

| Nhóm | Nội dung | Kết quả |
| --- | --- | --- |
| Original | Upload → `draft` → xem trước → cấu hình riêng → huấn luyện | PASS |
| Cùng nhóm nguyên nhân | 8 loại tham số sai bị từ chối; thiếu/sai CSRF; body không phải JSON | PASS |
| Bình thường | MD và CSV; preview khớp `chunk_markdown`; số chunk huấn luyện = xem trước | PASS |
| Biên | `processing` → 409; bot team khác → 404; tài liệu không tồn tại → 404; tài liệu cũ dùng mặc định | PASS |
| Hồi quy | Huấn luyện lại thay hoàn toàn chunk cũ; hai tệp cùng nội dung, cấu hình khác → số chunk khác nhau (24 vs 3) | PASS |

**Đã kiểm thử thêm trên server thật (`python run.py`, 40 kiểm tra đều đạt):** đăng nhập bằng mật khẩu, tạo trợ lý, Bước 1 với mẫu, nút Tối ưu, upload → xem trước → huấn luyện bằng worker và **mô hình embedding thật**, nhận sự kiện Socket.IO realtime, **chat thật (RAG + DeepSeek)** với 5 loại câu hỏi, Web Widget công khai (domain hợp lệ/lạ, CORS, hội thoại nhiều lượt, chặn đoán `conversation_id`), Lịch sử chat, chặn truy cập chéo team, xóa tài liệu kèm vector.

**Vẫn chưa kiểm thử:** đăng nhập OAuth (Google/Facebook) và Magic Link, `embed.js` chạy trong trang website thật trên trình duyệt, các màn hình Bước 2/3/4 trên trình duyệt thật (chỉ Bước 1 đã kiểm bằng Edge headless), tải cao/đồng thời.

#### 4.4b Pha B — Context & Response Decision Engine (mới, **nằm trong repo**)

Khác với Pha A, bộ kiểm thử của Decision Engine **nằm trong `tests/`** và dùng **`unittest` của thư viện chuẩn** (dự án chưa có
pytest — đúng quy định ưu tiên thư viện hiện có; xem [tests/README.md](../tests/README.md)). **[TEST, 2026-09-22]** chạy
`env\Scripts\python.exe -m unittest discover -s tests -t .` → **321 kiểm tra, toàn bộ PASS** (gồm cả phần cần DB thử, chạy với
`DATABASE_URL` trỏ tới `aichatbot_engine_test` — **không đụng DB thật** `aichatbot`, xem quy ước trong `tests/README.md`).

| Tệp | Số test | Phạm vi |
| --- | --- | --- |
| `test_settings.py` | 13 | `EngineSettings.from_model`: tier nào thấy trường nào, ép giá trị lỗi/thiếu về mặc định, bất biến giữa các trường |
| `test_builder.py` | 46 | Chọn tin gần đây theo token, ngân sách RAG, áp lực ngữ cảnh, **đúng thứ tự 5 bước nén**, dựng message theo role |
| `test_retrieval.py` | 37 | Lọc theo `rag_distance_threshold`, loại trùng, mở rộng lân cận, `candidate_count`, cắt theo ngân sách token |
| `test_decision.py` | 34 | Từng nhánh của cây quyết định ANSWER/CLARIFY/DECLINE (test THUẦN, không DB/LLM) |
| `test_structured_cost.py` | 19 | Parse JSON có cấu trúc của LLM (kể cả JSON lỗi/thiếu trường), đọc `usage` (cache hit/miss) |
| `test_state.py` | 17 | Merge slot, `slot_completion`, chuyển intent, chọn mục bộ nhớ giữ lại khi vượt `memory_max_items` |
| `test_history.py` | 11 | Historical Retrieval (Bước F): tìm, loại tin đã có trong prompt, lỗi lệnh gọi phụ không làm hỏng câu trả lời chính |
| `test_engine.py` | 44 | `run_turn` đầu-cuối (A→B→C→[F]→D, LLM giả lập tất định) — kể cả nén ngữ cảnh, thu hẹp phạm vi, gọi lại khi JSON lỗi |
| `test_worker.py` | 8 | Vòng lặp `context_jobs`: tóm tắt hội thoại, embed lịch sử chat, khóa Redis riêng, lùi lại khi lỗi |
| `test_cost_estimate.py` | 21 | **Mới (2026-09-22):** khớp 4 ví dụ tính tay trong [BANG_GIA_API_AI.md](BANG_GIA_API_AI.md); cận dưới ≤ cận trên; mỗi cấu hình liên quan đổi đúng chiều số tiền |
| `test_setup.py` | 36 | Form Bước 1 theo tier (server thuần) **+** route thật (DB): lưu, hiển thị, gộp card giao diện, tooltip mỗi trường, chi phí ước tính qua route mới |
| `test_db_flow.py` | 35 | Luồng lưu trạng thái/bộ nhớ/tóm tắt/embedding qua DB thật (MariaDB thử + ChromaDB trong bộ nhớ + embedding giả tất định, Redis thật) |

Tokenizer dùng trong test là **thật** (chỉ đọc `tokenizer.json`, không nạp model ONNX 2,13 GiB) nên số token đo được sát với lúc
chạy thật; ChromaDB dùng client trong bộ nhớ, DeepSeek **không** được gọi thật (LLM giả lập tất định) — khác với 4.4a, bộ test này
**không đo được** lỗi tầng hạ tầng thật kiểu [R42](#r-nhom-c) (RecursionError khi chạy trong `eventlet` đã vá).

---

## 5. Chức năng giao diện — chi tiết từng màn hình

> Mỗi mục gồm: **mục đích · route · công nghệ · luồng · bảo mật · trạng thái/giới hạn**. Bảng route đầy đủ ở [Phụ lục A](#phụ-lục-a--bảng-route-đầy-đủ).

### 5.1 Đăng nhập, đăng ký, đăng xuất

**Trạng thái:** ✅ hoàn chỉnh
**Route:** `/auth/login`, `/auth/register`, `/auth/logout`, `/auth/google/*`, `/auth/facebook/*`, `/auth/magic/callback`, `/auth/me`.
**Tệp:** [app/auth/routes.py](../app/auth/routes.py), [app/auth/service.py](../app/auth/service.py), [app/csrf.py](../app/csrf.py), `templates/auth/`.
**Công nghệ:** Flask-Login, Werkzeug (băm mật khẩu), itsdangerous (ký token), Authlib (OAuth), Flask-Mail, Flask-Limiter, Redis.

```mermaid
flowchart TD
    Start["Trang Đăng nhập"] --> PW["Mật khẩu + Email"]
    Start --> ML["Magic Link"]
    Start --> GG["Google"]
    Start --> FB["Facebook"]
    Start --> RG["Tạo tài khoản"]
    PW --> AU["authenticate:<br/>băm mật khẩu, không phân biệt<br/>sai email hay sai mật khẩu"]
    ML --> TK["Ký token 15 phút<br/>gửi email SMTP"] --> CB["Bấm link: kiểm tra chữ ký + hạn<br/>+ đánh dấu ĐÃ DÙNG trong Redis"]
    GG --> OI["OIDC: lấy email đã xác minh"]
    FB --> OA["OAuth2 Graph API: lấy email"]
    RG --> NEW["Tạo Team + User + Owner"]
    AU --> LG
    CB --> FC["find_or_create_user"]
    OI --> FC
    OA --> FC
    FC --> LG["login: đặt session<br/>xoay CSRF token, chọn team đầu tiên"]
    NEW --> LG
    LG --> DB["Bảng điều khiển"]
```

| Chức năng | Chi tiết |
| --- | --- |
| **Mật khẩu** | `POST /auth/login`, giới hạn **10 lần/phút/IP**. Kiểm tra CSRF → chuẩn hóa email → `check_password_hash`. Tài khoản chỉ có OAuth/Magic Link (không có mật khẩu) sẽ không đăng nhập được bằng mật khẩu. Tùy chọn "Ghi nhớ" dùng remember-cookie của Flask-Login. |
| **Magic Link** | Nhập email → token = `URLSafeTimedSerializer(SECRET_KEY)` (muối `magic-link`) → email SMTP. Bấm link: kiểm tra chữ ký, **hạn 15 phút**, và **dùng một lần** (khóa Redis `SET NX` TTL 15 phút). Email chưa có tài khoản → **tự tạo** Team + User. |
| **Google** | Authlib với OIDC discovery, scope `openid email profile`. Cần cấu hình `GOOGLE_CLIENT_ID/SECRET`, nếu thiếu thì báo "chưa cấu hình". |
| **Facebook** | Khai báo tay 3 endpoint Graph API v19.0 (Facebook không có OIDC discovery). Bắt buộc có email. |
| **Đăng ký** | Kiểm tra họ tên, email hợp lệ, mật khẩu ≥ 8 ký tự, nhập lại khớp, email chưa tồn tại. Tạo **Team** (gói `free`) + **User** + **TeamMember Owner** trong một giao dịch. |
| **Phiên** | **Cookie ký, không lưu ở server** (`_user_id`, `team_id`, `csrf_token`). Đổi `SECRET_KEY` = mọi phiên mất hiệu lực; không thu hồi được từng phiên. |
| **CSRF** | Token 32 byte ngẫu nhiên lưu trong phiên, so sánh hằng-thời-gian; **xoay** sau đăng nhập/đăng xuất. Đăng xuất là **POST + CSRF**. |

**Rủi ro/giới hạn:** open redirect qua `?next=` ([R3](#r-nhom-a)); gộp tài khoản theo email OAuth ([R7](#r-nhom-a)); "Quên mật khẩu" chỉ là liên kết `#`; không có khóa tài khoản sau nhiều lần sai (chỉ giới hạn theo IP); chỉ vào **team đầu tiên** của user ([R38](#r-nhom-d)).

### 5.2 Khung giao diện chung

**Trạng thái:** ✅ hoàn chỉnh
- **Khung quản trị** (`shell.html`): thanh bên (Bảng điều khiển, Tin nhắn, FollowUp, Khách hàng, Báo cáo, API Tokens, Hồ sơ), thanh trên có huy hiệu gói (Free/Premium + ngày hết hạn) và nút "Trợ lý mới". Thu gọn menu bằng JS thuần.
- **Khung trợ lý** (`bot_workspace.html`): thanh trạng thái + **thanh 4 bước** (Thiết lập → Cơ sở tri thức → Xuất bản → Lịch sử chat) + chân trang điều hướng "Quay lại/Tiếp theo".
- Các mục **Tin nhắn, FollowUp, Khách hàng, Báo cáo, API Tokens, Hồ sơ** đều dẫn tới `/placeholder?title=…` — trang "đang được xây dựng" (⬜).
- Thông báo (flash) hiển thị ở `base.html`; toàn bộ đầu ra qua Jinja2 tự escape (chỉ có một chỗ dùng `|safe`: markup SVG icon do **server** cấp từ bảng cố định).

### 5.3 Bảng điều khiển và tạo trợ lý

**Trạng thái:** ✅ hoàn chỉnh
**Route:** `/dashboard`, `/bots/new`. **Tệp:** [dashboard/routes.py](../app/dashboard/routes.py), [service.py `get_bot_detail`](../app/dashboard/service.py).

- Chưa có bot → trang trống mời tạo trợ lý. Có bot → **thẻ tab theo từng bot**, thẻ chi tiết: chủ sở hữu, ngôn ngữ, ngày tạo, hạn gói.
- **Trạng thái "Đang hoạt động"** = bot có **ít nhất 1 tài liệu `trained`**. **"Đã cấu hình"** = có lời chào hoặc hướng dẫn.
- Số liệu hội thoại/tin nhắn trong 30 ngày (khách / bot / nhân viên); chỉ số FollowUp luôn `0` (chưa có dữ liệu).
- **Tạo trợ lý:** nhập tên → tạo `Bot` + `BotSettings` mặc định (tiếng Việt, temperature 0,7). **Không giới hạn số bot theo gói cước**, **không có xóa/đổi tên trợ lý ngoài Bước 1** ([R39](#r-nhom-d)).

### 5.4 Bước 1 — Thiết lập

**Trạng thái:** 🟡 làm một phần
**Route:** `GET/POST /bots/<id>/setup`, `POST /bots/<id>/setup/optimize-instructions` (nút Tối ưu), `POST /bots/<id>/preview-chat`
(khung chat thử), `POST /bots/<id>/setup/cost-estimate` **(mới)** — chi phí ước tính, xem điểm 4 bên dưới. **Công nghệ:** form HTML +
JS thuần (không thư viện soạn thảo), Jinja2, DeepSeek, `core/context_engine`. **Tệp:** [setup.html](../app/templates/bots/setup.html),
[assistant_templates.py](../app/dashboard/assistant_templates.py), [service.py](../app/dashboard/service.py),
[cost_estimate.py](../core/context_engine/cost_estimate.py).

Trang gồm 5 khối theo thứ tự:

1. **Chọn mẫu trợ lý** — lưới 11 thẻ (Tùy Chỉnh, Bán Hàng, Hỗ Trợ Khách Hàng, Tư Vấn Tuyển Sinh, Công Tác Sinh Viên, Bán Hàng B2B, Tuyển Dụng, Pháp Lý, Chính Sách Công, Khách Sạn, Nhà Hàng) + ô **Thu gọn** (trạng thái ghi nhớ trong `localStorage`). Dữ liệu mẫu nằm ở **một nguồn duy nhất** `assistant_templates.py` (tên, mô tả, icon do server cấp, lời chào, chỉ dẫn markdown, độ sáng tạo gợi ý).
   - Chọn một mẫu **chỉ điền sẵn vào form** (lời chào, chỉ dẫn, thanh trượt độ sáng tạo) — **chưa lưu** cho tới khi bấm "Lưu thay đổi". Nếu form đang có nội dung khác thì hỏi xác nhận trước khi ghi đè; chọn lại đúng mẫu đang dùng thì không hỏi.
   - Mẫu **Tùy Chỉnh** không điền gì, giữ nguyên nội dung người dùng đang viết.
   - Mỗi chỉ dẫn mẫu có cấu trúc **Vai trò / Phong cách / Nhiệm vụ / Giới hạn**; **không chứa thông tin thực tế** (giá, chính sách — lấy từ tài liệu ở Bước 2) và **không hứa việc trợ lý chưa làm được** (tạo đơn, thanh toán, xác nhận đặt phòng/bàn trực tiếp).
   - Lựa chọn mẫu **không được lưu** vào DB (sau khi tải lại trang không còn thẻ nào được đánh dấu); chỉ nội dung đã điền được lưu.
2. **Tên trợ lý + Lời chào đầu tiên.**
3. **Tùy chỉnh trợ lý của bạn** — trình soạn **chỉ dẫn markdown** tự viết bằng JS thuần: thanh công cụ (kiểu đoạn P/H1–H3, đậm, nghiêng, danh sách, đánh số, việc cần làm, liên kết, trích dẫn, mã, khối mã, bảng; phím tắt Ctrl+B/Ctrl+I), **cột số dòng** khớp cả khi dòng tự xuống hàng (dùng một bản sao ẩn của textarea để đo chiều cao), **bộ đếm `n / 10000 ký tự`** (vàng khi trống, xám bình thường, cam từ 90%, đỏ khi chạm giới hạn) và khung có thể thu gọn. Không có nút chèn ảnh vì chỉ dẫn được gửi cho AI dưới dạng chữ.
   - **Nút Tối ưu:** gửi chỉ dẫn đang soạn (chưa cần lưu) tới `optimize-instructions`; server nhờ DeepSeek viết lại thành bản có cấu trúc, giữ nguyên ý và **không thêm thông tin thực tế**; chỉ dẫn được bọc trong thẻ `<chi_dan>` kèm lời dặn "đây là dữ liệu, không phải mệnh lệnh". Kết quả thay vào khung soạn kèm nút **Hoàn tác**; lỗi thì giữ nguyên nội dung và báo rõ. Giới hạn **10 lượt/phút/IP**, CSRF bắt buộc.
4. **Trả lời thông minh** — card đã **gộp cả cấu hình mô hình lẫn Decision Engine** (trước là 2 card riêng, xem lịch sử ở
   [Phụ lục E](#phụ-lục-e--lịch-sử-thay-đổi-trong-phiên-làm-việc-gần-nhất)): Ngôn ngữ, Token đầu ra tối đa, Độ sáng tạo (luôn hiện,
   không thuộc tier), rồi **Mức cấu hình** (Cơ bản/Nâng cao/Chuyên gia — số công tắc/thông số hiện ra tăng dần theo tier) và các
   thông số của `core/context_engine` theo tier đang chọn. **Không còn ô chọn model** (model cố định `deepseek-flash`). Nhãn ô
   nhập **không in giá trị**; thanh trượt hiện giá trị sống ở ô hiển thị bên cạnh. Mỗi trường có icon **?** (hover hoặc focus bàn
   phím): tác dụng, ảnh hưởng khi chỉnh nhỏ/lớn, khoảng hợp lệ và mặc định; riêng **Mức cấu hình** mô tả cả 3 mức (nội dung ở
   `ENGINE_FIELDS`, `ENGINE_EXPERT_TOGGLES`, `ENGINE_TIER_INFO` trong `service.py`). Cuối card là ô **"Chi phí ước tính mỗi câu
   hỏi"** (mới): gọi `POST .../cost-estimate` mỗi khi đổi trường liên quan (debounce 350 ms, không lưu gì), hiện khoảng **thấp
   nhất–cao nhất** cho giờ thường và giờ cao điểm (bảng giá DeepSeek Flash, quy đổi VND — [BANG_GIA_API_AI.md](BANG_GIA_API_AI.md)),
   kèm bảng chi tiết từng thành phần token (chỉ dẫn, tóm tắt, bộ nhớ, tin gần đây, tài liệu, câu hỏi). Server dùng **đúng** phép
   kiểm tra + quy tắc tier như lúc lưu (`parse_engine_form`) nên báo lỗi giống hệt; là **ước lượng** (token đếm bằng tokenizer của
   model embedding, không phải tokenizer DeepSeek) — chi tiết công thức ở [mục 4.3.2](#432-các-bước-chi-tiết) bước (4) và
   [CONTEXT_ENGINE.md §7](CONTEXT_ENGINE.md).
5. **Chuyển tiếp và thu thập dữ liệu** — **1 trong 3 công tắc/ô đã có logic** (cập nhật so với báo cáo trước): bật "Thu thập thông
   tin khách hàng" thì mỗi tin khách được quét số điện thoại/email và gắn vào `Customer` (xem [mục 4.3.2](#432-các-bước-chi-tiết)
   bước (2)). "Chuyển tiếp cho nhân viên" và "Tin nhắn khi vắng mặt" **vẫn chưa có logic phía sau** ([R17](#r-nhom-b)).

| Trường | Kiểm tra phía server |
| --- | --- |
| Tên trợ lý | Bắt buộc, không rỗng |
| Lời chào | Cắt khoảng trắng |
| Chỉ dẫn | **Tối đa 10.000 ký tự** (quá thì **không lưu** và báo lỗi); xuống dòng CRLF của trình duyệt được chuẩn hóa về LF **trước khi đếm** để khớp con số người dùng thấy |
| Ngôn ngữ | **Danh sách trắng** `vi`/`en`; giá trị lạ thì giữ cấu hình cũ |
| Nhiệt độ | Ép vào `[0, 1]`; lỗi số → 0,7 |
| Token đầu ra tối đa | Ép vào `[10, 3000]`; lỗi số → 500 |
| Mức cấu hình (`config_tier`) | Phải là `basic`/`advanced`/`expert`, khác thì **báo lỗi, không lưu gì** |
| Mọi thông số của Decision Engine (21 trường, [CONTEXT_ENGINE.md §6](CONTEXT_ENGINE.md)) | Chỉ đọc trường **thuộc tier đang gửi**; sai kiểu/ngoài khoảng hợp lệ → **báo lỗi rõ theo tên trường, không tự ép về khoảng hợp lệ và không lưu bất kỳ trường nào của form** (khác hẳn kiểu "ép về khoảng" của `temperature`/`max_tokens`/`min_similarity` cũ) |
| Câu hỏi lại / câu từ chối khi thiếu thông tin (tier Chuyên gia) | Tối đa 500 ký tự; để trống = dùng câu mặc định theo ngôn ngữ |
| Chuyển tiếp / thu thập thông tin / tin vắng mặt | Lưu được; **thu thập thông tin đã có logic dùng** (mục 4.3.2), 2 cái còn lại **chưa** ([R17](#r-nhom-b)) |

CSRF bắt buộc; lưu xong quay lại chính trang. Cột `bot_settings.min_similarity` (cũ) **vẫn còn trong DB** nhưng form không còn ô
nào ghi vào nó và engine không đọc — thay bằng `rag_distance_threshold` (mục "Mức cấu hình" ở trên).

### 5.5 Bước 2 — Cơ sở tri thức (danh sách tài liệu)

**Trạng thái:** ✅ hoàn chỉnh
**Route:** `GET /bots/<id>/knowledge`, `POST .../upload`, `POST .../<doc>/delete`, `GET .../status`. **Tệp:** [knowledge.html](../app/templates/bots/knowledge.html), [service.py](../app/dashboard/service.py), [events.py](../app/dashboard/events.py).
**Công nghệ:** Jinja2, XMLHttpRequest (thanh tiến độ upload), Socket.IO client, MinIO, ChromaDB.

**Thành phần giao diện:**

1. **Thẻ dung lượng** — đã dùng/tối đa (50 MB/bot), phần trăm, số tài liệu, tổng chunk, còn lại; đổi màu **cảnh báo từ 80%**, **đầy** ở 100%. Tệp thất bại và tệp `draft` **vẫn chiếm dung lượng** cho đến khi bị xóa ([R20](#r-nhom-b)).
2. **Vùng kéo-thả upload** — nhiều tệp cùng lúc; kiểm tra ngay ở trình duyệt (kích thước, quota) trước khi gửi; thanh % tải lên.
3. **Bộ lọc** — tìm theo tên (không phân biệt hoa thường), lọc theo loại tệp (txt/md/csv).
4. **Bảng tài liệu** — tên, kích thước, số chunk, **trạng thái trực tiếp**, ngày, nút **"Cấu hình chunk"** (hiện với `draft`/`trained`/`failed`) và **"Xóa"**.

**Trạng thái hiển thị:** Chờ cấu hình chunk (`draft`) · Đang chờ xử lý (`pending`) · Đang xử lý **x%** + `done/total chunk` + **ước tính phút còn lại** (`processing`) · ✓ Đã huấn luyện · Thất bại kèm lý do ngắn gọn.

**Cập nhật trực tiếp:** Socket.IO vào phòng `bot:<id>` (xem [4.2.10](#4210-trạng-thái-realtime-tới-trình-duyệt)); mất kết nối thì tự polling 5 giây; hiện chấm xanh "Cập nhật trực tiếp"; thông báo (toast) khi xong/thất bại.

> **Đã bỏ:** khối "Cấu hình cắt chunk" chung cho cả trợ lý và trang `/knowledge/<id>/edit` (chỉnh từng chunk sau huấn luyện) — theo yêu cầu. Hàm `rag_engine.get_document_chunks/update_chunk` và các khung `app/knowledge/` vì thế **không còn ai gọi** ([R40](#r-nhom-d)).

### 5.6 Bước 2b — Cấu hình chunk cho từng tệp (mới)

**Trạng thái:** ✅ hoàn chỉnh
**Route:** `GET /bots/<id>/knowledge/<doc>/chunks`, `POST .../chunks/preview` (JSON), `POST .../train`. **Tệp:** [chunk_config.html](../app/templates/bots/chunk_config.html), [routes.py](../app/dashboard/routes.py), [service.py](../app/dashboard/service.py).

```mermaid
flowchart LR
    U["Người dùng đổi<br/>chunk size / overlap"] --> DB2["Chờ 400 ms<br/>(debounce)"]
    DB2 --> REQ["POST /chunks/preview<br/>header X-CSRF-Token"]
    REQ --> SV["Server: đọc tệp MinIO<br/>chuẩn hóa markdown<br/>chunk_markdown"]
    SV --> RS["JSON: chunk, số token,<br/>tổng ký tự, token trung bình"]
    RS --> UI["Tô màu tuần hoàn 6 màu<br/>mỗi chunk 1 khối + nhãn token"]
    UI --> BT{"Hài lòng?"}
    BT -- "Có" --> TRN["POST /train: lưu cấu hình<br/>status = pending"]
    BT -- "Chưa" --> U
```

- **Hiển thị:** tổng ký tự, số chunk, token trung bình/chunk; mỗi chunk là một khối màu kèm "Chunk N · X token", **nội dung chunk chính là chuỗi sẽ được embed** (gồm dòng đường dẫn tiêu đề).
- **An toàn hiển thị:** nội dung tài liệu gán bằng `textContent` — **không bao giờ diễn giải thành HTML** (chống XSS từ chính tài liệu).
- **Chống đua yêu cầu:** chỉ vẽ kết quả của **lần gọi mới nhất** khi người dùng gõ liên tục; nút huấn luyện bị khóa trong lúc đang tính hoặc khi có lỗi.
- Overlap tối đa tự cập nhật theo chunk size (30%).
- **Giới hạn:** chưa có chỗ sửa nội dung markdown; chưa highlight *phần overlap* riêng; mỗi lần xem trước tính lại từ đầu, không cache ([R21](#r-nhom-b)); 409 hiển thị trang lỗi thô ([R34](#r-nhom-c)).

### 5.7 Bước 3 — Xuất bản

**Trạng thái:** ✅ hoàn chỉnh
**Route:** `GET/POST /bots/<id>/publish`, `POST .../publish/appearance`, `POST /bots/<id>/preview-chat`. **Tệp:** [publish.html](../app/templates/bots/publish.html), [widget/appearance.py](../app/widget/appearance.py), [widget/icons.py](../app/widget/icons.py).

- **Kênh kết nối:** *Web Widget* (thật) + thẻ *Facebook Messenger, Zalo OA, WhatsApp Business* (chỉ giao diện "đang phát triển").
- **Domain được phép nhúng:** người dùng khai báo domain; chuẩn hóa (`https://www.Shop.vn/abc` → `shop.vn`). Widget chỉ hoạt động trên domain đó và **subdomain** của nó.
- **Mã nhúng** để sao chép: `<script src="…/widget/embed.js" data-bot-id="ID"></script>`.
- **Tùy chỉnh giao diện chatbox** (mọi giá trị đi qua **một nguồn duy nhất** `appearance.py`: lựa chọn hợp lệ, mặc định, kiểm tra, payload):

| Tùy chọn | Giá trị | Kiểm tra |
| --- | --- | --- |
| Icon | 6 icon (Bong bóng, Tin nhắn, Robot, Hỗ trợ, AI, Trợ giúp) | Chỉ nhận **khóa** trong bảng; markup SVG do server cấp |
| Màu chủ đạo | 8 màu gợi ý + màu tự chọn | Chỉ nhận đúng `#RRGGBB` (vì đi vào CSS) |
| Kích thước nút | 40–96 px | Số nguyên trong khoảng |
| Kiểu nút | Tròn / Bo góc | Danh sách trắng |
| Vị trí | Trái / Phải | Danh sách trắng |
| Cỡ khung chat | Nhỏ 320×460 · Vừa 360×540 · Lớn 420×640 | Danh sách trắng |

- **Xem trước trực tiếp:** khung giả lập trang web chạy **đúng `embed.js` thật** (`AIChatbotWidget.create`) nên không thể lệch với widget thật; đổi bất kỳ tùy chọn nào là đổi ngay; có báo "● Có thay đổi chưa lưu".
- **Chat thử** trong khung xem trước gọi `POST /bots/<id>/preview-chat`: đi qua **đúng luồng RAG + cấu hình đã lưu** như widget thật nhưng **không lưu hội thoại** (không lẫn vào Lịch sử chat); giới hạn **30 lượt/phút**, tối đa 1000 ký tự, lịch sử 6 tin × 600 ký tự.

### 5.8 Web Widget công khai

**Trạng thái:** ✅ hoàn chỉnh
**Tệp:** [app/widget/embed.js](../app/widget/embed.js) (307 dòng, JS thuần), [routes.py](../app/widget/routes.py), [service.py](../app/widget/service.py).

```mermaid
sequenceDiagram
    participant P as Trang website khách
    participant E as embed.js
    participant F as Flask /widget
    P->>E: Nạp script (data-bot-id)
    E->>E: Đọc localStorage: visitorId, conversationId, lịch sử
    E->>F: GET /widget/api/ID/config
    F->>F: Kiểm tra Origin/Referer thuộc domain đã khai báo
    F-->>E: tên, lời chào, ngôn ngữ, giao diện, chữ nút
    E->>P: Dựng khung chat trong Shadow DOM
    E->>F: POST /widget/api/ID/messages (mỗi lần gửi)
    F-->>E: reply, conversation_id, visitor_id
    E->>E: Lưu tối đa 40 tin vào localStorage
```

| Đặc điểm | Chi tiết |
| --- | --- |
| **Cô lập giao diện** | **Shadow DOM**: CSS của website khách không ảnh hưởng widget và ngược lại |
| **Chống XSS** | Mọi nội dung (kể cả câu trả lời AI) gán bằng `textContent` |
| **Responsive** | ≤ 480 px: khung chat thành "bottom sheet" 85% chiều cao |
| **Ghi nhớ** | `visitorId` (UUID), `conversationId`, tối đa 40 tin trong `localStorage`; nếu `localStorage` bị chặn vẫn chạy nhưng không lưu |
| **Lỗi** | Domain chưa cấp phép/bot không tồn tại → widget **không hiển thị** (chỉ ghi `console.warn`); lỗi khi gửi → bong bóng đỏ với thông báo lỗi |
| **Bộ nhớ đệm** | `embed.js` cache 300 giây; `/config` gọi lại mỗi lần tải trang → chỉnh giao diện có hiệu lực sau tối đa vài phút |
| **Dùng lại mã** | Cùng `embed.js` phục vụ cả website khách lẫn khung xem trước ở Bước 3 |

**API công khai:**

| Endpoint | Yêu cầu | Phản hồi thành công | Lỗi |
| --- | --- | --- | --- |
| `GET /widget/api/<id>/config` | — | `{name, greeting, language, icon, color, size, shape, position, window, placeholder, send, error}` | `403` domain chưa cấp phép · `404` không có bot |
| `POST /widget/api/<id>/messages` | `{message, visitor_id, conversation_id}` | `{reply, conversation_id, visitor_id}` | `400` tin rỗng/quá dài · `403` · `404` · `429` quá 20 lượt/phút · `502` LLM/Chroma lỗi |

### 5.9 Bước 4 — Lịch sử chat

**Trạng thái:** 🟡 làm một phần
**Route:** `GET /bots/<id>/history`. **Tệp:** [history.html](../app/templates/bots/history.html).

- **Hai khung:** danh sách phiên (trái) và nội dung hội thoại (phải, tin khách bên phải – tin bot bên trái, kèm giờ).
- **Lọc:** theo kênh (hiện chỉ có `web_widget`) và tìm theo **tên khách hoặc mã khách vãng lai**; hiển thị "Khách #abc123" nếu chưa có tên.
- **Giới hạn:** lấy **200 phiên mới nhất** rồi mới tìm kiếm trong 200 phiên đó (không tìm được phiên cũ hơn); **chỉ đọc**, không trả lời được (Inbox chưa làm); nội dung hiển thị qua Jinja2 tự escape.

### 5.10 Các chức năng chưa hoàn thiện

**Trạng thái:** ⬜ chưa hoàn thiện
| Chức năng | Hiện trạng thực tế |
| --- | --- |
| **Tin nhắn (Inbox)**, **FollowUp**, **Khách hàng**, **Báo cáo**, **API Tokens**, **Hồ sơ** | Mục menu dẫn tới trang "đang xây dựng". Model dữ liệu đã có nhưng chưa có giao diện/logic. |
| **API `/api/*`** (bots, knowledge, inbox, followups, customers, reports, api-tokens, profile — 34 route) | Là **khung** (`raise NotImplementedError`) → khi gọi (đã đăng nhập) trả **HTTP 500** ([R36](#r-nhom-d)) |
| **`workers/send_followups.py`** | Quét FollowUp đến hạn rồi **đặt "sent" mà không gửi gì** ([R35](#r-nhom-d)) |
| **Kênh Facebook/Zalo/WhatsApp** | Chỉ là thẻ giao diện |
| **Quên mật khẩu, đổi mật khẩu, xóa trợ lý, phân quyền theo vai trò, chuyển team** | Chưa có |

---

## 6. Tài nguyên phần cứng

> **Phương pháp:** số liệu **[ĐO]** lấy ngày 2026-09-19 trên máy phát triển ở trạng thái **rảnh** (server Flask **đang tắt** lúc đo). Phần **[ƯỚC TÍNH]** là suy luận từ kiến trúc — **chưa đo tải thật**; cách tự đo lại ở [Phụ lục D](#phụ-lục-d--cách-tự-đo-lại-tài-nguyên).

**Máy đo [ĐO]:** Intel Core i5-1135G7 (4 nhân / 8 luồng, 2,4 GHz) · RAM **15,7 GB** (chỉ còn **2,9 GB trống** khi đo vì các ứng dụng khác đang chạy) · Windows 11 · Docker Desktop (WSL2, giới hạn VM **7,62 GiB**) · ổ C: 450 GB, D: 25 GB.

### 6.1 Dung lượng đĩa

| Thành phần | Dung lượng | Nguồn | Ghi chú |
| --- | --- | --- | --- |
| **Mô hình embedding** (`models/`) | **2,13 GiB** (≈ 2,29 GB) | [ĐO] | Trong đó `model.onnx_data` = 2.266.886.160 byte, tokenizer 17 MB |
| **Môi trường ảo Python** (`env/`) | **1,35 GB** | [ĐO] | `torch` 502 MB, `scipy` 108, `transformers` 102, `kubernetes` 74, `sympy` 65, `onnxruntime` 44… |
| **Image Docker** của 3 dịch vụ | **≈ 1,28 GB** | [ĐO] | `chromadb` 826 MB + `minio` 241 MB + `redis` 212 MB |
| Mã nguồn + tài liệu | **3,1 MB** (105 tệp) | [ĐO] | Chưa tính `env/`, `models/` |
| Dữ liệu ChromaDB (`C:\chromadb\data`) | 9,8 MB | [ĐO] | Gần như trống (chỉ `chroma.sqlite3` 5,6 MB + thư mục segment) |
| Dữ liệu MinIO (volume `minio-data`) | 46 KB (3 tệp, 11.684 byte) | [ĐO] | |
| Dữ liệu MariaDB (schema `aichatbot`) | **384 KB** | [ĐO] | |
| **Tổng nền (chưa có dữ liệu người dùng)** | **≈ 4,8 GB** | [ĐO] | 2,13 + 1,35 + 1,28 |

**Mức tăng theo dữ liệu người dùng [ƯỚC TÍNH]:**

| Dữ liệu | Công thức ước tính | Cơ sở |
| --- | --- | --- |
| **Tệp gốc (MinIO)** | ≈ đúng dung lượng tệp; **tối đa 50 MB/bot** (`KNOWLEDGE_STORAGE_LIMIT_MB`), 5 MB/tệp | [CODE] hạn mức có sẵn |
| **ChromaDB** | ≈ **4–9 lần** dung lượng tệp gốc | Mỗi chunk lưu vector 1024 × 4 byte = **4 KB** + nội dung (~1–2 KB) + chỉ mục HNSW + metadata; ≈ 600–1.000 chunk/MB tài liệu |
| **Một bot đầy quota 50 MB** | ≈ **0,2–0,45 GB** trong Chroma | 30.000–50.000 chunk |
| **MariaDB** | Tin nhắn ≈ 0,3–1 KB/dòng → **1 triệu tin ≈ 0,3–1 GB** | Kích thước dòng + chỉ mục |

### 6.2 RAM

| Thành phần | RAM | Nguồn |
| --- | --- | --- |
| MariaDB (`mysqld`) | Private **210 MB** (working set 26 MB) | [ĐO] |
| Redis (container) | **11 MiB** | [ĐO] |
| ChromaDB (container) | **72 MiB** (khi gần như trống) | [ĐO] |
| MinIO (container) | **79 MiB** | [ĐO] |
| Docker Desktop: VM WSL2 + giao diện | ≈ **0,9 GB** (`vmmemWSL` 700 MB + `vmmem` 185 MB) + ≈ **0,3 GB** tiến trình Docker Desktop | [ĐO] |
| **Ứng dụng Flask + worker + mô hình embedding** | **≈ 3–4 GiB** | [ƯỚC TÍNH] — chưa đo |
| Ứng dụng Flask (chỉ code, không model) | vài trăm MB | [ƯỚC TÍNH] |

**Cơ sở ước tính cho tiến trình ứng dụng:** trọng số fp32 **2,13 GiB** được nạp hẳn vào RAM (ONNX Runtime) + tokenizer 250.000 từ + thư viện (transformers, chromadb, langchain…) + vùng nhớ tạm khi chạy lô. **Điều chắc chắn rút ra từ code:**

- Mô hình được nạp **mỗi tiến trình một bản** ([rag_engine.py:64](../core/rag_engine.py#L64)).
- **README khuyến nghị production tách worker riêng** → khi đó **web và worker mỗi bên một bản** → **≈ 6–8 GiB chỉ riêng mô hình**.
- Nếu chạy `gunicorn` nhiều worker process → **mỗi process thêm ≈ 2,3 GiB**.
- Bộ nhớ ChromaDB tăng theo số vector (chỉ mục HNSW nằm trong RAM): **≈ 4,2–4,5 KB/chunk** → bot đầy quota ≈ **130–225 MB RAM**.

**Máy đo chỉ còn 2,9 GB trống** nên **chưa dám nạp thêm mô hình để đo** (nguy cơ làm treo máy) → mục RAM ứng dụng vẫn là ước tính.

### 6.3 CPU và thời gian xử lý

Hệ thống **không dùng GPU** (`CPUExecutionProvider`), nên **CPU là điểm nghẽn chính**.

| Việc | Tải CPU | Ước tính | Nguồn |
| --- | --- | --- | --- |
| **Embedding tài liệu** (huấn luyện) | Dùng **toàn bộ nhân** (ONNX Runtime) | Mô hình XLM-R cỡ lớn (24 tầng, hidden 1024): khoảng **0,3–2 giây/chunk** trên CPU 4 nhân | [ƯỚC TÍNH] |
| **Huấn luyện 1 MB tài liệu** (≈ 600–1.000 chunk) | Kéo dài | ≈ **3–33 phút** | [ƯỚC TÍNH] |
| **Huấn luyện 5 MB** (≈ 3.000–5.000 chunk) | Kéo dài | ≈ **15 phút – 2,8 giờ** | [ƯỚC TÍNH] |
| **Xem trước chunk** | Tokenize toàn bộ tệp (không embed) | Tính lại **mỗi lần đổi tham số**, tệp lớn có thể mất nhiều giây | [CODE] (không cache) |
| **Trả lời 1 câu hỏi** | Embedding câu hỏi ngắn: nhẹ | ≈ 0,05–0,3 giây CPU; phần lớn thời gian là **chờ DeepSeek** (mạng, 1–10 giây) | [ƯỚC TÍNH] |
| Khởi động | Nạp mô hình | ≈ **20 giây** (theo chú thích trong code) | [CODE] |

Ghi chú: code có hiển thị **"còn ~X phút"** và mô tả "tài liệu lớn có thể chạy hàng chục phút" — phù hợp với ước tính trên.

**Hệ quả kiến trúc quan trọng:** vì web và worker **chia chung CPU**, khi đang huấn luyện tài liệu lớn thì **chat của khách và giao diện đều chậm đi** ([R25](#r-nhom-c)).

### 6.4 Mạng

| Hướng | Điểm đến | Ghi chú |
| --- | --- | --- |
| Vào | Cổng **5000** (ứng dụng) | Nên đặt sau reverse proxy + HTTPS |
| Ra | `api.deepseek.com` (HTTPS) | Chi phí theo lượng token; **không có hạn mức nào ngăn lạm dụng** ngoài rate limit IP ([R5](#r-nhom-a)) |
| Ra | `smtp.gmail.com:587` | Magic Link |
| Ra | Google, Facebook | OAuth |
| Trình duyệt người dùng | Google Fonts | Phông chữ |
| Nội bộ | 3306, 6379, 8000, 9000/9001 | **Docker đang mở các cổng này trên `0.0.0.0`** ([R4](#r-nhom-a)) |

### 6.5 Cấu hình đề xuất

> **[ƯỚC TÍNH — khuyến nghị dựa trên số đo và cấu trúc code]**, cần kiểm chứng bằng tải thật.

| Kịch bản | CPU | RAM | Đĩa | Ghi chú |
| --- | --- | --- | --- | --- |
| **Phát triển / demo** (1–3 bot, tài liệu nhỏ) | 4 nhân | **8 GB tối thiểu, 16 GB thoải mái** | 10 GB | Máy đo đủ chạy nhưng chỉ còn 2,9 GB trống khi mở thêm ứng dụng khác |
| **Sản phẩm nhỏ, chạy chung 1 máy** (≤ 20 bot, ≤ 2 GB tài liệu tổng) | **8 vCPU** | **16 GB** | **50 GB SSD** | Web + worker + 4 dịch vụ chung máy; nên hạn chế giờ huấn luyện |
| **Tách worker** (khuyến nghị hơn khi có nhiều khách) | Web 2–4 vCPU · Worker 8 vCPU | Web ≥ 6 GB · Worker ≥ 6 GB · dịch vụ dữ liệu 4 GB | 50 GB+ SSD | **Trả giá 2 bản model** (~2,3 GiB × 2), đổi lại chat không bị chậm khi huấn luyện |
| **Tăng tốc mạnh** | Có GPU | — | — | Cần đổi `CPUExecutionProvider` sang `CUDAExecutionProvider` (chưa hỗ trợ trong code) |

**Đòn bẩy giảm tài nguyên (chưa làm):** dùng mô hình embedding nhỏ hơn (đổi mô hình = phải embed lại toàn bộ tài liệu); lượng tử hóa int8 (nhẹ ~4 lần nhưng cần kiểm định chất lượng tiếng Việt); giới hạn số luồng ONNX cho worker để chat không bị chèn ép.

---

## 7. Cài đặt, triển khai và đưa lên GitHub

### 7.1 Chuẩn bị hạ tầng

Cần 4 dịch vụ chạy sẵn: **MySQL/MariaDB (3306)**, **Redis (6379)**, **ChromaDB (8000)**, **MinIO (9000/9001)**.

> ⚠️ **Bucket MinIO không được tạo tự động** — không có đoạn code nào gọi `make_bucket`. Phải **tạo thủ công bucket `aichatbot`** (hoặc tên đặt ở `MINIO_BUCKET`) trước lần upload đầu tiên, nếu không upload sẽ lỗi. README hiện chưa nhắc điều này.

### 7.2 Các bước

```bash
# 1. Kích hoạt môi trường ảo và cài thư viện
env\Scripts\activate
pip install -r requirements.txt

# 2. Tạo .env từ mẫu rồi điền giá trị thật
cp .env.example .env

# 3. Tạo/cập nhật schema (13 migration)
flask db upgrade

# 4. (Tùy chọn) tài khoản demo: admin@example.com / Admin@123
python scripts/seed_admin.py

# 5. Chạy (KHÔNG dùng `flask run` vì không hỗ trợ WebSocket)
python run.py            # http://localhost:5000
```

Thư mục `models/Vietnamese_Embedding/` phải có sẵn (bản ONNX). **README không mô tả cách tạo/tải nó.**

### 7.3 Biến môi trường ([config.py](../config.py))

| Nhóm | Biến | Mặc định | Ghi chú |
| --- | --- | --- | --- |
| Lõi | `SECRET_KEY` | `dev-secret-key-change-me` | **Bắt buộc đổi** ([R1](#r-nhom-a)) |
| DB | `DATABASE_URL` | `mysql+pymysql://root:@localhost:3306/aichatbot` | root **không mật khẩu** |
| Redis | `REDIS_URL` | `redis://localhost:6379/0` | Dùng cho rate limit + Socket.IO + khóa |
| Chroma | `CHROMA_HOST`, `CHROMA_PORT` | `localhost`, `8000` | |
| MinIO | `MINIO_HOST/PORT/ACCESS_KEY/SECRET_KEY/SECURE/BUCKET` | `localhost`, `9000`, rỗng, rỗng, `false`, `aichatbot` | `.env.example` dùng `minioadmin` |
| OAuth | `GOOGLE_CLIENT_ID/SECRET`, `FACEBOOK_CLIENT_ID/SECRET` | rỗng | Rỗng = nút đăng nhập báo "chưa cấu hình" |
| Mail | `MAIL_SERVER/PORT/USE_TLS/USERNAME/PASSWORD/DEFAULT_SENDER`, `MAIL_SUPPRESS_SEND` | `smtp.gmail.com`, `587`, `true`… | `MAIL_SUPPRESS_SEND=true` để tắt gửi thật khi test |
| LLM | `DEEPSEEK_API_KEY` | rỗng | Khóa API **bắt buộc** để chat và nút Tối ưu hoạt động. Model **cố định trong code** (`core/llm_client.py`); biến `DEEPSEEK_MODEL` cũ không còn được đọc |
| Embedding | `EMBEDDING_MODEL_NAME`, `EMBEDDING_MODEL_PATH` | `AITeamVN/Vietnamese_Embedding`, `./models/Vietnamese_Embedding` | Model nạp từ đường dẫn cục bộ |
| Socket.IO | `SOCKETIO_CHANNEL` | `flask-socketio` | Đổi khi nhiều môi trường dùng chung 1 Redis |
| Worker | `EMBEDDED_WORKER` | `true` | `false` để tách worker riêng |
| Giới hạn | `KNOWLEDGE_MAX_FILE_MB`, `KNOWLEDGE_STORAGE_LIMIT_MB` | `5`, `50` | Theo từng tệp / từng bot |

### 7.3b Hai worker nền, mỗi worker hai chế độ chạy

Có **hai worker độc lập**, mỗi worker có khóa Redis riêng nên không chặn nhau:

| Worker | Việc | Khóa Redis | Chạy nhúng cùng `run.py` khi nào? |
| --- | --- | --- | --- |
| `workers/process_documents.py` | Huấn luyện tài liệu (Pha A) | `knowledge-worker-lock` | `EMBEDDED_WORKER=true` (mặc định) |
| `workers/context_jobs.py` **(mới)** | Rolling summary + embed tin nhắn cho Historical Retrieval (việc nền của Decision Engine, [mục 4.3](#43-pha-b--context--response-decision-engine)) | `context-jobs-worker-lock` | `EMBEDDED_WORKER=true` (mặc định) — cùng biến với worker huấn luyện |

| Chế độ | Cách chạy | Ưu | Nhược |
| --- | --- | --- | --- |
| **Nhúng** (mặc định) | `python run.py` — chạy **cả 2 worker + model embedding** trong cùng tiến trình web | Đơn giản, 1 tiến trình, 1 bản model | Huấn luyện/tóm tắt chia CPU với web; tắt web = ngắt cả 2 |
| **Tách riêng** | `EMBEDDED_WORKER=false` rồi chạy `python -m workers.process_documents` **và** `python -m workers.context_jobs` là 2 tiến trình riêng | Web không bị chèn ép bởi việc nền | Worker huấn luyện tài liệu nạp thêm **1 bản model embedding** (~gấp đôi RAM); `context_jobs` **không** nạp model embedding riêng (chỉ gọi lại `rag_engine`/DeepSeek qua `run_blocking`, xem [rag_engine.py:44](../core/rag_engine.py#L44)) |

Biến `EMBEDDED_WORKER` áp dụng cho **cả 2** worker cùng lúc (không tách được từng cái) — xem `start_embedded`/`start_context_jobs`
trong [run.py](../run.py). Chỉ 1 worker mỗi loại hoạt động tại một thời điểm nhờ khóa Redis (nhưng xem [R28](#r-nhom-c), riêng cho
`process_documents`; `context_jobs` chưa được rà theo cùng câu hỏi).

### 7.4 Đưa lên GitHub — các việc **phải làm trước**

| # | Việc | Lý do |
| --- | --- | --- |
| 1 | **Thêm `models/` vào `.gitignore`** | Tệp `model.onnx_data` **2,27 GB** vượt xa giới hạn 100 MB/tệp của Git; Git LFS cũng bị giới hạn dung lượng/tệp theo gói. Hiện `.gitignore` **chưa** loại `models/` ([R41](#r-nhom-e)). Nên ghi hướng dẫn tải/đặt model vào README. |
| 2 | Xác nhận `.env` **không** vào commit | `.env` có khóa thật (DeepSeek, SMTP, Google, Facebook). Đã có trong `.gitignore`, nhưng **thư mục chưa phải git repo** — kiểm tra `git status` trước commit đầu tiên. Nếu từng lộ ở đâu đó, **đổi khóa**. |
| 3 | (Tùy chọn) bỏ `templates/*.zip` | 14 tệp ≈ 2,8 MB bản thiết kế demo, app không dùng |
| 4 | Ghim phiên bản trong `requirements.txt` | Hiện `chromadb`, `langchain*`, `transformers`, `onnxruntime` không ghim ([R32](#r-nhom-c)) |
| 5 | Bổ sung README: tạo bucket MinIO, cách lấy model, biến môi trường bắt buộc | Người khác clone về sẽ không chạy được nếu thiếu |

`env/`, `__pycache__/`, `*.pyc`, `*.log`, `.chroma/`, `uploads/` đã được ignore.

---

## 8. Rủi ro và nợ kỹ thuật

> **Cách đọc mục này.** Các mục dưới đây được nhận diện bằng **đọc mã nguồn và đo đạc**. Riêng **R13 là suy ra từ đọc code, chưa tái hiện** (đã ghi rõ tại mục đó). Mỗi mục có **vị trí trong code** để kiểm tra lại. Mức độ:
> **CAO** = có thể gây mất an toàn/mất dữ liệu/chi phí lớn hoặc chặn triển khai · **TB** = gây lỗi, hiểu nhầm hoặc khó bảo trì · **THẤP** = nên dọn.
>
> **Lưu ý về nguồn gốc các giải pháp tạm:** tôi không có lịch sử chi tiết từng lần sửa ở các phiên trước, nên **không thể quy từng mục cho một lần sửa cụ thể**. Cột "Loại" phân biệt: **Tạm** (đánh đổi/giải pháp nhanh còn để lại), **Lỗ hổng** (thiếu biện pháp), **Chưa xong** (chưa hoàn thiện). Các mục phát sinh **trong phiên này** được đánh dấu ★ để bạn biết rõ.

### R-nhom-a — A. Bảo mật và cấu hình <a id="r-nhom-a"></a>

| ID | Mức | Loại | Vấn đề | Bằng chứng | Hậu quả | Đề xuất |
| --- | --- | --- | --- | --- | --- | --- |
| **R1** | **CAO** | Lỗ hổng | `SECRET_KEY` trong `.env` **đang đúng bằng khóa mặc định công khai** `dev-secret-key-change-me` (đã kiểm tra, không in giá trị khóa khác) | [config.py:9](../config.py#L9) | Ai biết khóa (nằm trong mã nguồn công khai) có thể **giả mạo cookie phiên** (đăng nhập thành ai cũng được) và **giả mạo token Magic Link** | Sinh khóa ngẫu nhiên ≥ 32 byte; **từ chối khởi động** nếu ở môi trường production mà dùng khóa mặc định |
| **R2** | **CAO** | Tạm | `run.py` **cố định `debug=True`** và bind **`0.0.0.0`**; Flask-SocketIO khi debug + eventlet **gắn Werkzeug debugger** | [run.py:16](../run.py#L16), [run.py:24](../run.py#L24); `flask_socketio/__init__.py` dòng 629–651 | Debugger tương tác lộ ra mạng; tự nạp lại code khi chạy | Đọc `debug` từ biến môi trường, mặc định **tắt**; production chạy qua server WSGI phù hợp (gunicorn eventlet) sau proxy |
| **R3** | **CAO** | Lỗ hổng | **Open redirect** ở đăng nhập: `redirect(request.args.get("next"))` không kiểm tra | [auth/routes.py:90](../app/auth/routes.py#L90) | Link `…/auth/login?next=https://trang-lua-dao.com` chuyển người dùng sang trang giả sau khi đăng nhập thật | Chỉ chấp nhận đường dẫn tương đối nội bộ (bắt đầu bằng `/`, không `//`, không có scheme/host) |
| **R4** | **CAO** | Lỗ hổng | Redis, ChromaDB, MinIO **mở cổng trên `0.0.0.0` không xác thực**; `.env.example` dùng `minioadmin/minioadmin123`; DB mặc định `root` không mật khẩu | `docker inspect` **[ĐO]**; [config.py:12](../config.py#L12), `.env.example` | Ai cùng mạng có thể đọc/ghi/xóa toàn bộ tri thức, tệp, và làm hỏng rate limit | Bind `127.0.0.1` hoặc mạng Docker nội bộ; bật mật khẩu Redis; đổi khóa MinIO; DB dùng user riêng có mật khẩu; chặn cổng ở firewall |
| **R5** | TB | Lỗ hổng | Widget chỉ kiểm tra header `Origin` — **chỉ ngăn được trình duyệt**; script tự viết giả header vẫn gọi được. Bảo vệ duy nhất còn lại: 20 lượt/phút/IP; `GET /config` **không giới hạn** | [widget/routes.py:58](../app/widget/routes.py#L58) | Tốn tiền DeepSeek + CPU embed do bị lạm dụng | Hạn mức theo `bot_id`/ngày; token ngắn hạn cấp từ `/config`; cảnh báo chi phí |
| **R6** | TB | Chưa xong | **Không có `ProxyFix`** → sau reverse proxy mọi request cùng một IP | không tìm thấy `ProxyFix` trong code | Rate limit trở thành **chung cho toàn hệ thống** (đăng nhập 10 lượt/phút cho tất cả; widget 20/phút cho tất cả khách) — một người có thể khóa mọi người | Cấu hình `ProxyFix` với đúng số proxy tin cậy |
| **R7** | TB | Lỗ hổng | Đăng nhập OAuth **gộp tài khoản theo email**; Google mặc định coi `email_verified = True` nếu thiếu trường; Facebook **không kiểm tra** xác minh email | [auth/routes.py:163](../app/auth/routes.py#L163), [auth/service.py:73](../app/auth/service.py#L73) | Kẻ tạo tài khoản mạng xã hội với email nạn nhân có thể **chiếm tài khoản** đã có | Mặc định `email_verified=False`; Facebook chỉ gộp khi đã xác minh hoặc yêu cầu xác nhận qua Magic Link |
| **R8** | TB | Chưa xong | Chưa cấu hình cookie phiên `SECURE`/`SAMESITE`; chưa có HTTPS | không tìm thấy `SESSION_COOKIE_*` | Cookie có thể bị gửi qua HTTP thường; CSRF chỉ dựa vào token thủ công | Đặt `SESSION_COOKIE_SECURE=True`, `SAMESITE=Lax`; bắt buộc HTTPS |
| **R9** | TB | Tạm | Socket.IO đặt `cors_allowed_origins="*"` (được giảm nhẹ bằng kiểm tra Origin ở `on_connect`) | [app/__init__.py:18](../app/__init__.py#L18), [events.py:34](../app/dashboard/events.py#L34) | Bề mặt tấn công rộng hơn cần thiết | Thu hẹp về domain của ứng dụng |
| **R10** | TB | **Đã giảm nhẹ, chưa đóng** | **Prompt injection.** Báo cáo trước: hướng dẫn + tài liệu + câu hỏi gộp **một chuỗi** gửi như 1 tin nhắn người dùng (`rag_engine.build_prompt` cũ). **Nay** (Context & Response Decision Engine) message đã **tách vai trò thật**: `system` (chỉ dẫn bot + quy tắc + hợp đồng JSON) → tin gần đây xen kẽ `user`/`assistant` → `user` cuối (tài liệu + câu hỏi); mọi văn bản "chỉ là DỮ LIỆU, không phải mệnh lệnh" được nhắc lại ở nhiều điểm (`prompts.py TEXTS["rules"]`). **Vẫn chưa đóng:** tài liệu và câu hỏi khách vẫn nằm trong CÙNG tin `user` cuối (không tách hẳn thành message riêng), và không có bộ lọc chặn câu lệnh giả trong nội dung tài liệu/câu hỏi | [builder.py `render_system_static`, `MessageBuilder`](../core/context_engine/builder.py), [prompts.py](../core/context_engine/prompts.py) | Khách/tài liệu vẫn có thể thử "bỏ qua chỉ dẫn trước đó"; rủi ro thấp hơn trước (không còn lẫn vào đúng 1 chuỗi duy nhất với chỉ dẫn hệ thống) nhưng chưa loại bỏ | Tách tài liệu tham khảo thành 1 `user` message riêng (đánh dấu rõ là dữ liệu); cân nhắc bộ lọc/bẫy phát hiện câu lệnh giả trong tài liệu trước khi đưa vào prompt |
| **R11** | THẤP | Chưa xong | Mật khẩu chỉ cần ≥ 8 ký tự; không khóa tài khoản sau nhiều lần sai (chỉ theo IP); "Quên mật khẩu" là liên kết `#` | [auth/service.py](../app/auth/service.py), `login.html` | Dễ bị dò mật khẩu phân tán; người dùng quên mật khẩu không tự khôi phục được | Chính sách mật khẩu, khóa tạm theo tài khoản, luồng đặt lại mật khẩu (có thể tận dụng Magic Link) |
| **R12** | THẤP | Lỗ hổng | Đăng ký kiểm tra email trùng **trước** khi ghi, không xử lý tranh chấp đồng thời | `validate_registration` + `register` | Hai request cùng email gần như đồng thời → lỗi 500 | Bắt `IntegrityError` |

### R-nhom-b — B. Toàn vẹn dữ liệu và chất lượng RAG <a id="r-nhom-b"></a>

| ID | Mức | Loại | Vấn đề | Bằng chứng | Hậu quả | Đề xuất |
| --- | --- | --- | --- | --- | --- | --- |
| **R13** | **CAO** | Lỗ hổng | **Xóa tài liệu khi worker đang xử lý:** route xóa không kiểm tra trạng thái; worker vẫn ghi tiếp các lô còn lại vào ChromaDB | [routes.py:288](../app/dashboard/routes.py#L288) (không kiểm `status`); *suy ra từ đọc code, **chưa tái hiện*** | **Vector mồ côi** của tài liệu đã xóa vẫn được tìm thấy → bot trả lời bằng nội dung người dùng tưởng đã xóa | Chặn xóa khi `pending`/`processing` (hoặc yêu cầu hủy job trước); worker kiểm tra tài liệu còn tồn tại trước mỗi lô |
| **R14** | TB | Tạm | **Huấn luyện lại xóa chunk cũ trước, rồi mới ghi chunk mới** | [rag_engine.py:341](../core/rag_engine.py#L341) | Trong lúc chạy (có thể hàng chục phút) tài liệu **biến mất một phần/toàn bộ** khỏi kết quả tìm kiếm; nếu lỗi giữa chừng, chunk cũ **đã mất** | Ghi phiên bản mới kèm nhãn version, xong mới xóa bản cũ (hoán đổi) |
| **R15** | ~~CAO~~ **Đã xử lý, kiến trúc đã thay** | ~~Chưa xong~~ | Báo cáo trước: ngưỡng `min_similarity` (cosine, mặc định 0,25, đo trên 2 kho dữ liệu/~40 câu hỏi: không liên quan ≤ 0,19; có dấu đúng chủ đề ≥ 0,42) + quy tắc "không bịa". **Nay thay bằng `rag_distance_threshold`** (bình phương khoảng cách L2 của Chroma, KHÔNG cùng thang với cosine cũ — `cos = 1 − d/2`; mặc định 1,50 ≡ cosine 0,25, tức **giữ nguyên ngưỡng hiệu lực đã đo**), đo lại trên dữ liệu thật: câu hỏi đúng chủ đề `d ≈ 1,19–1,48`, lạc đề `d ≥ 1,58`; cộng thêm cây quyết định tường minh (không chunk nào đạt ngưỡng → **không** dùng câu trả lời của LLM, luôn CLARIFY/DECLINE bằng câu chủ bot cấu hình — mạnh hơn "chỉ nhắc quy tắc trong prompt" của bản trước). **Còn lại (chưa đổi):** câu hỏi **viết không dấu** đúng chủ đề vẫn có thể bị bỏ sót (chưa đo lại con số cụ thể theo thang mới); kho rất lớn có thể nâng mức nhiễu nền | [settings.py `DEFAULTS`](../core/context_engine/settings.py) (ghi chú đo đạc), [decision.py `decide`](../core/context_engine/decision.py), [setup.html](../app/templates/bots/setup.html) | Trước: bot dễ trả lời bừa. Nay: chỉ dùng đoạn đủ giống; không có thì AI **không tự trả lời**, dùng câu đã cấu hình | Theo dõi `decision_trace.rag.distance_threshold`/`candidate_count` để chỉnh ngưỡng; cân nhắc chuẩn hóa/bổ sung dấu cho câu hỏi ([R19](#r-nhom-b)) |
| **R16** | ~~TB~~ **Đã xử lý** | ~~Chưa xong~~ | Ô chọn `ai_model` ở Bước 1 từng **không có tác dụng** (luôn dùng model trong `.env`). Nay **bỏ ô chọn**, model cố định `deepseek-flash` trong code. **Kèm theo phát hiện lỗi thật:** `deepseek-flash` mặc định bật thinking nên `max_tokens` nhỏ cho câu trả lời **rỗng** — đã tắt thinking (đo trên API thật) | [llm_client.py](../core/llm_client.py) | Trước: chat có thể trả lời rỗng hoặc cụt khi `max_tokens` thấp | Cột `bot_settings.ai_model` còn trong DB nhưng không dùng (xem [R40](#r-nhom-d)); dòng `DEEPSEEK_MODEL` trong `.env` giờ bị bỏ qua, có thể xóa |
| **R17** | TB | **Một phần đã xử lý** | Báo cáo trước: cả 3 công tắc/ô `forward_to_staff`, `collect_customer_info`, `away_message` đều **không có mã nào dùng**. **Nay `collect_customer_info` đã được nối**: bật thì mỗi tin khách được quét số điện thoại/email (regex) và tạo/gắn `Customer` cùng team ([mục 4.3.2](#432-các-bước-chi-tiết) bước (2)). `forward_to_staff` và `away_message` **vẫn** không có mã nào dùng. **Phát hiện phụ (ngoài phạm vi Context Engine, chưa kiểm tra kỹ trong lần cập nhật này):** widget đã có cơ chế tạm dừng bot khi nhân viên đang tiếp quản hội thoại (`_staff_has_taken_over`, dựa vào `Message.sender="staff"` + `Conversation.status`) — có vẻ module Inbox đã được xây thêm phần nào so với trạng thái "⬜ trang giữ chỗ" ghi ở [mục 1.3](#13-mức-độ-hoàn-thiện)/[5.10](#510-các-chức-năng-chưa-hoàn-thiện); cần audit riêng `app/inbox/`, `app/customers/` để cập nhật đúng | [widget/service.py](../app/widget/service.py) `receive_message`, `_staff_has_taken_over`; [customers/service.py `capture_contact`](../app/customers/service.py) | Giao diện bớt hứa hẹn tính năng không có, nhưng vẫn còn 2/3 | Hoàn thiện `forward_to_staff`/`away_message` cùng Inbox thật, hoặc ẩn khỏi giao diện đến khi có; audit riêng mức hoàn thiện của Inbox |
| **R18** | TB | Tạm | Đọc tệp bằng `decode("utf-8-sig", errors="replace")` | [service.py `_extract_text`](../app/dashboard/service.py) | Tệp không phải UTF-8 (Windows-1258, TCVN3…) bị thay bằng `�` **im lặng** rồi vẫn huấn luyện → tri thức hỏng. Nhánh báo lỗi `UnicodeError` **không bao giờ chạy** | Giải mã nghiêm ngặt và báo lỗi rõ, hoặc phát hiện mã hóa và **cảnh báo ngay ở bước xem trước** |
| **R19** | TB | Chưa xong | "Chuẩn hóa sang .md" mới chỉ là giải mã (+ CSV → bảng); **TXT không tiêu đề không được tự nhận diện mục**; chưa có chỗ sửa nội dung; tiêu đề `####` trở xuống bị bỏ qua | [rag_engine.py:122](../core/rag_engine.py#L122) | Tài liệu dạng văn bản thường cho chunk kém chất lượng (không có đường dẫn tiêu đề) | Tự nhận diện tiêu đề (dòng ngắn, viết hoa, đánh số "1.", "1.1"); cho sửa markdown ở bước xem trước; hỗ trợ PDF/DOCX (cần duyệt thêm thư viện) |
| **R20** ★ | TB | Tạm | Tài liệu `draft` **không hết hạn** và vẫn **tính vào quota** | [service.py `storage_summary`](../app/dashboard/service.py) | Tệp upload rồi bỏ dở chiếm chỗ cho đến khi xóa tay; có thể làm đầy quota 50 MB của bot | Dọn `draft` quá N ngày, hoặc hiển thị rõ "tệp chờ cấu hình" |
| **R21** ★ | TB | Tạm | **Xem trước không có cache/giới hạn:** mỗi lần gọi đọc lại MinIO + tokenize toàn bộ tệp (≤ 5 MB) + trả về toàn bộ chunk | [service.py:343](../app/dashboard/service.py#L343), [routes.py](../app/dashboard/routes.py) (không `@limiter`) | Tệp lớn + gõ liên tục/nhiều tab → nghẽn CPU dùng chung với chat; trang có hàng nghìn khối màu nặng | Cache theo (tài liệu, tham số); `@limiter.limit`; phân trang/giới hạn số chunk hiển thị |
| **R22** | TB | Tạm | **Lệch phiên bản** client `chromadb` 1.5.9 ↔ server 1.0.0 và **không ghim** | **[ĐO]** `chroma_client.get_version()`; [requirements.txt](../requirements.txt) | Nâng cấp thư viện/ image bất ngờ có thể làm hỏng giao tiếp/định dạng dữ liệu | Ghim phiên bản client và image server đồng bộ; kiểm thử khi nâng cấp |
| **R23** | THẤP | Tạm | Chỉ lưu metadata `document_id`, `chunk_index` (không lưu tiêu đề/nguồn) | [rag_engine.py:355](../core/rag_engine.py#L355) | Không thể trích dẫn nguồn (tiêu đề mục/tên tệp) cho khách | Thêm metadata tiêu đề, tên tệp để hiển thị nguồn |

### R-nhom-c — C. Vận hành và hiệu năng <a id="r-nhom-c"></a>

| ID | Mức | Loại | Vấn đề | Bằng chứng | Hậu quả | Đề xuất |
| --- | --- | --- | --- | --- | --- | --- |
| **R25** | **CAO** | Tạm | **CPU/RAM tranh chấp:** huấn luyện và chat dùng chung CPU (ONNX chiếm mọi nhân); tách worker thì **nhân đôi RAM model**; nhiều process gunicorn **nhân RAM lên nữa** | **[ĐO]** model 2,13 GiB; [rag_engine.py:64](../core/rag_engine.py#L64) | Đang huấn luyện tài liệu lớn thì chat chậm/timeout; chi phí hạ tầng tăng nhanh | Giới hạn số luồng ONNX cho worker; hàng đợi ưu tiên chat; cân nhắc model nhẹ/int8; tách máy cho worker |
| **R26** | TB | Tạm | **2 worker nhúng trong tiến trình web** (dùng `eventlet.tpool`/`run_blocking` để khỏi treo server): `process_documents` (huấn luyện tài liệu) **và** `context_jobs` **(mới)** (tóm tắt hội thoại + embed lịch sử chat của Decision Engine) | [run.py](../run.py), [workers/process_documents.py:131](../workers/process_documents.py#L131), [workers/context_jobs.py:102](../workers/context_jobs.py#L102), [rag_engine.py:44](../core/rag_engine.py#L44) | Tắt/khởi động lại web = **ngắt cả 2 việc nền**; tóm tắt hội thoại đang chờ (`summary_pending`) và tin chưa embed chỉ tiếp tục khi web chạy lại; lỗi worker ảnh hưởng web | Chạy cả 2 worker riêng ở production (`EMBEDDED_WORKER=false`) |
| **R27** | TB | Tạm | Worker **hỏi DB mỗi 3 giây** và phải `rollback()` mỗi vòng để né ảnh chụp REPEATABLE READ của MySQL | [process_documents.py:27](../workers/process_documents.py#L27), [process_documents.py:105](../workers/process_documents.py#L105) | Tải thừa nhỏ nhưng là **giải pháp vòng**; độ trễ nhận việc tới 3 giây | Dùng hàng đợi thực sự (Redis Streams/RQ/Celery) |
| **R28** | TB | Lỗ hổng | **Khóa Redis TTL 60 giây chỉ được gia hạn sau mỗi lô 16 chunk**; và worker mới giành được khóa sẽ **đặt lại mọi tài liệu `processing` về `pending`** | [process_documents.py:29](../workers/process_documents.py#L29), [process_documents.py:111](../workers/process_documents.py#L111) | Nếu một lô mất > 60 giây (CPU yếu) rồi có worker thứ hai → **xử lý trùng/ghi đè** giữa chừng | Gia hạn khóa bằng luồng nền định kỳ; chỉ khôi phục tài liệu `processing` cũ hơn ngưỡng thời gian |
| **R29** | TB | Tạm | **Nuốt ngoại lệ:** xóa tệp MinIO lỗi thì `except Exception: pass`; bước dọn khi upsert lỗi cũng nuốt | [service.py:430](../app/dashboard/service.py#L430), [rag_engine.py:360](../core/rag_engine.py#L360) | **Tệp/vector mồ côi** tích lũy, không có dấu vết để điều tra; trái với quy định chống che giấu lỗi của dự án | Bắt đúng loại lỗi + `logger.warning` kèm thông tin định danh |
| **R30** | TB | Chưa xong | Không có cấu hình logging; worker dùng `print`; `/healthz` chỉ trả `ok` (không kiểm tra DB/Redis/Chroma/MinIO) | [app/__init__.py](../app/__init__.py), [process_documents.py:88](../workers/process_documents.py#L88) | Khó theo dõi, khó phát hiện dịch vụ phụ thuộc chết | Logging có cấu trúc; health check sâu (`/readyz`) |
| **R31** | TB | **Một phần đã xử lý** | Báo cáo trước: **không có bộ test tự động trong repo**. **Nay `tests/` đã có 321 kiểm tra** (thư viện chuẩn `unittest`, không thêm pytest — đúng quy định ưu tiên thư viện hiện có) nhưng **chỉ phủ Context & Response Decision Engine** ([mục 4.4b](#44b-pha-b--context--response-decision-engine-mới-nằm-trong-repo)); **Pha A (upload/chunk/huấn luyện tài liệu) và toàn bộ giao diện/luồng còn lại (auth, Bước 2/3/4, Inbox, Khách hàng...) vẫn KHÔNG có test trong repo** | `tests/*.py` (mới); không có `test_*.py` nào cho `app/dashboard/service.py` (Pha A), `app/auth/`, `app/widget/` (ngoài phần đã gọi qua Decision Engine) | Sửa mã ngoài phạm vi Context Engine vẫn dễ gây hồi quy mà không biết | Viết test cho Pha A + auth + widget bằng `unittest` (cùng cách `tests/db_case.py` đang làm) |
| **R32** | TB | Tạm | `requirements.txt` **không ghim** nhiều thư viện chủ chốt; **không khớp** môi trường thực (thừa `langchain`, `langchain-experimental`, `gunicorn`; thiếu những gì đang cài như `torch`) | [requirements.txt](../requirements.txt); **[ĐO]** `env/` | Cài mới có thể ra phiên bản khác → lỗi khó tái hiện; môi trường phình 1,35 GB | Tạo `requirements.lock` từ môi trường đang chạy tốt; rà và bỏ gói thừa (**sau khi bạn xác nhận** — tôi chưa xóa gì) |
| **R33** | THẤP | Tạm | `get_llm` dùng `lru_cache` theo (temperature, max_tokens) | [llm_client.py:11](../core/llm_client.py#L11) | Đổi `DEEPSEEK_API_KEY` cần khởi động lại | Chấp nhận được; ghi chú vận hành |
| **R34** ★ | THẤP | Tạm | Truy cập cấu hình chunk khi tài liệu đang `pending/processing` trả **409 trang lỗi thô** | [routes.py:297](../app/dashboard/routes.py#L297) | Trải nghiệm thô | Chuyển hướng về danh sách kèm thông báo |
| **R42** | ~~CAO~~ **Đã xử lý** | ~~Lỗ hổng~~ | **Mọi lệnh gọi DeepSeek trong server lỗi `RecursionError`:** `openai 3.15` dùng `httpx2`, mặc định dựng SSL bằng `truststore.SSLContext`; `truststore` bắt lớp `ssl.SSLContext` lúc import, và khi tiến trình đã `eventlet.monkey_patch()` (bắt buộc với `run.py`) thì setter `verify_mode` của Python gọi lại chính nó vô hạn. Tái hiện tối thiểu: chạy thường OK, vá eventlet thì lỗi | [llm_client.py](../core/llm_client.py) `_http_client`; log server (`ssl.py` `verify_mode` lặp 887 lần) | Chat, chat thử, Widget và nút Tối ưu **không hoạt động** khi chạy bằng `run.py`; lỗi chỉ hiện ở log server còn người dùng chỉ thấy 502 | **Đã sửa:** chỉ định `ssl.create_default_context(cafile=certifi.where())` cho client HTTP của LLM (đi qua `SSLContext` của eventlet nên vẫn không chặn server). **Lưu ý:** mã nay import trực tiếp `certifi` và `openai.DefaultHttpxClient` — cả hai đang có sẵn nhờ phụ thuộc gián tiếp nhưng **chưa được khai báo/ghim** trong `requirements.txt` ([R32](#r-nhom-c)); khi nâng cấp `openai`/`eventlet`/Python cần chạy lại kiểm thử chat trong server. Hệ quả rộng hơn: **kiểm thử nào chỉ chạy ngoài `run.py` sẽ không thấy lỗi loại này** |

### R-nhom-d — D. Chưa hoàn thiện <a id="r-nhom-d"></a>

| ID | Mức | Loại | Vấn đề | Bằng chứng | Hậu quả | Đề xuất |
| --- | --- | --- | --- | --- | --- | --- |
| **R35** | **CAO** | Chưa xong | **`send_followups.py` đặt `status="sent"` mà chưa gửi gì** (còn `TODO`) | [send_followups.py:18](../workers/send_followups.py#L18) | Nếu bị đưa vào cron: FollowUp đến hạn bị **đánh dấu đã gửi nhưng khách không nhận được gì**, mất dấu | Không chạy theo lịch cho đến khi triển khai gửi thật; hoặc đổi thành no-op có log |
| **R36** | TB | Chưa xong | **34 route `/api/*`** là khung `raise NotImplementedError` | các `service.py` của `bots/knowledge/inbox/followup/customers/reports/api_tokens/profile` | Gọi (đã đăng nhập) → **HTTP 500**; bề mặt vô ích, dễ nhầm là tính năng đã có | Chỉ đăng ký blueprint khi đã triển khai, hoặc trả `501` rõ ràng |
| **R37** | TB | Chưa xong | 6 mục menu là trang giữ chỗ; 3 kênh mạng xã hội chỉ là thẻ | `shell.html`, `publish.html` | Kỳ vọng sản phẩm cao hơn thực tế | Ghi rõ trạng thái trong tài liệu sản phẩm |
| **R38** | TB | Chưa xong | **Không kiểm tra vai trò** Owner/Admin/Member; session chỉ lấy **team đầu tiên** | [auth/service.py:145](../app/auth/service.py#L145) | Mọi thành viên làm được mọi việc; user thuộc nhiều team không chuyển team được | Thêm kiểm tra quyền theo vai trò; chọn/chuyển team |
| **R39** | THẤP | Chưa xong | Không xóa được trợ lý; không giới hạn số trợ lý theo gói; gói cước chỉ hiển thị | `bots/service.py` (khung) | Chưa có mô hình kinh doanh | Xóa bot (kèm dọn collection Chroma + tệp MinIO) và hạn mức theo gói |
| **R40** ★ | THẤP | Tạm | Sau khi gỡ trang sửa chunk, còn **mã không ai gọi**: `rag_engine.get_document_chunks/update_chunk` và cột mặc định `bot_settings.chunk_size/overlap` (không còn giao diện chỉnh) | [rag_engine.py:374](../core/rag_engine.py#L374) | Mã chết gây rối; cột mặc định vẫn dùng làm giá trị dự phòng cho tài liệu cũ | Giữ đến khi quyết định có làm lại "sửa chunk" hay không; nếu bỏ hẳn thì xóa cùng lúc |

### R-nhom-e — E. Đóng gói <a id="r-nhom-e"></a>

| ID | Mức | Loại | Vấn đề | Bằng chứng | Hậu quả | Đề xuất |
| --- | --- | --- | --- | --- | --- | --- |
| **R41** | **CAO** | Lỗ hổng | **`models/` (2,13 GiB) không nằm trong `.gitignore`**; repo chưa `git init` | **[ĐO]** kích thước; [.gitignore](../.gitignore) | Không thể đẩy lên GitHub (vượt giới hạn tệp); nếu ép đẩy sẽ hỏng repo; và có nguy cơ vô tình commit `.env` | Xem [mục 7.4](#74-đưa-lên-github--các-việc-phải-làm-trước) |

### 8.1 Những điểm đã làm **tốt** (để cân bằng)

- Mỗi bot **một collection Chroma riêng** → cô lập dữ liệu ngay từ thiết kế.
- **CSRF** cho mọi form quản trị; **rate limit** cho đăng nhập, OAuth, chat.
- **Escape HTML mặc định** (Jinja2) và `textContent` ở widget/xem trước; chỉ một chỗ `|safe` với dữ liệu server cấp.
- **Danh sách trắng** cho mọi tùy chọn giao diện widget; màu chỉ nhận `#RRGGBB`.
- Magic Link **ký + có hạn + dùng một lần**; thông báo đăng nhập sai **không lộ** email nào tồn tại.
- Upload **giới hạn kích thước và quota** ở cả trình duyệt lẫn server; đọc tệp có trần để không nạp khổng lồ vào RAM.
- Worker: **nhận việc có điều kiện** (không nhận trùng), **khóa phân tán**, **khôi phục** tài liệu kẹt, **dọn phần ghi dở** khi lỗi.
- Đếm token bằng **tokenizer thật** → không vượt giới hạn model.
- **Mới — Context & Response Decision Engine:** cây quyết định (`decide`) và toàn bộ phần tính state/token đều là **hàm thuần,
  không đụng DB/LLM** → test được từng nhánh độc lập bằng dữ liệu giả (321 kiểm tra trong repo, [mục 4.4b](#44b-pha-b--context--response-decision-engine-mới-nằm-trong-repo)).
  Trường không thuộc tier đang chọn **luôn dùng mặc định**, kể cả khi giá trị cũ còn trong DB → hạ tier không để lại cấu hình ẩn.
  Không chunk nào đạt ngưỡng liên quan → **không bao giờ** dùng `proposed_answer` của LLM (khác hẳn kiểu chỉ "nhắc trong prompt").
  Lệnh gọi LLM phụ (Historical Retrieval) lỗi **không** làm hỏng câu trả lời chính đã hợp lệ. Chi phí ước tính hiển thị ngay khi
  đổi cấu hình (Bước 1) giúp chủ bot thấy tác động tiền bạc trước khi lưu.

---

## 9. Lộ trình xử lý đề xuất

> Sắp theo mức ưu tiên. Mỗi mục liên kết tới ID rủi ro. **Những việc đụng thư viện mới/đổi hạ tầng/đổi DB đều cần bạn xác nhận trước.**

**P0 — Bắt buộc trước khi mở ra Internet hoặc đưa lên GitHub**

1. Đổi `SECRET_KEY` ngẫu nhiên và bắt buộc ở production — [R1](#r-nhom-a)
2. Tắt `debug`, đọc từ môi trường — [R2](#r-nhom-a)
3. Vá open redirect `next` — [R3](#r-nhom-a)
4. Khóa Redis/ChromaDB/MinIO/DB (bind localhost, mật khẩu, firewall) — [R4](#r-nhom-a)
5. Thêm `models/` vào `.gitignore`, bổ sung hướng dẫn model + bucket vào README — [R41](#r-nhom-e)

**P1 — Trước khi có khách hàng thật**

6. ~~Ngưỡng độ giống/khoảng cách + phản hồi "không có thông tin"~~ — **đã xong, nay là Context & Response Decision Engine**, xem [R15](#r-nhom-b)
7. Chặn xóa tài liệu đang xử lý; worker kiểm tra tồn tại — [R13](#r-nhom-b)
8. Hạn mức chi phí/lạm dụng cho widget — [R5](#r-nhom-a) (chi phí ước tính ở Bước 1 giúp chủ bot thấy trước tác động, nhưng **chưa** có hạn mức/chặn thật)
9. `ProxyFix` + HTTPS + cookie an toàn — [R6, R8](#r-nhom-a)
10. Xử lý gộp tài khoản OAuth — [R7](#r-nhom-a)
11. ~~Số phận `ai_model`~~ (đã xong, [R16](#r-nhom-b)) · ~~thu thập thông tin khách~~ (đã nối, [R17](#r-nhom-b)) · quyết định
    `forward_to_staff`/`away_message` — [R17](#r-nhom-b)
12. Vô hiệu `send_followups.py` cho đến khi gửi thật — [R35](#r-nhom-d)

**P2 — Chất lượng và vận hành**

13. Hoán đổi phiên bản khi huấn luyện lại — [R14](#r-nhom-b)
14. Giải mã tệp nghiêm ngặt + tự nhận diện tiêu đề TXT + sửa markdown — [R18, R19](#r-nhom-b)
15. Cache/limiter cho xem trước; dọn `draft` — [R20, R21](#r-nhom-b)
16. Tách worker, giới hạn luồng ONNX, sửa gia hạn khóa — [R25, R26, R28](#r-nhom-c) (nay gồm cả worker `context_jobs`)
17. Ghim phiên bản + đồng bộ Chroma client/server — [R22, R32](#r-nhom-b)
18. Logging + health check sâu; **đưa kịch bản kiểm thử Pha A + auth + widget vào repo** (Decision Engine đã có, [mục 4.4b](#44b-pha-b--context--response-decision-engine-mới-nằm-trong-repo)) — [R30, R31](#r-nhom-c)
19. Log thay vì nuốt lỗi — [R29](#r-nhom-c)
20. Tách hẳn tài liệu tham khảo khỏi tin `user` cuối trong prompt; audit mức hoàn thiện thật của Inbox — [R10](#r-nhom-a), [R17](#r-nhom-b)

**P3 — Mở rộng tính năng**

21. Inbox, FollowUp thật, Khách hàng, Báo cáo, API Tokens, Hồ sơ, phân quyền, xóa trợ lý — [R36–R39](#r-nhom-d)
22. Hỗ trợ PDF/DOCX (cần duyệt thư viện) — [R19](#r-nhom-b)
23. Kênh Facebook/Zalo/WhatsApp (ngoài phạm vi hiện tại)

---

## 10. Phụ lục

### Phụ lục A — Bảng route đầy đủ

*(Đối chiếu lại 2026-09-22 cho các route liên quan tới Context Engine/Bước 1 bằng cách đọc trực tiếp `@bp.route` trong
`app/dashboard/routes.py`/`app/widget/routes.py`. Vài route quản trị khác — `/inbox`, `/customers`, `/customers/export.csv`,
`/bots/<id>/publish/icon`, `/widget/api/<id>/icon`, `/widget/api/<id>/staff-messages` — **có tồn tại trong code** nhưng
**chưa được đối chiếu/mô tả trong lần cập nhật này**, xem ghi chú đầu tài liệu.)*

**Giao diện quản trị và luồng chính**

| Route | Phương thức | Chức năng |
| --- | --- | --- |
| `/` | GET | Chuyển tới Bảng điều khiển hoặc Đăng nhập |
| `/healthz` | GET | Kiểm tra sống (`{"status":"ok"}`) |
| `/auth/login` | GET, POST | Đăng nhập (mật khẩu / Magic Link) |
| `/auth/register` | GET, POST | Đăng ký |
| `/auth/logout` | POST | Đăng xuất |
| `/auth/google/login`, `/auth/google/callback` | GET | Đăng nhập Google |
| `/auth/facebook/login`, `/auth/facebook/callback` | GET | Đăng nhập Facebook |
| `/auth/magic/callback` | GET | Xác nhận Magic Link |
| `/auth/me` | GET | Thông tin người dùng hiện tại (JSON) |
| `/dashboard` | GET | Bảng điều khiển |
| `/bots/new` | GET, POST | Tạo trợ lý |
| `/placeholder` | GET | Trang "đang xây dựng" |
| `/bots/<id>/setup` | GET, POST | **Bước 1** — Thiết lập (form cấu hình mô hình + Decision Engine theo tier) |
| `/bots/<id>/setup/optimize-instructions` | POST | Nút Tối ưu chỉ dẫn (JSON, CSRF header, 10 lượt/phút) |
| `/bots/<id>/preview-chat` | POST | Chat thử — chạy **đúng** `core/context_engine.run_turn` nhưng không lưu hội thoại (JSON, CSRF header, 30 lượt/phút) |
| `/bots/<id>/setup/cost-estimate` **(mới)** | POST | Chi phí ước tính mỗi câu hỏi theo cấu hình đang chọn trên form, chưa cần lưu (JSON, CSRF header, 60 lượt/phút) |
| `/bots/<id>/knowledge` | GET | **Bước 2** — Danh sách tài liệu |
| `/bots/<id>/knowledge/upload` | POST | Upload tệp |
| `/bots/<id>/knowledge/status` | GET | Trạng thái tài liệu (JSON, cho polling) |
| `/bots/<id>/knowledge/<doc>/delete` | POST | Xóa tài liệu |
| `/bots/<id>/knowledge/<doc>/chunks` | GET | Trang cấu hình chunk |
| `/bots/<id>/knowledge/<doc>/chunks/preview` | POST | Xem trước chunk (JSON) |
| `/bots/<id>/knowledge/<doc>/train` | POST | Lưu cấu hình và xếp hàng huấn luyện |
| `/bots/<id>/publish` | GET, POST | **Bước 3** — Xuất bản, lưu domain |
| `/bots/<id>/publish/appearance` | POST | Lưu giao diện widget |
| `/bots/<id>/history` | GET | **Bước 4** — Lịch sử chat |

**API công khai (Web Widget)**

| Route | Phương thức | Chức năng |
| --- | --- | --- |
| `/widget/embed.js` | GET | Script nhúng |
| `/widget/api/<id>/config` | GET | Cấu hình hiển thị widget |
| `/widget/api/<id>/messages` | POST | Nhận tin nhắn, chạy Context & Response Decision Engine, trả lời (hoặc `reply: null` nếu nhân viên đang tiếp quản) |

**Khung API chưa triển khai (trả HTTP 500)** — `/api/bots` (+`/<id>`, `/<id>/settings`, `/dashboard`), `/api/knowledge/...` (documents, chunks), `/api/inbox/conversations...`, `/api/followups...`, `/api/customers...`, `/api/reports/overview|export`, `/api/api-tokens...`, `/api/profile`, `/api/profile/team` — tổng 34 route ở lần đo 2026-09-19; **chưa đếm lại** lần này.

### Phụ lục B — Bảng tham số cố định trong mã

| Tham số | Giá trị | Vị trí |
| --- | --- | --- |
| Chunk size mặc định / min / max | 450 / 100 / 1000 token | [rag_engine.py:116](../core/rag_engine.py#L116) |
| Overlap mặc định / tối đa | 60 token / 30% chunk size | [rag_engine.py:117](../core/rag_engine.py#L117) |
| Giới hạn token embedding | 2048 | [rag_engine.py:26](../core/rag_engine.py#L26) |
| Lô embed / lô ghi Chroma | 8 chunk / 16 chunk | [rag_engine.py:80](../core/rag_engine.py#L80), [rag_engine.py:333](../core/rag_engine.py#L333) |
| Top-k / cửa sổ lân cận (Pha A, tra cứu tri thức) | mặc định 8 / 1 (`rag_top_k` chỉnh theo tier) | [rag_engine.py `NEIGHBOR_WINDOW`](../core/rag_engine.py), [settings.py `DEFAULTS`](../core/context_engine/settings.py) |
| `bot_settings.min_similarity` (cosine, **cũ, engine không còn đọc**) | 0,25 / 0,10–0,60 | Cột còn trong DB; xem hàng dưới |
| **Ngưỡng khoảng cách tra cứu** `rag_distance_threshold` (mới, thay `min_similarity`) | mặc định **1,50** (≡ cosine 0,25) / khoảng cho phép **0,20–1,90**, chỉnh theo tier Nâng cao trở lên | [settings.py `RANGES`](../core/context_engine/settings.py) |
| Chiều vector | 1024 | Cấu hình model |
| Tệp tối đa / quota mỗi bot / trần 1 request | 5 MB / 50 MB / 51 MB | [config.py](../config.py) |
| Worker huấn luyện tài liệu: chu kỳ quét / TTL khóa | 3 giây / 60 giây | [process_documents.py:27](../workers/process_documents.py#L27) |
| **Worker `context_jobs`** (mới): chu kỳ quét / TTL khóa / lùi lại khi tóm tắt lỗi | 3 giây / 60 giây / **300 giây** | [context_jobs.py](../workers/context_jobs.py) |
| **Lịch sử vào prompt** (thay "6 tin × 600 ký tự" cũ) | Tối đa `recent_message_limit` tin (mặc định **10**, cố định ở tier Cơ bản), dừng sớm khi vượt `recent_token_limit` (mặc định **2000 token thật**, không phải ký tự) | [settings.py `DEFAULTS`](../core/context_engine/settings.py), [builder.py `RecentMessageSelector`](../core/context_engine/builder.py) |
| Ngân sách dự phòng cho JSON có cấu trúc | **600 token**, cộng thêm vào `max_tokens` khi gọi DeepSeek | [settings.py `JSON_OVERHEAD_TOKENS`](../core/context_engine/settings.py) |
| Tổng ngân sách ngữ cảnh / áp lực cảnh báo / nén mạnh | mặc định **8000 token** / **0,80** / **0,90**, chỉnh ở tier Chuyên gia | [settings.py `DEFAULTS`](../core/context_engine/settings.py) |
| Số lượt hỏi làm rõ liên tiếp tối đa / lệnh gọi lại khi JSON lỗi | mặc định **2** / **2 lần** (gọi đầu + 1 lần gọi lại) | [settings.py](../core/context_engine/settings.py), [structured.py `MAX_JSON_ATTEMPTS`](../core/context_engine/structured.py) |
| Đơn vị cache DeepSeek | **128 token/khối**; lệch dưới 1 khối coi như cache miss | [cost_estimate.py `CACHE_BLOCK_TOKENS`](../core/context_engine/cost_estimate.py), [cost.py](../core/context_engine/cost.py) |
| Giá DeepSeek Flash (tham khảo) | Cache hit 78,6/157,2 VND·1M · Cache miss 3.930/7.860 VND·1M · Output 15.720/31.440 VND·1M (ngoài/trong giờ cao điểm), tỷ giá 26.200 VND/USD | [BANG_GIA_API_AI.md](BANG_GIA_API_AI.md), [cost_estimate.py](../core/context_engine/cost_estimate.py) |
| Độ dài tin nhắn tối đa | 1000 ký tự | [service.py `MAX_MESSAGE_CHARS`](../app/dashboard/service.py) |
| Giới hạn tần suất | Đăng nhập/đăng ký 10/phút · OAuth/Magic callback 20/phút · Chat thử 30/phút · Tối ưu chỉ dẫn 10/phút · **Chi phí ước tính 60/phút (mới)** · Widget 20/phút (theo IP) | các `routes.py` |
| Độ dài chỉ dẫn tối đa | 10.000 ký tự | [assistant_templates.py](../app/dashboard/assistant_templates.py) `MAX_INSTRUCTIONS_CHARS` |
| Model LLM / thinking mode | `deepseek-flash` / tắt | [llm_client.py](../core/llm_client.py) |
| Magic Link | Hiệu lực 15 phút, dùng 1 lần | [auth/service.py](../app/auth/service.py) |
| Domain widget | Chuẩn hóa về hostname, chấp nhận subdomain | [widget/service.py](../app/widget/service.py) |

### Phụ lục C — Sơ đồ hai quá trình kèm thành phần tham gia (tóm tắt một trang)

```mermaid
flowchart TB
    subgraph PhaA["PHA A — Nạp tri thức"]
        direction LR
        a1["knowledge.html<br/>chunk_config.html"] --> a2["dashboard/routes.py<br/>upload, preview, train"]
        a2 --> a3["dashboard/service.py<br/>upload_document, preview_chunks,<br/>queue_training, process_document"]
        a3 --> a4["core/storage_service.py<br/>MinIO"]
        a3 --> a5["core/rag_engine.py<br/>chunk_markdown, embed_texts,<br/>upsert_chunks"]
        a6["workers/process_documents.py<br/>claim, handle"] --> a3
        a5 --> a7[("ChromaDB")]
        a6 --> a8["dashboard/events.py<br/>Socket.IO"]
    end
    subgraph PhaB["PHA B — Context & Response Decision Engine"]
        direction LR
        b1["widget/embed.js"] --> b2["widget/routes.py<br/>_authorize, rate limit"]
        b2 --> b3["widget/service.py<br/>receive_message<br/>(tạm dừng nếu nhân viên đang tiếp quản)"]
        b3 --> b4["dashboard/service.py<br/>reply_to_customer"]
        b4 --> b5["core/context_engine/engine.py<br/>run_turn: A builder -> B LLM chính<br/>-> C decision -> [F] history -> D lưu"]
        b5 --> b5a["core/context_engine/builder.py<br/>+ core/rag_engine.retrieve"]
        b5a --> b6[("ChromaDB<br/>bot_ID + history_ID")]
        b5 --> b7["core/llm_client.py<br/>ChatDeepSeek (JSON mode)"]
        b5 --> b8["core/context_engine/state.py<br/>ConversationState, StructuredMemory"]
        b9["workers/context_jobs.py"] -.-> b6
        b9 -.-> b8
    end
```

### Phụ lục D — Cách tự đo lại tài nguyên

**RAM của tiến trình ứng dụng** (chạy khi `python run.py` đang chạy và đã hết ~20 giây khởi động):

```powershell
Get-Process python | Select-Object Id, @{n='WS_MB';e={[int]($_.WorkingSet64/1MB)}}, @{n='Private_MB';e={[int]($_.PrivateMemorySize64/1MB)}}
```

**RAM các container:**

```powershell
docker stats --no-stream
```

**Thời gian embed thật trên máy của bạn** (đo 1 lô 8 chunk — cần ≥ 4 GB RAM trống):

```python
# chạy trong thư mục dự án: env\Scripts\python.exe
import time
from core import rag_engine
rag_engine.warm_up()                      # nạp model (~20 giây)
batch = ["Chính sách bảo hành sản phẩm điện tử 12 tháng. " * 20] * 8
t = time.time(); rag_engine.embed_texts(batch)
print(f"{(time.time()-t)/8:.2f} giây/chunk")
```

Có số này là tính lại được toàn bộ bảng thời gian huấn luyện ở [mục 6.3](#63-cpu-và-thời-gian-xử-lý).

**Dung lượng dữ liệu:** `docker system df -v` (Docker), thư mục dữ liệu ChromaDB (`C:\chromadb\data`), và:

```sql
SELECT table_name, ROUND((data_length+index_length)/1024,1) AS kb
FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY 2 DESC;
```

### Phụ lục E — Lịch sử thay đổi trong phiên làm việc gần nhất

| Thay đổi | Tệp chính |
| --- | --- |
| Gỡ trang sửa chunk sau huấn luyện (`/knowledge/<id>/edit`) và nút "Chỉnh sửa" | `routes.py`, `knowledge.html`, xóa `doc_editor.html` |
| Gỡ khối "Cấu hình cắt chunk" chung + route `bot_knowledge_config` (gọi 2 hàm **không tồn tại**: `update_chunk_config`, `reprocess_documents`) | `routes.py`, `knowledge.html` |
| Upload dừng ở `draft`; thêm trang cấu hình chunk riêng từng tệp với xem trước tô màu; 3 route mới (`chunks`, `chunks/preview`, `train`) | `routes.py`, `service.py`, `chunk_config.html` |
| Migration `a8c6d0e2f4b7`: cột `chunk_size`, `chunk_overlap` (nullable) trên `documents` + giá trị enum `draft` (**đã áp dụng vào DB dev**, chỉ thêm, không đụng dữ liệu cũ) | `models.py`, `migrations/versions/` |
| Worker dùng cấu hình chunk **của từng tài liệu** | `service.py` (`chunk_params_for`, `process_document`) |
| **R15:** ngưỡng độ giống `min_similarity` (theo bot, Bước 1) + quy tắc "không bịa" trong prompt; migration `b9d7e1f3a5c8` (**đã áp dụng vào DB dev**, chỉ thêm cột, 4 bot cũ nhận 0,25) | `rag_engine.py`, `models.py`, `service.py`, `routes.py`, `setup.html` |
| **Bước 1:** bỏ ô chọn model, cố định `deepseek-flash` + tắt thinking (sửa lỗi câu trả lời rỗng khi `max_tokens` thấp); gỡ biến `DEEPSEEK_MODEL` khỏi `config.py` và `.env.example` | `llm_client.py`, `config.py`, `setup.html`, `service.py` |
| **Bước 1:** 11 mẫu trợ lý, trình soạn chỉ dẫn markdown (thanh công cụ, số dòng, bộ đếm 10.000 ký tự), nút Tối ưu (endpoint mới) — không đổi schema DB | `assistant_templates.py` (mới), `setup.html`, `routes.py`, `service.py` |
| Kiểm thử Bước 1: 89 kiểm tra (server, DeepSeek thật, **Edge headless thật** kèm ảnh chụp) đều đạt; xác nhận DeepSeek thật với prompt "không có thông tin" (không bịa, bỏ qua câu lệnh cài trong tài liệu) | (ngoài repo) |
| **Sửa R42:** dựng client HTTP của LLM với `certifi` để gọi DeepSeek chạy được trong server eventlet | `llm_client.py` |
| **Chạy thử toàn hệ thống trên server thật:** 40 kiểm tra đầu-cuối đạt (đăng nhập, Bước 1–4, huấn luyện bằng worker, Socket.IO, chat thật, Web Widget); bộ test R15 32/32 đạt; dữ liệu test đã dọn sạch | (ngoài repo) |
| Kiểm thử 41 kiểm tra + 1 kiểm tra bổ sung, tài liệu kiểm thử đã dọn sạch khỏi DB/ChromaDB/MinIO | (ngoài repo) |
| **(trước 2026-09-22, ghi lại khi đối chiếu báo cáo) Context & Response Decision Engine:** thay hẳn `rag_engine.search/build_prompt/answer` bằng `core/context_engine/` (settings theo 3 tier, builder ngân sách+nén, structured JSON output, decision tree thuần, state/memory/summary, historical retrieval, cost tracking); 4 bảng mới (`conversation_state`, `structured_memory`, `bot_intent_config`, `conversation_message_embeddings`) + 27 cột trên `bot_settings`; migration `1a2b3c4d5e01`–`04` (**đã áp dụng vào DB dev**, chỉ thêm, có backfill `rag_distance_threshold` từ `min_similarity` cũ); worker nền thứ 2 `context_jobs.py` (khóa Redis riêng); bộ `unittest` mới trong `tests/` cho toàn bộ engine (xem [mục 4.4b](#44b-pha-b--context--response-decision-engine-mới-nằm-trong-repo) — phần lớn trong tổng 321 kiểm tra hiện có, phần còn lại thêm ở 2 đợt bên dưới) | `core/context_engine/` (mới), `models.py`, `migrations/versions/1a2b3c4d5e01..04`, `workers/context_jobs.py` (mới), `app/dashboard/service.py`, `app/widget/service.py`, `docs/CONTEXT_ENGINE.md` (mới) |
| **(cùng đợt) `collect_customer_info` được nối logic thật** (quét số điện thoại/email, gắn `Customer`); widget tạm dừng bot khi nhân viên đang tiếp quản hội thoại — [R17](#r-nhom-b) cập nhật một phần | `app/widget/service.py`, `app/customers/service.py` |
| **2026-09-22 (phiên này):** gộp card "Cấu hình mô hình AI" vào card "Trả lời thông minh" ở Bước 1; bỏ giá trị tĩnh trên nhãn (thanh trượt hiện giá trị sống ở ô riêng); thêm icon **?** giải thích tác dụng cho mọi trường (kể cả Mức cấu hình, mô tả cả 3 tier) — không đổi schema DB, không đổi cách lưu | `setup.html`, `service.py` (`ENGINE_FIELDS`/`ENGINE_EXPERT_TOGGLES`/`ENGINE_TIER_INFO` thêm mô tả) |
| **2026-09-22 (phiên này):** ô **"Chi phí ước tính mỗi câu hỏi"** ở Bước 1 — khoảng thấp nhất–cao nhất theo giờ thường/cao điểm, cập nhật khi đổi cấu hình (chưa cần lưu); route mới `POST /bots/<id>/setup/cost-estimate` (CSRF, 60 lượt/phút); dùng đúng phép kiểm tra tier như lúc lưu; 21 kiểm tra mới khớp 4 ví dụ tính tay trong `BANG_GIA_API_AI.md` | `core/context_engine/cost_estimate.py` (mới), `app/dashboard/service.py`, `app/dashboard/routes.py`, `setup.html`, `docs/BANG_GIA_API_AI.md` (mới, do người dùng cung cấp), `tests/test_cost_estimate.py` (mới) |
| **2026-09-22 (phiên này):** đối chiếu lại toàn bộ báo cáo kỹ thuật với Context & Response Decision Engine hiện tại (mục 4.3, 3.5, 3.6, 3.7, 5.4, 8, Phụ lục A/B/C) — phạm vi và giới hạn của lần đối chiếu ghi ở đầu tài liệu | `docs/BAO_CAO_KY_THUAT.md` |

*Hết báo cáo.*
