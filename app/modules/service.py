"""Service layer của Website Action Engine (Phase M): khai báo module theo LOẠI (tải lên .zip trang đã lưu, không còn crawl URL — xem
docs/WEBSITE_ACTION_ENGINE.md), xác nhận domain, duyệt hành động, dựng danh sách tool cho agent.

Không chứa logic giải nén/phân tích nội dung (app/modules/runner.py) — ở đây chỉ gọi core/website_actions/zip_extract.py để VALIDATE file tải lên
ngay lúc nhận (từ chối sớm trước khi vào hàng đợi worker). Mọi truy vấn lọc theo bot (multi-tenant: route đã xác thực bot thuộc team).
"""
from __future__ import annotations

from datetime import datetime

from app.models import (
    Bot, BotModule, BotSettings, ModuleAction, ModuleType, ModuleTypeUrlRole, ModuleUrl,
)
from app.widget import domains as widget_domains
from config import Config
from core import storage_service
from core.website_actions import analysis as analysis_mod
from core.website_actions import risk
from core.website_actions import spec as spec_mod
from core.website_actions import zip_extract
from extensions import db

MAX_NAME_CHARS = 120
STATUS_PENDING, STATUS_ANALYZING, STATUS_AWAITING_DOMAIN, STATUS_READY, STATUS_FAILED = (
    "pending", "analyzing", "awaiting_domain_confirmation", "ready", "failed",
)
DOCUMENT_READER_KEY = "document_reader"  # loại module "Đọc tài liệu": không có URL, không cần phân tích — cài xong là dùng được (app/attachments)

# Dữ liệu seed loại module (cùng nội dung migration 1a2b3c4d5e10 + d... relabel — xem docstring ModuleType). CHỈ 1 loại, nhãn URL TRUNG TÍNH để
# phủ đủ 3 kịch bản thực tế mà không cần phân biệt "sản phẩm" hay "dịch vụ" bằng loại module: (1) web bán sản phẩm có thanh toán trực tuyến,
# (2) web dịch vụ có thanh toán/đặt cọc trực tuyến, (3) web bán hàng/dịch vụ KHÔNG thanh toán trên website (chỉ liên hệ/đặt lịch) — trường hợp (3)
# chỉ cần bỏ trống 2 ô cart/checkout, đã tuỳ chọn sẵn.
#
# Trang bắt buộc là nơi đặt nút hành động chính (thêm giỏ/mua, hoặc điền form đặt lịch/liên hệ), nên đó là URL tối thiểu để module có ích; các URL
# còn lại tuỳ chọn. 4 role (product_listing/product_detail/cart/checkout) là enum CỐ ĐỊNH của cột module_urls.url_role (app/models.py:MODULE_URL_ROLES)
# — risk.floor_risk() khoá cứng theo ĐÚNG 2 giá trị "cart"/"checkout" để nâng sàn rủi ro (trang giỏ hàng/đặt lịch, trang thanh toán/đặt cọc luôn tối
# thiểu ở mức đó, bất kể AI phân loại gì), nên giữ nguyên role, chỉ đổi NHÃN hiển thị cho trung tính.
#
# Trang liên hệ (công ty/cửa hàng không thanh toán trên website mà để khách liên hệ rồi thoả thuận/ký hợp đồng) dùng role "other" có sẵn của enum:
# không có sàn rủi ro nào (đúng bản chất — điền form liên hệ không phải giỏ hàng/thanh toán), AI vẫn phân tích hành động trên trang như các URL khác.
MODULE_TYPE_SEEDS = (
    {
        "key": "sales_support", "name": "Hỗ trợ bán hàng",
        "description": "Trợ lý xem thông tin sản phẩm/dịch vụ, hỗ trợ khách đặt hàng, đặt lịch hoặc liên hệ ngay trên website của bạn.",
        "roles": (
            ("product_detail", "URL trang sản phẩm / dịch vụ", True),
            ("product_listing", "URL trang danh sách sản phẩm / dịch vụ", False),
            ("cart", "URL trang giỏ hàng / đặt lịch (nếu có)", False),
            ("checkout", "URL trang thanh toán / đặt cọc (nếu có)", False),
            ("other", "URL trang liên hệ (nếu có)", False),
        ),
    },
    {
        "key": DOCUMENT_READER_KEY, "name": "Đọc tài liệu",
        "description": "Khách gửi tệp (PDF, Word, Excel, PowerPoint, ảnh, .md, .txt, .csv) ngay trong khung chat; trợ lý đọc cả chữ trong ảnh/bản scan (OCR) và trả lời theo nội dung tệp.",
        "roles": (),  # không cần URL nào
    },
)


def ensure_module_types() -> None:
    """Bổ sung loại module còn thiếu (idempotent) — tự chữa khi bảng bị dọn/chưa seed. Không sửa loại đã có (chủ hệ thống có thể đã đổi mô tả)."""
    changed = False
    for seed in MODULE_TYPE_SEEDS:
        if ModuleType.query.filter_by(key=seed["key"]).first() is not None:
            continue
        module_type = ModuleType(key=seed["key"], name=seed["name"], description=seed["description"], is_active=True)
        db.session.add(module_type)
        for order, (role, label, required) in enumerate(seed["roles"], start=1):
            module_type.url_roles.append(ModuleTypeUrlRole(url_role=role, label=label, is_required=required, display_order=order))
        changed = True
    if changed:
        db.session.commit()


def active_types() -> list[ModuleType]:
    ensure_module_types()
    return ModuleType.query.filter_by(is_active=True).order_by(ModuleType.id.asc()).all()


def types_for_client(types: list[ModuleType]) -> list[dict]:
    """Dữ liệu cho JS dựng ô nhập URL theo loại (server vẫn validate lại theo DB)."""
    return [
        {"id": t.id, "key": t.key, "name": t.name, "description": t.description or "",
         "roles": [{"role": r.url_role, "label": r.label, "required": r.is_required} for r in t.url_roles]}
        for t in types
    ]


def is_document_reader(module_type: ModuleType) -> bool:
    return module_type.key == DOCUMENT_READER_KEY


def document_reader_installed(bot_id: int) -> bool:
    """Bot đã cài module "Đọc tài liệu" (nguồn sự thật duy nhất cho việc widget có nhận tệp khách gửi hay không)."""
    return db.session.query(BotModule.id).join(ModuleType, BotModule.module_type_id == ModuleType.id).filter(
        BotModule.bot_id == bot_id, ModuleType.key == DOCUMENT_READER_KEY, BotModule.status == STATUS_READY,
    ).first() is not None


def list_modules(bot: Bot) -> list[BotModule]:
    return BotModule.query.filter_by(bot_id=bot.id).order_by(BotModule.id.desc()).all()


def get_module(bot: Bot, module_id: int) -> BotModule | None:
    return BotModule.query.filter_by(id=module_id, bot_id=bot.id).first()


def get_action(module: BotModule, action_id: int) -> ModuleAction | None:
    return ModuleAction.query.filter_by(id=action_id, module_id=module.id).first()


# ---------------------------------------------------------------- khai báo module (tải lên .zip)

def _validate_zip_upload(file) -> tuple[bytes | None, str | None]:
    """Đọc + kiểm tra 1 file .zip tải lên: cỡ tối đa, đủ 4 lớp an toàn khi giải nén (zip_extract), đúng 1 file .html/.htm ở cấp gốc.
    Trả (nội dung file, None) nếu hợp lệ, ngược lại (None, lỗi tiếng Việt). KHÔNG lưu entry_html_filename ở đây — worker xác định lại lúc xử lý."""
    if file is None or not getattr(file, "filename", ""):
        return None, "Chưa chọn tệp."
    raw = file.stream.read(Config.MODULE_ZIP_MAX_BYTES + 1)  # đọc tối đa cỡ cho phép + 1 byte: đủ để biết vượt, không nạp cả tệp khổng lồ vào RAM
    if not raw:
        return None, "Tệp trống."
    if len(raw) > Config.MODULE_ZIP_MAX_BYTES:
        return None, f"Tệp quá lớn (tối đa {Config.MODULE_ZIP_MAX_BYTES // (1024 * 1024)} MB)."
    try:
        zip_extract.extract_entry_html(raw, max_files=Config.MODULE_ZIP_MAX_FILES, max_uncompressed_bytes=Config.MODULE_ZIP_MAX_UNCOMPRESSED_BYTES)
    except zip_extract.ZipExtractError as exc:
        return None, str(exc)
    return raw, None


def create_module(bot: Bot, type_id, name: str, files_by_role: dict) -> tuple[BotModule | None, str | None]:
    """Tạo module (status=pending, worker sẽ nhận). Ô bắt buộc/tuỳ chọn lấy từ module_type_url_roles của loại đã chọn — KHÔNG hard-code theo loại.
    files_by_role: {url_role: FileStorage} (mỗi vai trò 1 file .zip). Trả (module, None) hoặc (None, lỗi tiếng Việt)."""
    ensure_module_types()
    module_type = db.session.get(ModuleType, type_id) if isinstance(type_id, int) else None
    if module_type is None or not module_type.is_active:
        return None, "Vui lòng chọn loại module."
    name = (name or "").strip()[:MAX_NAME_CHARS] or module_type.name

    if not module_type.url_roles:  # loại không cần URL/phân tích (Đọc tài liệu): cài xong là dùng được, mỗi bot chỉ cần 1
        if BotModule.query.filter_by(bot_id=bot.id, module_type_id=module_type.id).first() is not None:
            return None, f"Trợ lý đã cài module \"{module_type.name}\" rồi."
        module = BotModule(bot_id=bot.id, module_type_id=module_type.id, name=name, status=STATUS_READY, last_analyzed_at=datetime.utcnow())
        db.session.add(module)
        db.session.commit()
        return module, None

    rows = []
    for role in module_type.url_roles:
        file = (files_by_role or {}).get(role.url_role)
        if file is None or not getattr(file, "filename", ""):
            if role.is_required:
                return None, f"Vui lòng chọn tệp .zip cho {role.label}."
            continue
        raw, error = _validate_zip_upload(file)
        if error:
            return None, f"{role.label}: {error}"
        rows.append((role.url_role, raw))
    if len(rows) > Config.MODULE_MAX_URLS:
        return None, f"Tối đa {Config.MODULE_MAX_URLS} URL mỗi module."

    module = BotModule(bot_id=bot.id, module_type_id=module_type.id, name=name, status=STATUS_PENDING, progress_total=len(rows))
    db.session.add(module)
    db.session.flush()  # cần module.id để đặt tên khoá lưu trữ
    created = []
    for role, raw in rows:
        module_url = ModuleUrl(module_id=module.id, url_role=role, upload_status="uploaded")
        db.session.add(module_url)
        created.append((module_url, raw))
    db.session.flush()  # cần module_url.id để đặt tên khoá lưu trữ
    for module_url, raw in created:
        module_url.upload_storage_key = storage_service.save_module_zip(bot.team_id, bot.id, module.id, module_url.id, raw)
    db.session.commit()
    return module, None


def request_analysis(module: BotModule) -> str | None:
    """Đặt module về pending để worker xử lý lại (lần đầu hoặc "phân tích lại", dùng lại ĐÚNG file .zip đã tải lên). Lỗi tiếng Việt nếu đang chạy.
    Mọi module_urls trở lại 'uploaded' (kể cả url đã xác nhận domain trước đó) — mỗi lượt phân tích là 1 lượt xử lý mới nên cần xác nhận domain lại,
    tránh tình trạng source_url cũ không còn khớp với kết quả phân tích mới."""
    if module.status == STATUS_ANALYZING:
        return "Module đang được phân tích, vui lòng đợi hoàn tất."
    if is_document_reader(module.module_type):
        return "Module này không cần phân tích."
    for url in module.urls:
        url.upload_status, url.error_message = "uploaded", None
        url.source_url, url.detected_domain, url.entry_html_filename, url.extracted_at = None, None, None, None
    module.status = STATUS_PENDING
    module.error_message = None
    module.analysis_note = None
    module.progress_done, module.progress_total = 0, len(module.urls)
    db.session.commit()
    return None


# ---------------------------------------------------------------- xác nhận domain (M2 vá, bước 4)

def _required_role_keys(module: BotModule) -> set[str]:
    return {r.url_role for r in module.module_type.url_roles if r.is_required}


def pending_domain_confirmations(module: BotModule) -> list[ModuleUrl]:
    """module_urls BẮT BUỘC (theo module_type_url_roles) còn chưa qua bước xác nhận domain — url tuỳ chọn mà chủ bot bỏ qua hoàn toàn (không có
    dòng module_urls tương ứng) không tính. Dùng để chặn module chuyển 'ready' và để trang kết quả hiện rõ url nào còn thiếu."""
    required = _required_role_keys(module)
    return [u for u in module.urls if u.url_role in required and u.upload_status != "domain_confirmed"]


def _recompute_module_status(module: BotModule) -> None:
    """Gọi sau khi 1 module_urls được xác nhận domain: module đã phân tích xong (đang STATUS_AWAITING_DOMAIN) và giờ không còn url bắt buộc nào
    thiếu xác nhận thì mới cho chuyển 'ready'. Không đụng tới module đang pending/analyzing/failed."""
    if module.status != STATUS_AWAITING_DOMAIN:
        return
    if pending_domain_confirmations(module):
        return
    module.status = STATUS_READY
    db.session.commit()


def confirm_domain(module_url: ModuleUrl, raw_value: str) -> str | None:
    """Chủ bot xem/sửa detected_domain rồi xác nhận -> ghi vào source_url (giá trị DUY NHẤT dùng để so khớp bảo mật Origin, xem app/models.py:
    ModuleUrl). Validate bằng ĐÚNG logic domain đã có ở Phase A4/bot_domains (app/widget/domains.py), không viết logic validate domain song song.
    None nếu thành công, ngược lại lỗi tiếng Việt."""
    if module_url.upload_status not in ("extracted", "domain_confirmed"):
        return "URL này chưa phân tích xong nên chưa thể xác nhận domain."
    domain = widget_domains.normalize_domain(raw_value)
    if not domain or not widget_domains.is_valid_domain(domain):
        return "Domain không hợp lệ."
    module_url.source_url = domain
    module_url.upload_status = "domain_confirmed"
    db.session.commit()
    _recompute_module_status(module_url.module)
    return None


def delete_module(module: BotModule) -> str | None:
    if module.status == STATUS_ANALYZING:
        return "Module đang được phân tích, không thể xoá lúc này."
    db.session.delete(module)
    db.session.commit()
    return None


def domain_warning(bot: Bot, module: BotModule) -> str | None:
    """Cảnh báo nếu domain ĐÃ XÁC NHẬN (source_url) của module chưa nằm trong danh sách domain được nhúng widget của bot: khi đó widget không bao
    giờ chạy được action. Url chưa xác nhận domain thì chưa có gì để cảnh báo ở đây (xem pending_domain_confirmations cho cảnh báo riêng đó)."""
    allowed = [d.domain for d in bot.domains]
    missing = sorted({u.source_url for u in module.urls if u.source_url and not widget_domains.origin_allowed(f"https://{u.source_url}", allowed)})
    if missing:
        return ("Domain " + ", ".join(missing) + " chưa được khai báo ở Bước 3 (Xuất bản) là nơi nhúng widget — "
                "hành động chỉ chạy được trên website đã khai báo ở đó.")
    return None


# ---------------------------------------------------------------- lưu kết quả phân tích

def save_page_actions(module: BotModule, module_url: ModuleUrl, candidates: list[analysis_mod.Candidate]) -> tuple[int, int, int]:
    """Ghi kết quả phân tích 1 URL: thêm mới / cập nhật / xoá hành động của URL này. Trả (mới, cập nhật, xoá). Không commit.
    Hành động cũ giữ nguyên trạng thái duyệt CHỈ khi loại + selector_spec + mức rủi ro không đổi và không có lỗi thực thi — mọi thay đổi khác đều phải duyệt lại
    (chủ bot không vô tình để 1 kịch bản đã đổi vẫn ở trạng thái đã duyệt)."""
    existing = {a.action_name: a for a in ModuleAction.query.filter_by(module_id=module.id).all()}
    kept, created, updated = set(), 0, 0
    for candidate in candidates:
        name, n = candidate.action_name, 2
        while name in existing and (existing[name].url_id != module_url.id or name in kept):
            name, n = f"{candidate.action_name[:70]}_{n}", n + 1
        row = existing.get(name)
        kept.add(name)
        if row is None:
            row = ModuleAction(module_id=module.id, url_id=module_url.id, action_name=name, verified=False)
            db.session.add(row)
            existing[name] = row
            created += 1
        else:
            unchanged = (row.action_type == candidate.action_type and row.selector_spec == candidate.selector_spec
                         and row.risk_level == candidate.risk_level and row.failure_reason is None)
            row.verified = bool(row.verified and unchanged)
            updated += 1
        row.action_type, row.description = candidate.action_type, candidate.description
        row.selector_spec, row.confidence, row.risk_level = candidate.selector_spec, candidate.confidence, candidate.risk_level
        row.failure_reason, row.failed_at = None, None
    stale = [a for a in existing.values() if a.url_id == module_url.id and a.action_name not in kept]
    for action in stale:
        db.session.delete(action)
    db.session.flush()
    return created, updated, len(stale)


# ---------------------------------------------------------------- duyệt hành động (M4)

def url_of(action: ModuleAction) -> ModuleUrl | None:
    return action.url or db.session.get(ModuleUrl, action.url_id)


def effective_risk_of(action: ModuleAction) -> str:
    """Mức rủi ro HIỆU LỰC: max(mức đã lưu, sàn cứng tính lại từ selector_spec). Sửa tay DB để hạ mức cũng không có tác dụng."""
    url = url_of(action)
    context_url = (url.source_url or url.detected_domain or "") if url else ""
    return risk.effective_risk(action.risk_level, action.action_type, action.selector_spec or {}, url_role=url.url_role if url else "other", url=context_url)


def payment_allowed(bot_id: int) -> bool:
    settings = BotSettings.query.filter_by(bot_id=bot_id).first()
    return bool(settings and settings.allow_agent_payment_actions)


def approve_action(bot: Bot, action: ModuleAction) -> str | None:
    """Chủ bot duyệt thủ công 1 hành động (KHÔNG có duyệt hàng loạt). None nếu thành công, ngược lại lý do tiếng Việt."""
    url = url_of(action)
    if url is None or url.upload_status != "domain_confirmed":
        return "Trang của hành động này chưa được xác nhận domain — vào mục \"Các trang đã khai báo\" để xác nhận trước khi duyệt."
    level = effective_risk_of(action)
    reason = risk.approval_check(level, action.confidence, allow_payment=payment_allowed(bot.id))
    if reason:
        return reason
    action.risk_level = level  # ghi nhận mức hiệu lực (không bao giờ thấp hơn sàn)
    action.verified = True
    action.failure_reason, action.failed_at = None, None
    db.session.commit()
    return None


def unapprove_action(action: ModuleAction) -> None:
    action.verified = False
    db.session.commit()


def set_payment_switch(bot: Bot, enabled: bool) -> None:
    """Công tắc allow_agent_payment_actions. TẮT lại thì mọi action thanh toán đã duyệt bị bỏ duyệt luôn (không còn "đã duyệt" mà agent không được phép dùng)."""
    from app.dashboard import service as dashboard_service

    settings = dashboard_service.get_or_create_settings(bot)
    settings.allow_agent_payment_actions = bool(enabled)
    if not enabled:
        for action in ModuleAction.query.join(BotModule, ModuleAction.module_id == BotModule.id).filter(BotModule.bot_id == bot.id, ModuleAction.verified.is_(True)).all():
            if effective_risk_of(action) == "payment":
                action.verified = False
    db.session.commit()


# ---------------------------------------------------------------- danh sách tool cho agent (M3)

RESERVED_TOOL_NAMES = analysis_mod.RESERVED_NAMES


def usable_actions(bot_id: int) -> list[tuple[str, ModuleAction]]:
    """[(tên tool, action)] của bot: chỉ action ĐÃ duyệt, trang của nó ĐÃ xác nhận domain (source_url — nếu không thì app/modules/dispatch.py không
    có domain thật để so khớp Origin của widget), và (nếu mức hiệu lực là payment) chỉ khi công tắc của bot đang bật. Tên tool là action_name;
    trùng nhau giữa các module của bot hoặc trùng tên công cụ hệ thống thì thêm hậu tố _<id> (ổn định theo id nên process_key không đổi vô cớ)."""
    rows = (
        ModuleAction.query.join(BotModule, ModuleAction.module_id == BotModule.id)
        .filter(BotModule.bot_id == bot_id, ModuleAction.verified.is_(True)).order_by(ModuleAction.id.asc()).all()
    )
    allow_payment = payment_allowed(bot_id)
    used, out = set(RESERVED_TOOL_NAMES), []
    for action in rows:
        url = url_of(action)
        if url is None or url.upload_status != "domain_confirmed":
            continue
        if effective_risk_of(action) == "payment" and not allow_payment:
            continue
        name = action.action_name if action.action_name not in used else f"{action.action_name[:70]}_{action.id}"
        used.add(name)
        out.append((name, action))
    return out


def agent_tools_for_bot(bot_id: int) -> list[dict]:
    """Manifest hành động cho AgentRunner: [{"name", "description", "params": [{"name"}], "confirm": bool}] — mô tả lấy từ module_actions.description."""
    tools = []
    for name, action in usable_actions(bot_id):
        tools.append({
            "name": name, "description": action.description, "confirm": effective_risk_of(action) == "payment",
            "params": [{"name": p} for p in spec_mod.params_of(action.selector_spec or {})],
        })
    return tools


def resolve_tool(bot_id: int, tool_name: str) -> ModuleAction | None:
    return next((action for name, action in usable_actions(bot_id) if name == tool_name), None)


def mark_action_failed(action: ModuleAction, reason: str) -> None:
    """Widget báo selector không còn khớp: bỏ duyệt + ghi lý do để dashboard hiện cảnh báo. KHÔNG tự đoán selector mới, KHÔNG tự crawl lại."""
    action.verified = False
    action.failure_reason = reason[:50]
    action.failed_at = datetime.utcnow()
