"""Service layer cho blueprint customers: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def list_customers(request, **kwargs):
    """Danh sách khách hàng thu thập được từ hội thoại (CRM cơ bản)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("customers.list_customers chưa implement")

def get_customer(request, **kwargs):
    """Chi tiết khách hàng + lịch sử hội thoại liên quan"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("customers.get_customer chưa implement")

def update_customer(request, **kwargs):
    """Cập nhật thông tin khách hàng"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("customers.update_customer chưa implement")
