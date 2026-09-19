"""Service layer cho blueprint reports: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def overview(request, **kwargs):
    """Số liệu thống kê tổng hợp (hội thoại, khách hàng, tài liệu theo thời gian)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("reports.overview chưa implement")

def export(request, **kwargs):
    """Xuất báo cáo (chạy nền nếu dữ liệu lớn)"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("reports.export chưa implement")
