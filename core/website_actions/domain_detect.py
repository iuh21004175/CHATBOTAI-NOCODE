"""Tự động đoán domain của 1 trang từ chính nội dung file HTML đã tải lên (M2 vá, bước 3b của worker) — vì giờ không còn crawl URL thật nên
không còn biết domain "miễn phí" như trước; hệ thống phải tự đọc HTML để GỢI Ý, người dùng luôn phải xác nhận/sửa ở bước sau (xem app/modules/service.py:
confirm_domain). Giá trị đoán được CHỈ ghi vào module_urls.detected_domain — KHÔNG bao giờ dùng trực tiếp để so khớp bảo mật Origin (đó là
module_urls.source_url, chỉ có giá trị sau khi người dùng xác nhận).

CHỈ thư viện chuẩn (html.parser). Thứ tự ưu tiên (dừng ở bước đầu tiên có kết quả):
1. <base href="...">
2. <link rel="canonical" href="...">
3. <meta property="og:url" content="...">
4. (yếu nhất) domain xuất hiện nhiều nhất trong các URL TUYỆT ĐỐI (http/https) của <a href>/<img src> trong trang.
Không có gì -> trả None (không đoán bừa).
"""
from __future__ import annotations

from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlparse


class _MetaScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.canonical_href: str | None = None
        self.og_url: str | None = None
        self.link_hosts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {name: (value or "") for name, value in attrs}
        if tag == "base" and self.base_href is None and values.get("href"):
            self.base_href = values["href"]
        elif tag == "link" and values.get("rel", "").strip().lower() == "canonical" and self.canonical_href is None and values.get("href"):
            self.canonical_href = values["href"]
        elif tag == "meta" and values.get("property", "").strip().lower() == "og:url" and self.og_url is None and values.get("content"):
            self.og_url = values["content"]
        elif tag == "a" and values.get("href"):
            self._note_link(values["href"])
        elif tag == "img" and values.get("src"):
            self._note_link(values["src"])

    def _note_link(self, value: str) -> None:
        host = _absolute_host(value)
        if host:
            self.link_hosts.append(host)


def _absolute_host(value: str | None) -> str | None:
    """Hostname đã chuẩn hoá nếu `value` là URL TUYỆT ĐỐI http/https (hoặc protocol-relative "//host/..."); None nếu là đường dẫn tương đối."""
    if not value:
        return None
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return None
    if not parsed.hostname or parsed.scheme not in ("http", "https", ""):
        return None
    return parsed.hostname.lower().removeprefix("www.")


def detect(html: str) -> str | None:
    """Domain đoán được từ nội dung `html` (đã chuẩn hoá chữ thường, bỏ "www."), hoặc None nếu không đoán được gì."""
    scanner = _MetaScanner()
    try:
        scanner.feed(html or "")
        scanner.close()
    except Exception:
        return None
    for raw in (scanner.base_href, scanner.canonical_href, scanner.og_url):
        host = _absolute_host(raw)
        if host:
            return host
    if scanner.link_hosts:
        return Counter(scanner.link_hosts).most_common(1)[0][0]
    return None
