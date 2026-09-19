"""Service layer cho blueprint inbox: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""



def list_conversations(request, **kwargs):
    """Danh sách hội thoại (dùng chung cho Inbox và Lịch sử chat), lọc theo kênh/trạng thái/thời gian"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("inbox.list_conversations chưa implement")

def get_conversation(request, **kwargs):
    """Chi tiết 1 hội thoại kèm tin nhắn"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("inbox.get_conversation chưa implement")

def reply_message(request, **kwargs):
    """Nhân viên gửi tin nhắn trả lời thủ công, phát realtime qua Flask-SocketIO"""
    # TODO: implement — nhớ lọc theo team_id đang đăng nhập (nguyên tắc multi-tenant)
    raise NotImplementedError("inbox.reply_message chưa implement")
