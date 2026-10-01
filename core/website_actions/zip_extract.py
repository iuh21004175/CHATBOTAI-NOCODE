"""Giải nén an toàn file .zip do chủ bot tải lên (M2 vá — thay cho crawl HTTP): trang web đã lưu bằng "Webpage, Complete" của
trình duyệt (1 file .html cấp gốc + thư mục tài nguyên đi kèm), nén lại thành .zip rồi tải lên thay vì khai URL để hệ thống tự crawl.

CHỈ thư viện chuẩn (zipfile/tempfile/pathlib). Đây là bề mặt tấn công thật (file do khách hàng của chủ bot tải lên qua giao diện quản trị), 4 lớp
phòng thủ BẮT BUỘC theo đúng đặc tả:
1. Chống zip bomb: đọc tổng kích thước ĐÃ GIẢI NÉN từ ZipInfo.file_size (có sẵn trong central directory, không cần giải nén) TRƯỚC khi ghi bất kỳ
   file nào ra đĩa — vượt trần thì từ chối ngay.
2. Chống zip slip (path traversal): mọi file được ghi vào 1 thư mục tạm; trước khi ghi, đường dẫn đích được `resolve()` và phải nằm bên trong đúng
   thư mục tạm đó — entry chứa "../" hoặc đường dẫn tuyệt đối sẽ resolve ra ngoài và bị chặn.
3. Allowlist phần mở rộng: file ngoài danh sách bị BỎ QUA (không ghi), không làm hỏng cả lượt tải lên chỉ vì 1 file thừa (vd "Thumbs.db").
4. Giới hạn số lượng file trong zip.

Sau khi dùng xong (đọc nội dung file HTML chính), gọi cleanup() để xoá thư mục tạm — dùng try/finally, không để lại rác trên đĩa.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import tempfile
import zipfile
from dataclasses import dataclass

ALLOWED_EXTENSIONS = frozenset({
    ".html", ".htm", ".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".woff", ".woff2", ".ttf", ".ico",
})
HTML_EXTENSIONS = frozenset({".html", ".htm"})


class ZipExtractError(Exception):
    """File .zip không hợp lệ hoặc vi phạm giới hạn an toàn (thông điệp tiếng Việt, hiển thị cho chủ bot)."""


@dataclass
class ExtractedPage:
    entry_filename: str   # tên file .html chính (tương đối, ở cấp gốc của zip)
    html: str              # nội dung file đó, đã decode
    skipped: list[str]     # tên các file trong zip bị bỏ qua (ngoài allowlist) — chỉ để ghi log, không phải lỗi


def _open_zip(raw: bytes) -> zipfile.ZipFile:
    import io

    try:
        return zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ZipExtractError("Tệp không phải file .zip hợp lệ hoặc đã bị hỏng.") from exc


def _safe_target(base: pathlib.Path, member_name: str) -> pathlib.Path | None:
    """Đường dẫn đích đã resolve, hoặc None nếu entry cố thoát khỏi thư mục đích (zip slip)."""
    target = (base / member_name).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


def safe_extract(raw: bytes, *, max_files: int, max_uncompressed_bytes: int) -> tuple[pathlib.Path, list[str]]:
    """Giải nén `raw` (nội dung .zip) vào 1 thư mục tạm mới, đã qua đủ 4 lớp kiểm tra an toàn. Trả (thư mục tạm, tên file bị bỏ qua).
    Gọi cleanup(thư_mục_tạm) khi dùng xong. Ném ZipExtractError nếu vi phạm bất kỳ giới hạn nào."""
    zf = _open_zip(raw)
    try:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > max_files:
            raise ZipExtractError(f"File .zip có quá nhiều file bên trong (tối đa {max_files}).")
        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > max_uncompressed_bytes:
            raise ZipExtractError(
                f"Nội dung sau khi giải nén quá lớn (tối đa {max_uncompressed_bytes // (1024 * 1024)} MB) — có thể file .zip có vấn đề."
            )

        base = pathlib.Path(tempfile.mkdtemp(prefix="module-zip-")).resolve()
        skipped: list[str] = []
        try:
            for info in infos:
                # Entry tuyệt đối (vd "/etc/passwd") hoặc có ổ đĩa Windows (vd "C:\\...") không phải đường dẫn tương đối hợp lệ trong zip
                if os.path.isabs(info.filename) or (len(info.filename) > 1 and info.filename[1] == ":"):
                    raise ZipExtractError("File .zip chứa đường dẫn không hợp lệ.")
                target = _safe_target(base, info.filename)
                if target is None:
                    raise ZipExtractError("File .zip chứa đường dẫn cố thoát khỏi thư mục giải nén (không an toàn).")
                ext = pathlib.Path(info.filename).suffix.lower()
                if ext not in ALLOWED_EXTENSIONS:
                    skipped.append(info.filename)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
        except Exception:
            cleanup(base)
            raise
        return base, skipped
    finally:
        zf.close()


def cleanup(base_dir: pathlib.Path) -> None:
    shutil.rmtree(base_dir, ignore_errors=True)


def find_root_html(base_dir: pathlib.Path) -> str:
    """Tên file .html/.htm DUY NHẤT ở cấp gốc (không tính thư mục con — đúng cách trình duyệt lưu "Webpage, Complete" mặc định).
    0 hoặc >1 file -> từ chối rõ ràng, KHÔNG tự đoán file nào là chính."""
    roots = sorted(p.name for p in base_dir.iterdir() if p.is_file() and p.suffix.lower() in HTML_EXTENSIONS)
    if len(roots) != 1:
        found = f"tìm thấy {len(roots)}" if roots else "không tìm thấy file nào"
        raise ZipExtractError(
            f"Cần đúng 1 file .html hoặc .htm nằm ở cấp gốc của file .zip ({found}). "
            "Hãy lưu lại trang bằng \"Trang web, Hoàn chỉnh\" (Webpage, Complete) rồi nén và tải lên lại."
        )
    return roots[0]


def extract_entry_html(raw: bytes, *, max_files: int, max_uncompressed_bytes: int) -> ExtractedPage:
    """Giải nén + xác định + đọc file HTML chính trong 1 lần gọi (dùng cho cả validate lúc tải lên lẫn worker phân tích).
    Luôn dọn thư mục tạm trước khi trả kết quả — không để lại rác trên đĩa dù thành công hay lỗi."""
    base, skipped = safe_extract(raw, max_files=max_files, max_uncompressed_bytes=max_uncompressed_bytes)
    try:
        entry = find_root_html(base)
        html = (base / entry).read_text(encoding="utf-8", errors="replace")
        return ExtractedPage(entry_filename=entry, html=html, skipped=skipped)
    finally:
        cleanup(base)
