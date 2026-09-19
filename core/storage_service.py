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
