"""Service layer cho blueprint bots: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def dashboard(request, **kwargs):
    """Tổng quan Bảng điều khiển: số bot, trạng thái 4 bước, số tài liệu/hội thoại"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.dashboard chưa implement")

def list_bots(request, **kwargs):
    """Danh sách bot thuộc team đang đăng nhập"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.list_bots chưa implement")

def create_bot(request, **kwargs):
    """Tạo bot mới cho team"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.create_bot chưa implement")

def get_bot(request, **kwargs):
    """Chi tiết 1 bot"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.get_bot chưa implement")

def update_bot(request, **kwargs):
    """Cập nhật thông tin bot"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.update_bot chưa implement")

def delete_bot(request, **kwargs):
    """Xóa bot"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.delete_bot chưa implement")

def get_bot_settings(request, **kwargs):
    """Lấy cấu hình hành vi (Bước 1 — Thiết lập): lời chào, hướng dẫn, ngôn ngữ, temperature"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.get_bot_settings chưa implement")

def update_bot_settings(request, **kwargs):
    """Cập nhật cấu hình hành vi bot"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("bots.update_bot_settings chưa implement")
