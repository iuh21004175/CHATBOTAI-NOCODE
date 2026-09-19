"""Service layer cho blueprint profile: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def get_profile(request, **kwargs):
    """Thông tin user hiện tại"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("profile.get_profile chưa implement")

def update_profile(request, **kwargs):
    """Cập nhật hồ sơ user"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("profile.update_profile chưa implement")

def get_team(request, **kwargs):
    """Thông tin team + gói cước hiện tại"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("profile.get_team chưa implement")

def update_team(request, **kwargs):
    """Cập nhật thông tin team"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("profile.update_team chưa implement")
