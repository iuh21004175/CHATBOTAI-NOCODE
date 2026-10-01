"""Phân loại rủi ro + ngưỡng duyệt của action (M4) — thuần, không DB.

risk_level do AI gán lúc phân tích, NHƯNG backend luôn có 1 lớp kiểm tra cứng (`floor_risk`) đọc chính selector_spec + phần tử thật trong trang đã crawl:
mức cuối cùng = max(AI, sàn) — AI phân loại nhầm không bao giờ hạ mức xuống thấp hơn thực tế. Sàn được tính lại ở 3 nơi (lúc phân tích, lúc duyệt, lúc
giao cho agent) nên sửa tay dữ liệu trong DB cũng không hạ được mức.

Sàn 'payment': input type=password; trường tên/id/autocomplete/placeholder kiểu số thẻ/CVV/hạn thẻ; từ khoá thanh toán (checkout, payment, thanh toán,
đặt hàng, các cổng thanh toán...) trong selector/href/URL/thuộc tính/chữ của phần tử; action nằm trên URL vai trò 'checkout'.
Sàn 'cart': action_type add_to_cart; từ khoá giỏ hàng (add to cart, thêm vào giỏ, mua ngay...); action nằm trên URL vai trò 'cart'.
"""
from __future__ import annotations

import json
import re
import unicodedata

from core.website_actions import dom
from core.website_actions import spec as spec_mod

LEVELS = ("read_only", "cart", "payment")
# Ngưỡng confidence tối thiểu để được duyệt (verified=true). payment còn cần công tắc allow_agent_payment_actions của bot.
# 0.6 / 0.8 theo đặc tả M4; 0.9 cho payment là mức thận trọng thêm (đặt ở đây để chỉnh 1 chỗ sau khi có dữ liệu thật).
CONFIDENCE_MIN = {"read_only": 0.6, "cart": 0.8, "payment": 0.9}

_PAYMENT_WORDS = re.compile(
    r"checkout|payment|paypal|vnpay|momo|zalopay|payos|onepay|thanh[\s_-]?toan|place[\s_-]?order|dat[\s_-]?hang|pay[\s_-]?now|credit[\s_-]?card|billing"
)
_CARD_FIELD = re.compile(r"card|cc[-_]?(num|number|exp|csc|cvc)|cvv|cvc|ccnum|expir|iban|otp")
_CART_WORDS = re.compile(r"add[\s_-]?to[\s_-]?(cart|basket|bag)|them[\s_-]?(vao[\s_-]?)?gio|gio[\s_-]?hang|cart|basket|buy[\s_-]?now|mua[\s_-]?ngay")


def fold(text: str) -> str:
    """Chữ thường, bỏ dấu tiếng Việt ('Đặt hàng' -> 'dat hang') để một bộ từ khoá dùng cho cả có dấu lẫn không dấu."""
    text = unicodedata.normalize("NFD", str(text or "").lower().replace("đ", "d"))
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def rank(level: str) -> int:
    return LEVELS.index(level) if level in LEVELS else 1  # giá trị lạ -> coi như 'cart' (thận trọng, không tin AI)


def max_level(*levels: str) -> str:
    return LEVELS[max(rank(level) for level in levels)]


def _nodes_and_inputs(root: dom.Node | None, spec: dict) -> list[dom.Node]:
    """Phần tử khớp các selector của spec + (nếu là form) mọi phần tử con — trường thẻ/mật khẩu nằm trong form."""
    if root is None:
        return []
    nodes: list[dom.Node] = []
    for selector in spec_mod.selectors_of(spec):
        try:
            matched = dom.select(root, selector)
        except dom.SelectorError:
            continue
        for node in matched:
            nodes.extend(node.iter() if node.tag in ("form", "fieldset") else [node])
    return nodes


def floor_risk(action_type: str, spec: dict, *, url_role: str = "other", url: str = "", root: dom.Node | None = None) -> tuple[str, list[str]]:
    """(mức sàn, lý do) — lý do đưa ra giao diện để chủ bot hiểu vì sao action bị xếp mức đó."""
    level, reasons = "read_only", []

    def raise_to(new: str, why: str) -> None:
        nonlocal level
        if rank(new) > rank(level):
            level = new
        reasons.append(why)

    if url_role == "checkout":
        raise_to("payment", "nằm trên trang thanh toán")
    if url_role == "cart":
        raise_to("cart", "nằm trên trang giỏ hàng")
    if action_type == "add_to_cart":
        raise_to("cart", "thêm vào giỏ hàng")

    nodes = _nodes_and_inputs(root, spec)
    for node in nodes:
        if node.tag == "input" and (node.attrs.get("type") or "").lower() == "password":
            raise_to("payment", "có trường mật khẩu")
        field_text = fold(" ".join(node.attrs.get(k, "") for k in ("name", "id", "autocomplete", "placeholder")))
        if node.tag in ("input", "select", "textarea") and _CARD_FIELD.search(field_text):
            raise_to("payment", "có trường thông tin thẻ")

    blob = fold(" ".join([
        json.dumps(spec, ensure_ascii=False), url or "",
        *(" ".join(str(node.attrs.get(k, "")) for k in ("id", "class", "name", "href", "action", "value", "onclick")) + " " + node.text for node in nodes),
    ]))
    if _PAYMENT_WORDS.search(blob):
        raise_to("payment", "có từ khoá thanh toán")
    if _CART_WORDS.search(blob):
        raise_to("cart", "có từ khoá giỏ hàng")
    return level, reasons


def effective_risk(ai_level: str, action_type: str, spec: dict, *, url_role: str = "other", url: str = "", root: dom.Node | None = None) -> str:
    return max_level(ai_level, floor_risk(action_type, spec, url_role=url_role, url=url, root=root)[0])


def approval_check(risk_level: str, confidence: float, *, allow_payment: bool) -> str | None:
    """None nếu được duyệt; ngược lại lý do (tiếng Việt) để hiện cho chủ bot."""
    if risk_level == "payment" and not allow_payment:
        return "Hành động thanh toán chỉ duyệt được sau khi bật công tắc \"cho phép agent thực hiện hành động thanh toán\" của trợ lý."
    need = CONFIDENCE_MIN.get(risk_level, CONFIDENCE_MIN["payment"])
    if confidence is None or confidence < need:
        return f"Độ tin cậy {0 if confidence is None else confidence:.2f} thấp hơn ngưỡng {need:.2f} của nhóm rủi ro này."
    return None
