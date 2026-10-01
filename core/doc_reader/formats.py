"""Định dạng tệp module "Đọc tài liệu" nhận và cách kiểm tra chữ ký (magic bytes) — không tin phần mở rộng tên tệp do khách gửi.

Ba nhánh đọc (xem extract.py):
- TEXT: .txt/.md/.markdown/.csv — giải mã trực tiếp, miễn phí, không qua markitdown/vision.
- DOCUMENT: .pdf/.docx/.pptx/.xlsx — đọc bằng markitdown (core/doc_reader/markitdown_reader.py), miễn phí. markitdown chỉ trích chữ CÓ SẴN
  trong tệp (text layer), không tự OCR; PDF dạng bản scan (không có text layer) được markitdown_reader.py tự chuyển sang đọc bằng DeepSeek
  vision (core/doc_reader/vision_reader.py — TỐN AI Credit, xem app/credits/service.py:settle_attachment_vision).
- IMAGE: ảnh — markitdown không đọc được chữ trong ảnh (chỉ cho metadata EXIF) nên đi thẳng qua DeepSeek vision (TỐN AI Credit).
"""
from __future__ import annotations

import os

TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".csv")
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".pptx", ".xlsx")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")
ALL_EXTENSIONS = TEXT_EXTENSIONS + DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS

_ZIP = (b"PK\x03\x04",)
# Chữ ký đầu tệp theo phần mở rộng; ".webp" kiểm thêm "WEBP" ở byte 8-12 trong has_valid_signature().
_SIGNATURES = {
    ".pdf": (b"%PDF",),
    ".docx": _ZIP, ".pptx": _ZIP, ".xlsx": _ZIP,
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".bmp": (b"BM",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".tif": (b"II*\x00", b"MM\x00*"), ".tiff": (b"II*\x00", b"MM\x00*"),
}


def extension_of(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def is_supported(filename: str) -> bool:
    return extension_of(filename) in ALL_EXTENSIONS


def is_document(filename: str) -> bool:
    return extension_of(filename) in DOCUMENT_EXTENSIONS


def is_image(filename: str) -> bool:
    return extension_of(filename) in IMAGE_EXTENSIONS


def needs_heavy_processing(filename: str) -> bool:
    """True cho mọi định dạng KHÔNG đọc trực tiếp (document qua markitdown, ảnh qua OCR) — dùng để xếp hàng (app/attachments/service.py), vì
    cả hai đều có thể tốn CPU/thời gian hơn hẳn so với giải mã văn bản thuần."""
    return is_document(filename) or is_image(filename)


def has_valid_signature(filename: str, raw: bytes) -> bool:
    """Nội dung tệp phải đúng loại theo phần mở rộng (chặn đổi đuôi .exe -> .pdf). Tệp văn bản: không chứa byte NUL (dấu hiệu tệp nhị phân)."""
    ext = extension_of(filename)
    if ext in TEXT_EXTENSIONS:
        return b"\x00" not in raw[:8192]
    signatures = _SIGNATURES.get(ext)
    if not signatures or not raw.startswith(signatures):
        return False
    if ext == ".webp":
        return raw[8:12] == b"WEBP"
    return True
