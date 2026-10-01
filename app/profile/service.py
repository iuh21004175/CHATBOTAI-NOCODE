"""Service layer cho blueprint profile: chứa logic nghiệp vụ thật (query DB theo team_id,
gọi core/rag_engine, core/storage_service, ...). routes.py chỉ gọi xuống đây.
"""
from app.credits import service as credits_service
from app.models import Team
from app.payments import service as payments_service
from config import Config
from extensions import db

CREDIT_TYPE_LABELS = {"execution_charge": "Sử dụng trợ lý AI", "module_analysis_charge": "Phân tích module website", "trial_grant": "Credit dùng thử", "topup": "Nạp Credit"}
ORDER_STATUS_LABELS = {"pending": "Chờ thanh toán", "paid": "Đã thanh toán", "cancelled": "Đã hủy", "expired": "Hết hạn", "failed": "Lỗi tạo đơn"}
# Gói nạp theo bảng giá dịch vụ: 3 gói cố định + gói "1.000.000+" (khách nhập số tiền)
TOPUP_PLANS = (("Cá nhân", 50_000), ("Kinh doanh hộ gia đình", 200_000), ("Doanh nghiệp", 500_000))
RECENT_TRANSACTIONS = 20


def credit_overview(team_id: int) -> dict | None:
    """Dữ liệu trang hồ sơ: team + số dư AI Credit + lịch sử gần đây. CHỈ số tiền VND: không token, không giá vốn LLM; và không hiện các dòng
    reserve/release (giữ chỗ/hoàn giữ chỗ) để số dư trong mắt khách không nhấp nháy tăng giảm."""
    team = db.session.get(Team, team_id)
    if team is None:
        return None
    payments_service.sync_pending_orders(team_id)  # đơn đã trả tiền mà webhook/return chưa tới: cộng Credit trước khi đọc số dư
    balance = credits_service.get_balance(team_id)
    db.session.commit()  # get_balance có thể vừa tạo tài khoản + Credit dùng thử cho team cũ
    return {
        "team": team,
        "balance_vnd": balance,
        "transactions": credits_service.visible_transactions(team_id, RECENT_TRANSACTIONS),
        "type_labels": CREDIT_TYPE_LABELS,
        "topup_plans": [(label, amount) for label, amount in TOPUP_PLANS if amount in Config.TOPUP_PACKAGES_VND],
        "popular_amount": 200_000,
        "custom_min": Config.TOPUP_CUSTOM_MIN_VND,
        "custom_step": Config.TOPUP_STEP_VND,
        "payments_configured": payments_service.is_configured(),
        "orders": payments_service.recent_orders(team_id),
        "order_labels": ORDER_STATUS_LABELS,
    }


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
