"""Lớp trừu tượng lưu file — implement mặc định bằng MinIO. Code nghiệp vụ (knowledge,
avatar, xuất báo cáo) chỉ gọi qua đây, không gọi trực tiếp SDK MinIO ở nhiều nơi.
"""
from datetime import timedelta

from extensions import minio_client
from config import Config


def _object_key(team_id: int, bot_id: int, document_id: int, filename: str) -> str:
    return f"{team_id}/{bot_id}/{document_id}/{filename}"


def save_file(team_id: int, bot_id: int, document_id: int, filename: str, file_stream, length: int) -> str:
    """Upload file lên MinIO, trả về object key đã lưu."""
    key = _object_key(team_id, bot_id, document_id, filename)
    minio_client.put_object(Config.MINIO_BUCKET, key, file_stream, length)
    return key


def save_icon(team_id: int, bot_id: int, filename: str, file_stream, length: int) -> str:
    """Upload ảnh icon widget tự tải lên (Bước 3 — Xuất bản). filename PHẢI khác nhau giữa các lần tải
    (vd. có mốc thời gian) để mỗi lần tải là 1 object key mới — nhờ đó URL công khai của icon
    (kèm ?v=<tên file>) tự đổi theo, không dính cache trình duyệt của lần tải trước."""
    key = f"{team_id}/{bot_id}/icon/{filename}"
    minio_client.put_object(Config.MINIO_BUCKET, key, file_stream, length)
    return key


def save_module_zip(team_id: int, bot_id: int, module_id: int, url_id: int, raw: bytes) -> str:
    """Lưu file .zip GỐC (chưa giải nén) do chủ bot tải lên cho 1 URL module (Phase M). Mỗi lần khai báo lại ghi đè cùng khoá (chỉ giữ bản mới nhất)."""
    import io

    key = f"{team_id}/{bot_id}/modules/{module_id}/url-{url_id}.zip"
    minio_client.put_object(Config.MINIO_BUCKET, key, io.BytesIO(raw), len(raw), content_type="application/zip")
    return key


def save_attachment(team_id: int, bot_id: int, attachment_id: int, extension: str, raw: bytes) -> str:
    """Lưu tệp khách gửi trong widget (module "Đọc tài liệu"). Tên object không chứa tên tệp của khách (chỉ id + đuôi đã kiểm) nên không có ký tự lạ/đường dẫn."""
    import io

    key = f"{team_id}/{bot_id}/attachments/{attachment_id}{extension}"
    minio_client.put_object(Config.MINIO_BUCKET, key, io.BytesIO(raw), len(raw))
    return key


def get_file(object_key: str):
    """Đọc file từ MinIO theo object key."""
    return minio_client.get_object(Config.MINIO_BUCKET, object_key)


def get_presigned_url(object_key: str, expires_minutes: int = 15) -> str:
    """URL tạm thời để trình duyệt upload/tải thẳng lên MinIO, giảm tải băng thông qua Flask."""
    return minio_client.presigned_put_object(
        Config.MINIO_BUCKET, object_key, expires=timedelta(minutes=expires_minutes)
    )


def delete_file(object_key: str) -> None:
    minio_client.remove_object(Config.MINIO_BUCKET, object_key)
