"""Đọc tệp văn bản thuần (md/txt/csv) — không cần markitdown/OCR."""
from __future__ import annotations

import csv
import io

from core.doc_reader.formats import extension_of


def _decode(raw: bytes) -> str:
    """UTF-8 (có/không BOM) trước; tệp Windows cũ (Excel xuất CSV) thường là UTF-16 (có BOM) hoặc cp1258/cp1252 -> thử lần lượt, không bao giờ làm mất chữ âm thầm."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1258", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_text(filename: str, raw: bytes) -> str:
    text = _decode(raw)
    if extension_of(filename) != ".csv":
        return text
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]  # dòng thiếu ô -> bảng markdown vẫn đủ cột
    header, body = rows[0], rows[1:]
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)
