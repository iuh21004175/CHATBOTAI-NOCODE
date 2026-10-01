"""Quy tắc domain được phép nhúng widget — module lá (không import service nào) để cả app/widget/service.py (kiểm tra request
công khai) và app/dashboard/service.py (lưu danh sách ở Bước 3) dùng chung 1 định nghĩa, không lệch nhau."""
import re
from collections.abc import Iterable
from urllib.parse import urlparse

MAX_DOMAINS_PER_BOT = 20
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")


def normalize_domain(domain: str | None) -> str:
    """'https://www.Shop.vn/abc' / 'shop.vn:443' -> 'shop.vn'"""
    value = (domain or "").strip().lower()
    if "://" in value:
        value = urlparse(value).hostname or ""
    value = value.split("/")[0].split(":")[0]
    return value.removeprefix("www.")


def is_valid_domain(domain: str) -> bool:
    """Domain đã chuẩn hóa phải là hostname hợp lệ (vd 'shopabc.vn', 'localhost'); chuỗi trống/chứa khoảng trắng/ký tự lạ bị loại."""
    return bool(_HOSTNAME_RE.match(domain))


def origin_allowed(origin: str | None, allowed_domains: Iterable[str]) -> bool:
    """Origin của trang đang nhúng widget phải trùng MỘT trong các domain đã khai báo (hoặc là subdomain của nó).
    Trình duyệt luôn gửi Origin với request cross-origin nên chặn được nhúng trái phép ở website khác.
    Danh sách trống -> không domain nào được phép."""
    host = normalize_domain(origin) if origin and origin != "null" else ""
    if not host:
        return False
    for domain in allowed_domains:
        allowed = normalize_domain(domain)
        if allowed and (host == allowed or host.endswith("." + allowed)):
            return True
    return False
