"""Cấu trúc `selector_spec` của module_actions — kiểm tra/chuẩn hoá (thuần, không DB, không mạng).

Vì sao mỗi action_type có 1 cấu trúc riêng (thay vì 1 cấu trúc chung): widget cần biết CHÍNH XÁC phải làm gì với DOM mà không suy đoán, và server cần
liệt kê được mọi selector + mọi tham số khách phải cung cấp để (a) kiểm chứng selector có thật trong trang đã crawl, (b) dựng tham số cho tool của agent,
(c) quét từ khoá rủi ro. Chỉ khoá trong danh sách trắng được giữ (mọi khoá lạ do LLM bịa ra đều bị bỏ):

  navigate      {"href": "/gio-hang" | "https://cung-domain/...", "selector": tuỳ chọn (bấm liên kết thay vì đổi địa chỉ)}
  click         {"selector": "...", "event": "click", "fields": tuỳ chọn [{"selector", "value_from_slot"}] — điền trước khi bấm (vd số lượng)}
  add_to_cart   như click
  fill_form     {"form_selector": "...", "fields": [{"selector", "value_from_slot"}] (>=1), "submit": false, "submit_selector": tuỳ chọn}
                — mặc định CHỈ điền, không gửi form; submit=true mới gửi (và khi đó rủi ro do risk.py quyết định)
  read_info     {"selector": "...", "attribute": "text" | tên thuộc tính} — widget đọc rồi trả nội dung về cho agent

value_from_slot = tên tham số (chữ thường/số/gạch dưới) mà agent phải truyền khi gọi tool — nội dung do KHÁCH cung cấp trong hội thoại.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from core.website_actions import dom

SLOT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
ATTRIBUTE_RE = re.compile(r"^[a-zA-Z][\w:-]{0,30}$")
MAX_FIELDS = 12
ACTION_TYPES = ("navigate", "click", "fill_form", "add_to_cart", "read_info")


class SpecError(ValueError):
    """selector_spec không hợp lệ cho action_type."""


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def same_site(host: str, page_host: str) -> bool:
    host, page_host = host.lower().removeprefix("www."), page_host.lower().removeprefix("www.")
    return bool(host) and (host == page_host or host.endswith("." + page_host))


def _selector(value, label: str) -> str:
    if not isinstance(value, str):
        raise SpecError(f"{label} phải là chuỗi selector")
    value = value.strip()
    try:
        dom.parse_selector(value)
    except dom.SelectorError as exc:
        raise SpecError(f"{label}: {exc}") from exc
    return value


def _fields(raw) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_FIELDS:
        raise SpecError("fields phải là danh sách (tối đa %d)" % MAX_FIELDS)
    fields = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SpecError(f"fields[{i}] phải là đối tượng")
        slot = item.get("value_from_slot")
        if not isinstance(slot, str) or not SLOT_RE.match(slot):
            raise SpecError(f"fields[{i}].value_from_slot phải là tên chữ thường/số/gạch dưới")
        fields.append({"selector": _selector(item.get("selector"), f"fields[{i}].selector"), "value_from_slot": slot})
    return fields


def normalize(action_type: str, spec, *, page_host: str) -> dict:
    """Trả selector_spec đã làm sạch (chỉ khoá trong danh sách trắng). Ném SpecError nếu sai cấu trúc/selector ngoài tập con/href sang site khác."""
    if action_type not in ACTION_TYPES:
        raise SpecError(f"action_type không hợp lệ: {action_type!r}")
    if not isinstance(spec, dict):
        raise SpecError("selector_spec phải là đối tượng")

    if action_type == "navigate":
        href = spec.get("href")
        if not isinstance(href, str) or not href.strip():
            raise SpecError("navigate cần href")
        href = href.strip()
        parsed = urlparse(href)
        relative = not parsed.scheme and not parsed.netloc and not href.startswith("//")  # "/gio-hang", "gio-hang.html", "?p=2", "#top"
        if not relative and (parsed.scheme not in ("http", "https") or not same_site(parsed.hostname or "", page_host)):
            raise SpecError("href phải là đường dẫn tương đối hoặc URL cùng website")
        out = {"href": href}
        if spec.get("selector") not in (None, ""):
            out["selector"] = _selector(spec.get("selector"), "selector")
        return out

    if action_type in ("click", "add_to_cart"):
        out = {"selector": _selector(spec.get("selector"), "selector"), "event": "click"}
        fields = _fields(spec.get("fields"))
        if fields:
            out["fields"] = fields
        return out

    if action_type == "fill_form":
        fields = _fields(spec.get("fields"))
        if not fields:
            raise SpecError("fill_form cần ít nhất 1 trường")
        out = {"form_selector": _selector(spec.get("form_selector"), "form_selector"), "fields": fields, "submit": bool(spec.get("submit", False))}
        if spec.get("submit_selector") not in (None, ""):
            out["submit_selector"] = _selector(spec.get("submit_selector"), "submit_selector")
        return out

    attribute = spec.get("attribute", "text") or "text"  # read_info
    if not isinstance(attribute, str) or not (attribute == "text" or ATTRIBUTE_RE.match(attribute)) or attribute.lower().startswith("on"):
        raise SpecError("attribute không hợp lệ")
    return {"selector": _selector(spec.get("selector"), "selector"), "attribute": attribute}


def selectors_of(spec: dict) -> list[str]:
    """Mọi selector trong 1 spec ĐÃ chuẩn hoá (để kiểm chứng và quét rủi ro)."""
    out = [spec[key] for key in ("selector", "form_selector", "submit_selector") if isinstance(spec.get(key), str)]
    out.extend(f["selector"] for f in spec.get("fields") or [])
    return out


def params_of(spec: dict) -> list[str]:
    """Tên tham số agent phải/được truyền khi gọi tool (giữ thứ tự, không trùng)."""
    seen: list[str] = []
    for field in spec.get("fields") or []:
        if field["value_from_slot"] not in seen:
            seen.append(field["value_from_slot"])
    return seen


def missing_selectors(root: dom.Node, spec: dict) -> list[str]:
    """Các selector KHÔNG khớp phần tử nào trong trang đã crawl (selector bịa/đã lỗi thời). Rỗng = mọi selector đều có thật."""
    return [selector for selector in selectors_of(spec) if not dom.select(root, selector)]
