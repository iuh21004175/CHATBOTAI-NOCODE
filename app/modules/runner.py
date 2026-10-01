"""Quy trình phân tích 1 module (M2 vá + M5): với mỗi module_urls có upload_status='uploaded' -> giải nén an toàn file .zip đã tải lên -> phát hiện
domain (chỉ để GỢI Ý, không tự đặt source_url) -> LLM đề xuất hành động -> kiểm chứng -> ghi module_actions -> trừ Credit.

Tách khỏi vòng lặp worker (workers/module_analysis.py) để test được không cần Redis/MinIO thật: get_zip, llm_call là tham số.

Nguyên tắc lỗi: lỗi của 1 URL (zip hỏng, thiếu đúng 1 file .html cấp gốc, JSON hỏng, LLM lỗi...) ghi RÕ vào module_urls.error_message (upload_status=
'failed') rồi đi tiếp URL khác; module chỉ 'failed' khi KHÔNG URL nào phân tích được. Không nuốt lỗi: mọi lỗi đều vào log + cột error_message.

module chỉ chuyển 'ready' ngay nếu mọi URL bắt buộc đã được xác nhận domain từ trước (phân tích lại một module đã có domain xác nhận); bình thường sau
lần phân tích ĐẦU TIÊN module dừng ở 'awaiting_domain_confirmation' — chờ chủ bot xác nhận domain cho từng URL (app/modules/service.py:confirm_domain)
trước khi tự chuyển 'ready'.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from decimal import Decimal

from app.credits import service as credits_service
from app.models import BotModule, ModuleUrl
from app.modules import events, service
from config import Config
from core import storage_service
from core.context_engine import cost_estimate as ce
from core.context_engine import execution_cost as xc
from core.context_engine import structured
from core.context_engine.cost import LLMUsageTracker
from core.website_actions import analysis as analysis_mod
from core.website_actions import domain_detect
from core.website_actions import zip_extract
from extensions import db

logger = logging.getLogger(__name__)

PROMPT_OVERHEAD_TOKENS = 1500   # system prompt + khung của lệnh gọi phân tích
ANALYSIS_TEMPERATURE = 0.1      # bài toán trích xuất, cần ổn định


def estimate_analysis_vnd(url_count: int) -> Decimal:
    """Giá BÁN cao nhất của 1 lần phân tích (cận trên: đầu vào chạm trần, đầu ra chạm trần, giờ cao điểm, không trúng cache, mỗi URL gọi lại 1 lần vì JSON lỗi)."""
    input_tokens = math.ceil(Config.MODULE_LLM_INPUT_CHARS / ce.CHARS_PER_TOKEN) + PROMPT_OVERHEAD_TOKENS
    per_call = ce.call_cost_vnd(0, input_tokens, Config.MODULE_ANALYSIS_MAX_TOKENS, ce.PEAK)
    return xc.money(xc.money(per_call * structured.MAX_JSON_ATTEMPTS * max(1, url_count)) * Config.PLATFORM_MARKUP_MULTIPLIER)


def _get_zip(module_url: ModuleUrl) -> bytes:
    obj = storage_service.get_file(module_url.upload_storage_key)
    try:
        return obj.read()
    finally:
        obj.close()
        obj.release_conn()


def _set_status(module: BotModule, status: str, *, error: str | None = None) -> None:
    module.status = status
    module.error_message = error
    module.updated_at = datetime.utcnow()


def analyze_module(module_id: int, *, get_zip=None, llm_call=None, now=datetime.utcnow, keepalive=None) -> str | None:
    """Phân tích module đã ở trạng thái 'analyzing' (worker đã nhận). Trả trạng thái cuối ('awaiting_domain_confirmation'/'ready'/'failed') hoặc None
    nếu module không còn tồn tại. keepalive(): gọi sau mỗi URL để worker gia hạn khoá (phân tích nhiều URL có thể lâu hơn TTL của khoá)."""
    module = db.session.get(BotModule, module_id)
    if module is None:
        return None
    urls = [u for u in module.urls if u.upload_status == "uploaded"]  # chỉ url CHỜ xử lý lượt này (url đã domain_confirmed không bị đụng tới)
    module.progress_done, module.progress_total = 0, len(urls)
    module.analysis_note = None
    team_id = module.bot.team_id
    estimate = estimate_analysis_vnd(len(urls))

    # M5: số dư team phải đủ ước tính cao nhất (chưa tốn phí LLM nào nếu bị chặn)
    if not credits_service.has_credit_for_analysis(team_id, estimate):
        _set_status(module, service.STATUS_FAILED, error=f"Không đủ AI Credit để phân tích (cần tối thiểu khoảng {int(math.ceil(estimate)):,}đ). Vui lòng nạp thêm Credit rồi bấm \"Phân tích lại\".".replace(",", "."))
        db.session.commit()
        events.emit_module_status(module, service.STATUS_FAILED, error=module.error_message)
        return service.STATUS_FAILED
    db.session.commit()
    events.emit_module_status(module, service.STATUS_ANALYZING)

    tracker = LLMUsageTracker()
    call = llm_call or structured.deepseek_call(ANALYSIS_TEMPERATURE, Config.MODULE_ANALYSIS_MAX_TOKENS)
    type_name = module.module_type.name
    role_labels = {r.url_role: r.label for r in module.module_type.url_roles}
    succeeded, dropped_notes, action_total = 0, [], 0

    get_zip = get_zip or _get_zip
    for url_id in [u.id for u in urls]:
        module_url = db.session.get(ModuleUrl, url_id)
        try:
            page = zip_extract.extract_entry_html(
                get_zip(module_url), max_files=Config.MODULE_ZIP_MAX_FILES, max_uncompressed_bytes=Config.MODULE_ZIP_MAX_UNCOMPRESSED_BYTES,
            )
        except Exception as exc:  # lỗi RIÊNG url này (zip hỏng, thiếu đúng 1 file .html cấp gốc...): ghi rõ rồi đi tiếp url khác
            db.session.rollback()
            module_url = db.session.get(ModuleUrl, url_id)
            expected = isinstance(exc, zip_extract.ZipExtractError)
            if not expected:
                logger.exception("module=%s url=%s: lỗi khi giải nén", module.id, url_id)
            module_url.upload_status = "failed"
            module_url.error_message = (str(exc) if expected else f"Lỗi khi giải nén: {type(exc).__name__}: {exc}")[:1000]
            module = db.session.get(BotModule, module_id)
            module.progress_done += 1
            db.session.commit()
            events.emit_module_status(module, service.STATUS_ANALYZING)
            if keepalive:
                keepalive()
            continue

        # Giải nén + phát hiện domain đã xong: chốt NGAY (trước khi thử LLM) để chủ bot xác nhận domain được dù bước LLM bên dưới có lỗi hay không.
        module_url.entry_html_filename = page.entry_filename
        module_url.upload_status, module_url.extracted_at = "extracted", now()
        module_url.detected_domain = domain_detect.detect(page.html)
        db.session.commit()

        context_url = f"https://{module_url.detected_domain}/" if module_url.detected_domain else ""
        try:
            analyzed = analysis_mod.analyze_page(
                page.html, url=context_url, url_role=module_url.url_role, url_role_label=role_labels.get(module_url.url_role, module_url.url_role),
                module_type_name=type_name, page_host=module_url.detected_domain or "", call=call, tracker=tracker, max_chars=Config.MODULE_LLM_INPUT_CHARS,
            )
            service.save_page_actions(module, module_url, analyzed.candidates)
            module_url.error_message = None
            dropped_notes.extend(f"{module_url.url_role}: đã bỏ hành động {note}" for note in analyzed.dropped)
            action_total += len(analyzed.candidates)
            succeeded += 1
        except Exception as exc:  # LLM lỗi: KHÔNG hạ upload_status xuống 'failed' — đã giải nén + phát hiện domain thành công, vẫn xác nhận domain được
            db.session.rollback()
            module_url = db.session.get(ModuleUrl, url_id)
            expected = isinstance(exc, analysis_mod.AnalysisError)
            if not expected:
                logger.exception("module=%s url=%s: lỗi khi phân tích", module.id, url_id)
            module_url.error_message = (str(exc) if expected else f"Lỗi khi phân tích: {type(exc).__name__}: {exc}")[:1000]

        module = db.session.get(BotModule, module_id)
        module.progress_done += 1
        db.session.commit()
        events.emit_module_status(module, service.STATUS_ANALYZING)
        if keepalive:
            keepalive()

    module = db.session.get(BotModule, module_id)
    llm = xc.llm_cost(tracker.calls, now())
    price = xc.price_execution(llm, markup=Config.PLATFORM_MARKUP_MULTIPLIER)
    if tracker.calls:  # đã tốn LLM thật (kể cả khi mọi URL thất bại sau đó) thì vẫn tính phí phần đã dùng
        module.analysis_cost_vnd = price.total_cost_vnd
        module.analysis_charged_vnd = credits_service.settle_module_analysis(team_id, module_id=module.id, price=price)
    if not llm.reported and tracker.calls:
        logger.warning("module=%s có lệnh gọi LLM không đo được usage — phần đó không được tính phí", module.id)

    module.last_analyzed_at = now()
    if succeeded:
        notes = list(dropped_notes)
        if action_total == 0:
            notes.append("Không tìm thấy hành động phù hợp. Nếu trang có nội dung tải thêm bằng JavaScript SAU KHI mở (vd cuộn xuống mới hiện sản phẩm), hãy đợi trang tải xong rồi lưu trang lại (Webpage, Complete) và tải lên lại.")
        module.analysis_note = "\n".join(notes)[:4000] or None
        if service.pending_domain_confirmations(module):
            _set_status(module, service.STATUS_AWAITING_DOMAIN)
        else:
            _set_status(module, service.STATUS_READY)
    else:
        errors = [f"{role_labels.get(u.url_role, u.url_role)}: {u.error_message}" for u in module.urls if u.error_message]
        _set_status(module, service.STATUS_FAILED, error=("Không phân tích được URL nào. " + " | ".join(errors))[:4000])
    db.session.commit()
    events.emit_module_status(module, module.status, error=module.error_message)
    return module.status
