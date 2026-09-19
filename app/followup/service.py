"""Service layer cho blueprint followup: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def list_followups(request, **kwargs):
    """Danh sách kịch bản FollowUp tự động"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("followup.list_followups chưa implement")

def create_followup(request, **kwargs):
    """Tạo kịch bản FollowUp mới (nội dung, lịch gửi)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("followup.create_followup chưa implement")

def update_followup(request, **kwargs):
    """Cập nhật kịch bản FollowUp"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("followup.update_followup chưa implement")

def delete_followup(request, **kwargs):
    """Xóa kịch bản FollowUp"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("followup.delete_followup chưa implement")
