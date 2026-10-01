"""Quét tĩnh code do LLM sinh (AB0, bước 3 của pipeline trong định hướng): bắt các mẫu nguy hiểm TRƯỚC khi lưu/chạy.

Đây là lớp phòng thủ PHỤ, mang tính heuristic (regex, không phải trình phân tích JS đầy đủ): lớp chính là backend + RBAC cố định ở server (code sinh ra
không bao giờ nối thẳng DB) và CSP của trang (xem routes.py). Quét sai thiên về CHẶN NHẦM hơn là bỏ sót — LLM được yêu cầu sửa lại theo từng lỗi.
"""
from __future__ import annotations

import re

# (regex, thông báo cho LLM). Thông báo nói rõ cách làm ĐÚNG để vòng tự sửa có hướng.
_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\beval\s*\("), "cấm eval()"),
    (re.compile(r"\bnew\s+Function\b|\bFunction\s*\("), "cấm new Function()"),
    (re.compile(r"\bdocument\s*\.\s*write"), "cấm document.write"),
    (re.compile(r"\.\s*(innerHTML|outerHTML)\b|\binsertAdjacentHTML\b"), "cấm innerHTML/outerHTML/insertAdjacentHTML: dùng $('<tag>', {text: ...}) hoặc .text()"),
    (re.compile(r"\.\s*html\s*\("), "cấm .html(): dùng .text() hoặc UI.escape() — dữ liệu người dùng chèn bằng .html() gây XSS"),
    (re.compile(r"\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|\bdocument\s*\.\s*cookie\b"), "cấm truy cập localStorage/sessionStorage/cookie: mọi dữ liệu đi qua PF.records"),
    (re.compile(r"\bXMLHttpRequest\b|\bfetch\s*\(|\bWebSocket\b|\bEventSource\b|\bsendBeacon\b|\$\s*\.\s*(ajax|get|post|getJSON|getScript)\b|\.\s*load\s*\("),
     "cấm tự gọi mạng: chỉ dùng PF.records.* của platform-sdk"),
    (re.compile(r"https?://|//[a-z0-9-]+\.[a-z]{2,}", re.I), "cấm URL tuyệt đối/domain ngoài"),
    (re.compile(r"\bjavascript\s*:", re.I), "cấm URL javascript:"),
    (re.compile(r"\bwindow\s*\.\s*open\b|\bpostMessage\b|\bdocument\s*\.\s*domain\b|\b(top|parent)\s*\.\s*(location|document)\b|\bimportScripts\b|\bimport\s*\("),
     "cấm window.open/postMessage/top/parent/import()"),
    (re.compile(r"<\s*(script|iframe|object|embed|link|style|base|meta)\b", re.I), "cấm tạo thẻ script/iframe/object/embed/link/style/base/meta"),
    (re.compile(r"\bon[a-z]+\s*=\s*['\"]", re.I), "cấm thuộc tính sự kiện inline (onclick=...): gắn bằng .on('click', fn)"),
    (re.compile(r"\$\s*\(\s*(['\"`])\s*<[^)\n]*?(\1\s*\+|\+\s*\1|\$\{)"), "cấm dựng HTML bằng nối chuỗi trong $(): dùng $('<tag>').text(...)"),
    (re.compile(r"\.\s*(append|prepend|after|before|appendTo|prependTo|replaceWith|wrap|wrapAll|wrapInner)\s*\(\s*[^)\n]*(['\"`]\s*\+|\+\s*['\"`]|\$\{)"),
     "cấm chèn chuỗi HTML ghép từ dữ liệu vào .append/.prepend/...: chèn phần tử jQuery đã tạo bằng .text()"),
    (re.compile(r"\.\s*attr\s*\(\s*['\"](on[a-z]+|style|srcdoc)['\"]", re.I), "cấm đặt thuộc tính on*/style/srcdoc bằng .attr()"),
]
_COLLECTION_REFS = [
    re.compile(r"\bcollection\s*:\s*['\"]([A-Za-z0-9_]+)['\"]"),
    re.compile(r"\bPF\s*\.\s*records\s*\.\s*\w+\s*\(\s*['\"]([A-Za-z0-9_]+)['\"]"),
]


def scan_js(path: str, source: str, *, collections: set[str]) -> list[str]:
    """Danh sách lỗi dạng "path:dòng: nội dung" (rỗng = sạch). collections: tên collection có thật trong Spec — tham chiếu bịa bị coi là lỗi."""
    problems: list[str] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
            continue  # dòng chú thích thuần — không phải code chạy
        for pattern, message in _RULES:
            if pattern.search(line):
                problems.append(f"{path}:{lineno}: {message}")
    for pattern in _COLLECTION_REFS:
        for match in pattern.finditer(source):
            if match.group(1) not in collections:
                lineno = source.count("\n", 0, match.start()) + 1
                problems.append(f'{path}:{lineno}: collection "{match.group(1)}" không có trong Spec (có: {", ".join(sorted(collections))})')
    if not re.search(r"\bUI\s*\.\s*\w+|\bPF\s*\.\s*records", source):
        problems.append(f"{path}: không dùng UI Kit (UI.*) hay PF.records — màn hình phải ráp từ thành phần của nền tảng")
    return problems
