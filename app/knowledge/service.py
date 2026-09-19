"""Service layer cho blueprint knowledge: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def list_documents(request, **kwargs):
    """Danh sách tài liệu cơ sở tri thức của 1 bot (Bước 2)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("knowledge.list_documents chưa implement")

def upload_document(request, **kwargs):
    """Upload tài liệu mới, lưu qua storage_service, tạo job embedding nền (status=pending)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("knowledge.upload_document chưa implement")

def get_document(request, **kwargs):
    """Chi tiết tài liệu + trạng thái xử lý (pending/processing/trained)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("knowledge.get_document chưa implement")

def list_chunks(request, **kwargs):
    """Danh sách chunk đã cắt của tài liệu (màn hình Chỉnh sửa tài liệu)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("knowledge.list_chunks chưa implement")

def update_chunk(request, **kwargs):
    """Sửa nội dung 1 chunk, tính lại token bằng tokenizer thật trước khi embed lại"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("knowledge.update_chunk chưa implement")

def delete_document(request, **kwargs):
    """Xóa tài liệu + vector liên quan trong ChromaDB"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("knowledge.delete_document chưa implement")
