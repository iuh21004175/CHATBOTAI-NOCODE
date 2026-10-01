"""Credit Engine (Phase D): sổ cái AI Credit của team + giữ chỗ (reserve) / quyết toán mỗi lượt chạy agent.

Nguyên tắc:
- Đơn vị là VND ("AI Credit"); khách không bao giờ thấy token.
- credit_transactions là sổ cái CHỈ THÊM (models.py chặn sửa/xóa); mỗi dòng ghi số dư SAU giao dịch. Mọi thay đổi số dư đi qua đây và luôn giữ
  bất biến: credit_accounts.balance_vnd = tổng amount_vnd của các giao dịch của team.
- Số dư KHÔNG BAO GIỜ âm: khóa dòng tài khoản (SELECT ... FOR UPDATE) trước mỗi thay đổi; giữ chỗ chỉ thành công khi đủ số dư.
- Vòng đời 1 lượt: reserve (-R, ghi + commit NGAY để các lượt song song thấy số dư đã bị giữ) -> chạy agent -> settle (release +R rồi
  execution_charge -thực_tế, CÙNG transaction với execution_costs và tin bot) hoặc release_unused (lượt không chạy được: hoàn đủ R).
  Hai dòng release/charge thay vì 1 dòng chênh lệch để giao diện khách (ẩn reserve/release) luôn thấy đúng số đã trừ của từng lượt.
- Chi phí thực vượt cả phần giữ chỗ lẫn số dư còn lại: chỉ thu tối đa số dư (không âm), phần không thu ghi vào execution_costs.uncollected_vnd
  + log cảnh báo; lượt SAU của team đó bị chặn cho tới khi có Credit (số dư không đủ để giữ chỗ). Không sửa lại lượt đã xảy ra.
- Chưa có tích hợp thanh toán: chưa có luồng topup.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from app.models import CreditAccount, CreditTransaction, ExecutionCost
from config import Config
from core.context_engine import execution_cost as xc
from extensions import db

logger = logging.getLogger(__name__)

VISIBLE_TYPES = ("execution_charge", "module_analysis_charge", "attachment_vision_charge", "trial_grant", "topup")  # giao diện khách: không hiện reserve/release (số dư "nhấp nháy")
BLOCK_INSUFFICIENT = "insufficient_credit"
BLOCK_MAX_COST = "max_cost_exceeded"


@dataclass(frozen=True)
class Reservation:
    team_id: int
    amount_vnd: Decimal


@dataclass(frozen=True)
class Blocked:
    reason: str            # BLOCK_INSUFFICIENT | BLOCK_MAX_COST
    estimate_vnd: Decimal
    balance_vnd: Decimal | None = None
    limit_vnd: Decimal | None = None


def _add_transaction(account: CreditAccount, type_: str, amount: Decimal, *, execution_id: int | None = None, note: str | None = None) -> None:
    """Áp `amount` vào số dư rồi ghi 1 dòng sổ cái. Người gọi đã khóa account."""
    account.balance_vnd = xc.money(Decimal(account.balance_vnd) + amount)
    db.session.add(CreditTransaction(
        team_id=account.team_id, execution_id=execution_id, type=type_, amount_vnd=xc.money(amount),
        balance_after_vnd=account.balance_vnd, note=note,
    ))


def ensure_account(team_id: int) -> CreditAccount:
    """Tài khoản Credit của team; CHƯA có thì tạo kèm Credit dùng thử (TRIAL_CREDIT_VND) — đúng 1 lần cho mỗi team (team_id là duy nhất; 2 lần
    tạo đồng thời: bên thua đọc lại tài khoản bên thắng, không cấp lần 2). Không commit: người gọi commit."""
    account = CreditAccount.query.filter_by(team_id=team_id).first()
    if account is not None:
        return account
    try:
        with db.session.begin_nested():
            account = CreditAccount(team_id=team_id, balance_vnd=0)
            db.session.add(account)
            db.session.flush()
            _add_transaction(account, "trial_grant", xc.money(Config.TRIAL_CREDIT_VND), note="Credit dùng thử")
    except IntegrityError:
        account = CreditAccount.query.filter_by(team_id=team_id).one()
    return account


def _locked_account(team_id: int) -> CreditAccount:
    ensure_account(team_id)
    return CreditAccount.query.filter_by(team_id=team_id).with_for_update().one()


def lock_account(team_id: int) -> CreditAccount:
    """Tài khoản Credit đã khóa dòng (tạo kèm Credit dùng thử nếu chưa có) — cho các module khác cần thay đổi số dư (nạp tiền)."""
    return _locked_account(team_id)


def add_topup(account: CreditAccount, amount: Decimal, *, note: str) -> None:
    """Cộng Credit đã thanh toán vào sổ cái (type='topup'). Người gọi đã khóa account và commit cùng transaction đổi trạng thái đơn."""
    if amount <= 0:
        raise ValueError("Số Credit nạp phải dương")
    _add_transaction(account, "topup", xc.money(amount), note=note)


def get_balance(team_id: int) -> Decimal:
    return Decimal(ensure_account(team_id).balance_vnd)


def display_balance(team_id: int) -> Decimal:
    """Số dư để HIỂN THỊ (header mọi trang) — chỉ đọc, không tạo tài khoản. Team cũ chưa có tài khoản sẽ được cấp đúng TRIAL_CREDIT_VND ở lần
    dùng đầu tiên (ensure_account), nên hiển thị số đó thay vì 0."""
    account = CreditAccount.query.filter_by(team_id=team_id).first()
    return Decimal(account.balance_vnd) if account is not None else xc.money(Config.TRIAL_CREDIT_VND)


def reserve(team_id: int, amount: Decimal) -> Reservation | None:
    """Giữ chỗ `amount`. None nếu số dư không đủ (không ghi gì). Thành công thì COMMIT ngay (nhả khóa dòng, các lượt song song thấy số dư mới)."""
    amount = xc.money(amount)
    account = _locked_account(team_id)
    if Decimal(account.balance_vnd) < amount:
        db.session.commit()  # không ghi gì thêm; chỉ nhả khóa dòng và giữ tài khoản vừa được tạo lười (nếu có)
        return None
    _add_transaction(account, "reserve", -amount, note="Giữ chỗ cho lượt trả lời")
    db.session.commit()
    return Reservation(team_id, amount)


def begin_execution(team_id: int, *, estimate_vnd: Decimal, limit_vnd: Decimal) -> Reservation | Blocked:
    """Cửa vào của MỌI lượt chạy agent: (1) trần chi phí/lượt của bot — chặn TRƯỚC khi giữ chỗ; (2) giữ chỗ theo số dư còn lại của team."""
    estimate_vnd, limit_vnd = xc.money(estimate_vnd), xc.money(limit_vnd)
    if estimate_vnd > limit_vnd:
        return Blocked(BLOCK_MAX_COST, estimate_vnd, limit_vnd=limit_vnd)
    reservation = reserve(team_id, estimate_vnd)
    if reservation is None:
        return Blocked(BLOCK_INSUFFICIENT, estimate_vnd, balance_vnd=get_balance(team_id))
    return reservation


def settle(reservation: Reservation, *, execution_id: int, bot_id: int, llm: xc.LlmCost, price: xc.ExecutionPrice) -> ExecutionCost:
    """Quyết toán 1 lượt ĐÃ chạy (dù kết thúc thế nào): hoàn toàn bộ phần giữ chỗ rồi trừ giá bán thực (tối đa số dư). Ghi execution_costs.
    KHÔNG commit — người gọi commit cùng transaction với dòng agent_executions và tin bot."""
    account = _locked_account(reservation.team_id)
    if reservation.amount_vnd > 0:
        _add_transaction(account, "release", reservation.amount_vnd, execution_id=execution_id, note="Hoàn phần giữ chỗ")
    collected = min(price.billed_vnd, Decimal(account.balance_vnd))
    if collected > 0:
        _add_transaction(account, "execution_charge", -collected, execution_id=execution_id, note="Chi phí lượt trả lời")
    uncollected = xc.money(price.billed_vnd - collected)
    if uncollected > 0:
        logger.warning(
            "credit: team=%s execution=%s chi phí thực %s vượt số dư: chỉ thu %s, thiếu %s (lượt sau sẽ bị chặn tới khi có Credit)",
            reservation.team_id, execution_id, price.billed_vnd, collected, uncollected,
        )
    row = ExecutionCost(
        execution_id=execution_id, team_id=reservation.team_id, bot_id=bot_id,
        llm_input_tokens=llm.input_tokens, llm_output_tokens=llm.output_tokens,
        llm_cache_hit_tokens=llm.cache_hit_tokens, llm_cache_miss_tokens=llm.cache_miss_tokens,
        llm_cost_vnd=price.llm_cost_vnd, tool_cost_vnd=price.tool_cost_vnd, infra_cost_vnd=price.infra_cost_vnd,
        total_cost_vnd=price.total_cost_vnd, markup_multiplier=price.markup_multiplier, billed_vnd=price.billed_vnd,
        charged_vnd=collected, uncollected_vnd=uncollected, usage_reported=llm.reported,
    )
    db.session.add(row)
    if not llm.reported:
        logger.warning("credit: execution=%s có lệnh gọi LLM không đo được usage — phần đó không được tính phí", execution_id)
    return row


def has_credit_for_analysis(team_id: int, estimate_vnd: Decimal) -> bool:
    """Cổng vào của mọi việc chạy NỀN tính phí theo giá vốn thật thay vì agent_executions (Phase M phân tích module; đọc ảnh/PDF bản scan bằng
    DeepSeek vision của module "Đọc tài liệu"): số dư phải đủ ước tính cao nhất. Chỉ kiểm tra, KHÔNG giữ chỗ: việc chạy nền có thể bị ngắt giữa
    chừng (worker/tiến trình chết) và một khoản giữ chỗ mồ côi sẽ khoá Credit của team vô thời hạn. Đánh đổi: nếu lúc quyết toán số dư đã bị các
    lượt chat tiêu mất thì chỉ thu được phần còn lại (settle_module_analysis/settle_attachment_vision không bao giờ làm số dư âm), nền tảng chịu
    phần thiếu."""
    return get_balance(team_id) >= xc.money(estimate_vnd)


def settle_module_analysis(team_id: int, *, module_id: int, price: xc.ExecutionPrice) -> Decimal:
    """Trừ giá bán THỰC của lần phân tích (type 'module_analysis_charge', tối đa số dư — không âm). Trả số đã thu. KHÔNG commit — người gọi commit cùng
    transaction với trạng thái module."""
    account = _locked_account(team_id)
    collected = min(price.billed_vnd, Decimal(account.balance_vnd))
    if collected > 0:
        _add_transaction(account, "module_analysis_charge", -collected, note=f"Phân tích module #{module_id}")
    if price.billed_vnd - collected > 0:
        logger.warning("credit: team=%s module=%s chi phí phân tích %s vượt số dư: chỉ thu %s", team_id, module_id, price.billed_vnd, collected)
    return collected


def settle_attachment_vision(team_id: int, *, attachment_id: int, price: xc.ExecutionPrice) -> Decimal:
    """Trừ giá bán THỰC của việc đọc 1 tệp bằng DeepSeek vision (type 'attachment_vision_charge', tối đa số dư — không âm). Trả số đã thu.
    KHÔNG commit — người gọi (app/attachments/service.py) commit cùng transaction với trạng thái tệp. Chỉ gọi khi THỰC SỰ có lệnh gọi vision
    (markitdown/text không tốn gì — không gọi hàm này)."""
    account = _locked_account(team_id)
    collected = min(price.billed_vnd, Decimal(account.balance_vnd))
    if collected > 0:
        _add_transaction(account, "attachment_vision_charge", -collected, note=f"Đọc ảnh/PDF scan #{attachment_id}")
    if price.billed_vnd - collected > 0:
        logger.warning("credit: team=%s attachment=%s chi phí đọc ảnh/PDF scan %s vượt số dư: chỉ thu %s", team_id, attachment_id, price.billed_vnd, collected)
    return collected


def release_unused(reservation: Reservation, note: str = "Lượt không chạy được: hoàn giữ chỗ") -> None:
    """Hoàn đủ phần giữ chỗ của lượt KHÔNG tạo ra dòng agent_executions (agent chưa chạy / lỗi trước khi chạy). Không commit."""
    if reservation.amount_vnd <= 0:
        return
    account = _locked_account(reservation.team_id)
    _add_transaction(account, "release", reservation.amount_vnd, note=note)


def visible_transactions(team_id: int, limit: int = 20) -> list[CreditTransaction]:
    """Lịch sử cho giao diện khách: chỉ execution_charge / trial_grant / topup (mới nhất trước)."""
    return (
        CreditTransaction.query.filter(CreditTransaction.team_id == team_id, CreditTransaction.type.in_(VISIBLE_TYPES))
        .order_by(CreditTransaction.id.desc()).limit(limit).all()
    )


def out_of_credit_message(settings_row) -> str:
    return ((getattr(settings_row, "out_of_credit_message", None) or "").strip()) or Config.DEFAULT_OUT_OF_CREDIT_MESSAGE
