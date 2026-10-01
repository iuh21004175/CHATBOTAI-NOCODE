"""Phân tích 1 trang bằng LLM (M2): HTML rút gọn -> danh sách hành động agent có thể thực hiện, RỒI kiểm chứng bằng code trước khi lưu.

LLM chỉ ĐỀ XUẤT; mọi thứ sau đây do code quyết định (không tin LLM):
- selector_spec đúng cấu trúc của action_type (core/website_actions/spec.py), selector nằm trong tập con được hỗ trợ;
- MỌI selector phải khớp ít nhất 1 phần tử trong trang thật đã crawl — selector bịa bị loại (không lưu action "ma" mà chủ bot phải đoán);
- risk_level cuối = max(AI, sàn cứng của risk.floor_risk) — AI phân loại nhầm không hạ được mức rủi ro.

Dùng lại đúng khuôn call_structured: JSON mode + parse chặt + gọi lại 1 lần khi JSON lỗi (structured.MAX_JSON_ATTEMPTS), usage của MỌI lần gọi vào tracker.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from core.context_engine import structured
from core.context_engine.cost import LLMUsageTracker, parse_usage
from core.website_actions import dom, risk
from core.website_actions import spec as spec_mod

logger = logging.getLogger("website_actions.analysis")

MAX_ACTIONS_PER_PAGE = 20
MAX_DESCRIPTION_CHARS = 500
RESERVED_NAMES = frozenset({"search_knowledge_base", "summarize_conversation", "finish_answer", "ask_clarification", "decline"})

SYSTEM_PROMPT = """Bạn phân tích HTML rút gọn của MỘT trang website để liệt kê các HÀNH ĐỘNG mà một trợ lý AI có thể thực hiện thay khách ngay trên trang đó.
Bạn chỉ thấy các phần tử trong HTML được cung cấp: TUYỆT ĐỐI không bịa phần tử, id, class hay đường dẫn không có trong HTML.

Trả về DUY NHẤT một đối tượng JSON (json), không kèm lời giải thích:
{"actions": [{"action_type": "...", "action_name": "...", "description": "...", "selector_spec": {...}, "confidence": 0.0, "risk_level": "..."}]}

action_type và cấu trúc selector_spec (chỉ dùng đúng các khoá này):
- "navigate":    {"href": "/duong-dan-hoac-URL-cung-website", "selector": "(tuỳ chọn) liên kết để bấm"}
- "click":       {"selector": "...", "event": "click", "fields": [{"selector": "...", "value_from_slot": "ten_tham_so"}]}  (fields tuỳ chọn, điền trước khi bấm, ví dụ số lượng)
- "add_to_cart": như click, dùng cho nút thêm vào giỏ hàng / mua
- "fill_form":   {"form_selector": "...", "fields": [{"selector": "...", "value_from_slot": "ten_tham_so"}], "submit": false, "submit_selector": "(tuỳ chọn)"}
                 mặc định CHỈ điền, chỉ đặt "submit": true khi mục đích của hành động là gửi form
- "read_info":   {"selector": "...", "attribute": "text"}  đọc nội dung (giá, tình trạng còn hàng...) để trả lời khách

Quy tắc selector: chỉ dùng thẻ, #id, .class, [thuộc-tính], [thuộc-tính="giá trị"], [thuộc-tính^=..], [thuộc-tính$=..], [thuộc-tính*=..] và tổ hợp cách nhau bằng
khoảng trắng hoặc ">". KHÔNG dùng :nth-child, :not, :first, dấu phẩy, +, ~ hay bất kỳ pseudo-class nào. Ưu tiên #id, sau đó tới .class hoặc [name="..."] duy nhất.
value_from_slot là tên tham số bằng chữ thường/số/gạch dưới (vd "so_luong", "ho_ten", "so_dien_thoai") — giá trị sẽ do khách cung cấp khi trò chuyện.

action_name: tên ngắn bằng chữ thường không dấu, gạch dưới, không trùng nhau (vd "them_gio_hang", "doc_gia_san_pham").
description: 1-2 câu tiếng Việt cho trợ lý hiểu KHI NÀO nên dùng hành động này và cần khách cung cấp gì (vd "Dùng khi khách muốn thêm sản phẩm đang xem vào giỏ hàng. Cần số lượng nếu khách nói rõ.").
confidence: 0-1, mức chắc chắn rằng selector đúng và hành động đúng mục đích. Không chắc thì cho thấp.
risk_level: "read_only" (chỉ xem/đọc/điều hướng), "cart" (thay đổi giỏ hàng), "payment" (thanh toán, đặt hàng, nhập thông tin thẻ/mật khẩu). Không chắc thì chọn mức CAO hơn.
Chỉ đề xuất hành động thực sự hữu ích cho việc hỗ trợ bán hàng; tối đa 12 hành động; trang không có gì hữu ích thì trả {"actions": []}."""


@dataclass
class Candidate:
    action_type: str
    action_name: str
    description: str
    selector_spec: dict
    confidence: float
    risk_level: str            # mức CUỐI CÙNG = max(AI, sàn cứng)
    ai_risk_level: str
    risk_reasons: list[str] = field(default_factory=list)


@dataclass
class PageAnalysis:
    candidates: list[Candidate]
    dropped: list[str]         # "tên: lý do" — hành động LLM đề xuất nhưng code loại (minh bạch, hiện cho chủ bot)


class AnalysisError(ValueError):
    """LLM không trả JSON đúng hợp đồng sau khi đã gọi lại."""


def slugify(name) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", risk.fold(name)).strip("_")[:60]
    if not slug:
        return ""
    if not slug[0].isalpha():
        slug = "a_" + slug
    return slug + "_action" if slug in RESERVED_NAMES else slug


def build_messages(reduced_html: str, *, url: str, url_role_label: str, module_type_name: str) -> list[dict]:
    user = f"Loại module: {module_type_name}\nVai trò trang: {url_role_label}\nURL: {url}\n\nHTML rút gọn:\n{reduced_html}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def _parse_actions(text: str) -> list:
    payload = structured.extract_json(text)
    if not payload:
        raise AnalysisError("phản hồi rỗng")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AnalysisError(f"không phải JSON hợp lệ: {exc.msg}") from exc
    actions = data.get("actions") if isinstance(data, dict) else None
    if not isinstance(actions, list):
        raise AnalysisError('thiếu danh sách "actions"')
    return actions


def _validate(item, *, root: dom.Node, url: str, url_role: str, page_host: str) -> Candidate:
    """1 hành động do LLM đề xuất -> Candidate đã kiểm chứng. Ném spec_mod.SpecError với lý do nếu phải loại."""
    if not isinstance(item, dict):
        raise spec_mod.SpecError("mục không phải đối tượng")
    action_type = item.get("action_type")
    spec = spec_mod.normalize(action_type, item.get("selector_spec"), page_host=page_host)
    missing = spec_mod.missing_selectors(root, spec)
    if missing:
        raise spec_mod.SpecError("selector không khớp phần tử nào trong trang: " + ", ".join(missing[:3]))
    name = slugify(item.get("action_name"))
    description = item.get("description")
    if not name:
        raise spec_mod.SpecError("thiếu action_name")
    if not isinstance(description, str) or not description.strip():
        raise spec_mod.SpecError("thiếu description")
    confidence = item.get("confidence")
    confidence = float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) and confidence == confidence else 0.0
    ai_level = item.get("risk_level") if item.get("risk_level") in risk.LEVELS else "cart"  # AI trả mức lạ -> thận trọng, không coi là read_only
    floor, reasons = risk.floor_risk(action_type, spec, url_role=url_role, url=url, root=root)
    return Candidate(
        action_type=action_type, action_name=name, description=description.strip()[:MAX_DESCRIPTION_CHARS], selector_spec=spec,
        confidence=min(1.0, max(0.0, confidence)), risk_level=risk.max_level(ai_level, floor), ai_risk_level=ai_level, risk_reasons=reasons,
    )


def analyze_page(html: str, *, url: str, url_role: str, url_role_label: str, module_type_name: str, page_host: str, call: structured.LLMCall,
                 tracker: LLMUsageTracker, max_chars: int) -> PageAnalysis:
    root = dom.parse(html)
    messages = build_messages(dom.reduce(root, max_chars), url=url, url_role_label=url_role_label, module_type_name=module_type_name)
    raw_actions, last_error = None, None
    for attempt in range(structured.MAX_JSON_ATTEMPTS):
        reply = call(messages)
        tracker.record("module_analysis" if attempt == 0 else "module_analysis_retry", parse_usage(reply.token_usage))
        try:
            raw_actions = _parse_actions(reply.content)
            break
        except AnalysisError as exc:
            last_error = exc
            logger.warning("Phân tích module: JSON không hợp lệ (lần %d/%d): %s", attempt + 1, structured.MAX_JSON_ATTEMPTS, exc)
    if raw_actions is None:
        raise AnalysisError(f"LLM không trả JSON hợp lệ sau {structured.MAX_JSON_ATTEMPTS} lần: {last_error}")

    candidates, dropped, used = [], [], set()
    for item in raw_actions[:MAX_ACTIONS_PER_PAGE]:
        label = str(item.get("action_name")) if isinstance(item, dict) else "?"
        try:
            candidate = _validate(item, root=root, url=url, url_role=url_role, page_host=page_host)
        except spec_mod.SpecError as exc:
            dropped.append(f"{label}: {exc}")
            continue
        base, n = candidate.action_name, 2
        while candidate.action_name in used:  # trùng tên trong cùng trang: đánh số để mỗi action là 1 tool riêng
            candidate.action_name, n = f"{base[:70]}_{n}", n + 1
        used.add(candidate.action_name)
        candidates.append(candidate)
    return PageAnalysis(candidates, dropped)
