"""Route layer (API) cho blueprint knowledge: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.
"""
from flask import Blueprint, jsonify, request, abort
from flask_login import login_required

from . import service

bp = Blueprint("knowledge", __name__, url_prefix="/api/knowledge")


@bp.route("/bots/<int:bot_id>/documents", methods=["GET"])
@login_required
def list_documents(**kwargs):
    """Danh sách tài liệu cơ sở tri thức của 1 bot (Bước 2)"""
    # TODO: implement — gọi service.list_documents(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_documents(request, **kwargs))

@bp.route("/bots/<int:bot_id>/documents", methods=["POST"])
@login_required
def upload_document(**kwargs):
    """Upload tài liệu mới, lưu qua storage_service, tạo job embedding nền (status=pending)"""
    # TODO: implement — gọi service.upload_document(...) khi model/DB đã sẵn sàng
    return jsonify(service.upload_document(request, **kwargs))

@bp.route("/documents/<int:document_id>", methods=["GET"])
@login_required
def get_document(**kwargs):
    """Chi tiết tài liệu + trạng thái xử lý (pending/processing/trained)"""
    # TODO: implement — gọi service.get_document(...) khi model/DB đã sẵn sàng
    return jsonify(service.get_document(request, **kwargs))

@bp.route("/documents/<int:document_id>/chunks", methods=["GET"])
@login_required
def list_chunks(**kwargs):
    """Danh sách chunk đã cắt của tài liệu (màn hình Chỉnh sửa tài liệu)"""
    # TODO: implement — gọi service.list_chunks(...) khi model/DB đã sẵn sàng
    return jsonify(service.list_chunks(request, **kwargs))

@bp.route("/documents/<int:document_id>/chunks/<int:chunk_id>", methods=["PUT"])
@login_required
def update_chunk(**kwargs):
    """Sửa nội dung 1 chunk, tính lại token bằng tokenizer thật trước khi embed lại"""
    # TODO: implement — gọi service.update_chunk(...) khi model/DB đã sẵn sàng
    return jsonify(service.update_chunk(request, **kwargs))

@bp.route("/documents/<int:document_id>", methods=["DELETE"])
@login_required
def delete_document(**kwargs):
    """Xóa tài liệu + vector liên quan trong ChromaDB"""
    # TODO: implement — gọi service.delete_document(...) khi model/DB đã sẵn sàng
    return jsonify(service.delete_document(request, **kwargs))
