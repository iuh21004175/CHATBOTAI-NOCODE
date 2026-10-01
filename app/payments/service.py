"""Nạp AI Credit qua payOS: tạo đơn + link thanh toán, nhận kết quả (webhook / quay lại từ trang thanh toán) và cộng Credit ĐÚNG 1 LẦN mỗi đơn.

Nguyên tắc an toàn:
- Không tin gì từ trình duyệt: trang "quay lại" chỉ mang orderCode, trạng thái luôn được hỏi lại payOS bằng khóa của server (get_payment).
- Webhook phải có chữ ký HMAC hợp lệ (core/payos_client.verify_webhook); số tiền payOS báo phải KHỚP số tiền của đơn mới cộng Credit.
- Cộng Credit idempotent: khóa dòng đơn (SELECT ... FOR UPDATE) + kiểm tra trạng thái trong cùng transaction với ghi sổ cái — webhook gửi lặp,
  webhook và trang quay lại chạy đồng thời đều chỉ cộng 1 lần.
- Đơn thanh toán muộn (đã hết hạn/hủy ở phía ta nhưng payOS báo đã trả) vẫn được cộng Credit: tiền đã thu thì khách phải nhận Credit.
"""
from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.credits import service as credits_service
from app.models import PaymentOrder
from config import Config
from core import payos_client as payos
from extensions import db

logger = logging.getLogger(__name__)

DESCRIPTION = "AICREDIT"  # payOS giới hạn 9 ký tự với tài khoản ngân hàng chưa liên kết payOS
PAID_STATUS = "PAID"
FINAL_REMOTE_STATUSES = {"CANCELLED": "cancelled", "EXPIRED": "expired"}


def payos_config() -> payos.PayOSConfig:
    return payos.PayOSConfig(Config.PAYOS_CLIENT_ID, Config.PAYOS_API_KEY, Config.PAYOS_CHECKSUM_KEY, Config.PAYOS_API_BASE)


def is_configured() -> bool:
    return payos_config().configured


def validate_amount(amount) -> str | None:
    """Số tiền nạp hợp lệ: đúng 1 trong các gói, hoặc (gói "1.000.000+") ≥ mức tối thiểu, bội số của bước, ≤ trần. Trả thông báo lỗi hoặc None."""
    if isinstance(amount, bool) or not isinstance(amount, int):
        return "Số tiền không hợp lệ."
    if amount in Config.TOPUP_PACKAGES_VND:
        return None
    if amount < Config.TOPUP_CUSTOM_MIN_VND:
        return f"Số tiền tự nhập tối thiểu {Config.TOPUP_CUSTOM_MIN_VND:,}đ.".replace(",", ".")
    if amount > Config.TOPUP_MAX_VND:
        return f"Số tiền tối đa mỗi lần nạp là {Config.TOPUP_MAX_VND:,}đ.".replace(",", ".")
    if amount % Config.TOPUP_STEP_VND:
        return f"Số tiền phải là bội số của {Config.TOPUP_STEP_VND:,}đ.".replace(",", ".")
    return None


def parse_amount(raw) -> int | None:
    """Chuỗi từ form ("500000", "1.000.000") -> số nguyên VND; None nếu không phải số nguyên dương."""
    text = str(raw or "").strip().replace(".", "").replace(",", "").replace(" ", "")
    amount = int(text) if text.isdigit() and len(text) <= 12 else 0
    return amount or None


def _new_order_code() -> int:
    """Mã đơn số nguyên (payOS yêu cầu), khó đoán và duy nhất: 12 chữ số, mã thật do ràng buộc unique của DB bảo đảm."""
    return 10**11 + secrets.randbelow(9 * 10**11)


def create_order(team_id: int, user_id: int | None, amount: int, *, return_url: str, cancel_url: str) -> PaymentOrder:
    """Tạo đơn 'pending' rồi xin link thanh toán payOS. Lỗi payOS -> đơn được đánh dấu 'failed' (không mất dấu vết) và PayOSError ném tiếp."""
    error = validate_amount(amount)
    if error:
        raise ValueError(error)
    expires_at = datetime.utcnow() + timedelta(minutes=Config.PAYOS_LINK_EXPIRE_MINUTES)
    order = None
    for _ in range(5):
        try:
            order = PaymentOrder(order_code=_new_order_code(), team_id=team_id, user_id=user_id, amount_vnd=amount,
                                 credit_vnd=Decimal(amount), status="pending", expires_at=expires_at)
            db.session.add(order)
            db.session.commit()
            break
        except IntegrityError:
            db.session.rollback()  # trùng mã đơn (cực hiếm): thử mã khác
            order = None
    if order is None:
        raise payos.PayOSError("Không tạo được mã đơn, vui lòng thử lại.")
    try:
        data = payos.create_payment_link(
            payos_config(), order_code=order.order_code, amount=amount, description=DESCRIPTION, return_url=return_url, cancel_url=cancel_url,
            expired_at=int(time.time() + Config.PAYOS_LINK_EXPIRE_MINUTES * 60),
            items=[{"name": "Nạp AI Credit", "quantity": 1, "price": amount}],
        )
    except payos.PayOSError as exc:
        order.status, order.note = "failed", str(exc)[:255]
        db.session.commit()
        raise
    order.payment_link_id, order.checkout_url = str(data["paymentLinkId"])[:64], str(data["checkoutUrl"])[:500]
    db.session.commit()
    return order


def apply_paid(order_code: int, paid_amount: int | None, *, source: str) -> bool:
    """Ghi nhận đơn ĐÃ thanh toán: cộng Credit + chuyển 'paid' (cùng transaction, đúng 1 lần). True nếu lần gọi này là lần cộng Credit.
    Đơn không tồn tại -> False (không lỗi: payOS gửi cả webhook thử với mã đơn không phải của ta). Số tiền lệch -> KHÔNG cộng, ghi log lỗi."""
    order = PaymentOrder.query.filter_by(order_code=order_code).with_for_update().first()
    if order is None:
        db.session.rollback()
        logger.warning("payos: bỏ qua thanh toán cho orderCode=%s không có trong hệ thống", order_code)
        return False
    if order.status == "paid":
        db.session.rollback()  # nhả khóa: đã cộng Credit từ trước
        return False
    if paid_amount is not None and int(paid_amount) != int(order.amount_vnd):
        order.note = f"Số tiền payOS báo {paid_amount} lệch số tiền đơn {order.amount_vnd} ({source})"[:255]
        db.session.commit()
        logger.error("payos: orderCode=%s %s", order_code, order.note)
        return False
    account = credits_service.lock_account(order.team_id)
    credits_service.add_topup(account, Decimal(order.credit_vnd), note=f"Nạp qua payOS #{order.order_code}")
    order.status, order.paid_at, order.note = "paid", datetime.utcnow(), f"Đã thanh toán ({source})"
    db.session.commit()
    return True


def handle_webhook(payload) -> str:
    """Xử lý webhook payOS. Trả 'credited' | 'ignored' (đã xử lý/đơn lạ/không phải thanh toán thành công). Ném PayOSSignatureError nếu chữ ký sai."""
    data = payos.verify_webhook(payload, Config.PAYOS_CHECKSUM_KEY)
    if payload.get("code") != payos.SUCCESS_CODE or data.get("code") != payos.SUCCESS_CODE:
        return "ignored"
    try:
        order_code, amount = int(data["orderCode"]), int(data["amount"])
    except (KeyError, TypeError, ValueError):
        return "ignored"
    return "credited" if apply_paid(order_code, amount, source="webhook") else "ignored"


def sync_order(order: PaymentOrder) -> str:
    """Hỏi lại payOS trạng thái đơn (khi khách quay lại từ trang thanh toán) và cập nhật. Trả trạng thái đơn sau đồng bộ."""
    if order.status != "pending":
        return order.status
    data = payos.get_payment(payos_config(), order.order_code)
    remote = str(data.get("status") or "").upper()
    if remote == PAID_STATUS:
        apply_paid(order.order_code, int(data.get("amountPaid") or data.get("amount") or order.amount_vnd), source="return")
    elif remote in FINAL_REMOTE_STATUSES:
        locked = PaymentOrder.query.filter_by(id=order.id).with_for_update().one()
        if locked.status == "pending":
            locked.status = FINAL_REMOTE_STATUSES[remote]
        db.session.commit()
    db.session.refresh(order)
    return order.status


def sync_pending_orders(team_id: int) -> None:
    """Đối soát các đơn gần đây còn 'pending' với payOS (hỏi lại trạng thái bằng khóa của server). Đường dự phòng khi webhook chưa đăng ký/không tới
    được và khách không quay lại đúng URL return sau khi trả tiền: đơn đã thu tiền vẫn được cộng Credit (apply_paid đúng 1 lần), đơn hủy/hết hạn
    được đóng. Lỗi payOS chỉ ghi log rồi để lần đối soát sau (không làm hỏng trang đang xem, không đổi trạng thái đơn)."""
    if not is_configured():
        return
    for order in recent_orders(team_id):
        if order.status != "pending":
            continue
        try:
            sync_order(order)
        except payos.PayOSError as exc:
            logger.warning("payos: đối soát đơn %s lỗi: %s", order.order_code, exc)


def order_for_team(team_id: int, order_code: int) -> PaymentOrder | None:
    return PaymentOrder.query.filter_by(order_code=order_code, team_id=team_id).first()


def recent_orders(team_id: int, limit: int = 5) -> list[PaymentOrder]:
    return PaymentOrder.query.filter_by(team_id=team_id).order_by(PaymentOrder.id.desc()).limit(limit).all()
