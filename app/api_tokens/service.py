"""Service layer cho blueprint api_tokens: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def list_tokens(request, **kwargs):
    """Danh sách API Token của team (không trả token_hash gốc)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("api_tokens.list_tokens chưa implement")

def create_token(request, **kwargs):
    """Tạo API Token mới, trả token plaintext đúng 1 lần"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("api_tokens.create_token chưa implement")

def update_token(request, **kwargs):
    """Cập nhật scope/hạn sử dụng của token"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("api_tokens.update_token chưa implement")

def revoke_token(request, **kwargs):
    """Thu hồi (xóa) token"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("api_tokens.revoke_token chưa implement")
