"""Client PayOS (cổng thanh toán payOS, https://payos.vn) — CHỈ dùng thư viện chuẩn (urllib, hmac, hashlib, json): không thêm dependency.
Không đụng DB, không đọc Flask config: nhận cấu hình qua `PayOSConfig` (app/payments/service.py dựng từ config.Config).

Quy tắc đã đối chiếu với tài liệu/SDK chính thức của payOS (payos.vn/docs/api, github.com/payOSHQ/payos-lib-python):
- Base URL https://api-merchant.payos.vn; header x-client-id + x-api-key.
- Tạo link: POST /v2/payment-requests; chữ ký = HMAC-SHA256 (hex) của chuỗi
  "amount=..&cancelUrl=..&description=..&orderCode=..&returnUrl=.." (khóa theo thứ tự chữ cái) bằng checksum key. Thành công: code == "00",
  data.checkoutUrl / data.paymentLinkId.
- Lấy thông tin: GET /v2/payment-requests/{orderCode}; data.status: PENDING | PAID | CANCELLED | EXPIRED | PROCESSING | UNDERPAID.
- Webhook: body {code, desc, success, data{...}, signature}; chữ ký = HMAC-SHA256 (hex) của TOÀN BỘ `data` — khóa xếp theo chữ cái, "k=v" nối bằng "&",
  None -> "", bool -> "true"/"false", list/dict -> JSON gọn (separators=(",", ":")). Thanh toán thành công khi code == "00".
- Đăng ký webhook: POST /confirm-webhook {"webhookUrl": ...}.
Lỗi mạng/HTTP/định dạng KHÔNG bị nuốt: ném PayOSError để tầng trên báo cho người dùng.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://api-merchant.payos.vn"
SUCCESS_CODE = "00"
TIMEOUT_SECONDS = 15
# Cloudflare phía trước api-merchant.payos.vn chặn User-Agent mặc định "Python-urllib/x.y" (HTTP 403, "error code: 1010") trước khi tới API:
# client phải tự nhận diện bằng User-Agent riêng (đã xác nhận: cùng khóa, UA riêng -> API trả phản hồi JSON bình thường).
USER_AGENT = "chatbotai-nocode-payos/1.0"


class PayOSError(RuntimeError):
    """Lỗi khi gọi payOS (mạng, HTTP, phản hồi không hợp lệ, hoặc payOS từ chối)."""


class PayOSSignatureError(PayOSError):
    """Chữ ký webhook không khớp (dữ liệu bị sửa hoặc không phải từ payOS)."""


@dataclass(frozen=True)
class PayOSConfig:
    client_id: str
    api_key: str
    checksum_key: str
    base_url: str = DEFAULT_BASE_URL

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.api_key and self.checksum_key)


# ---------------------------------------------------------------- chữ ký

def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _hmac_hex(checksum_key: str, message: str) -> str:
    return hmac.new(checksum_key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_object(data: dict, checksum_key: str) -> str:
    """Chữ ký của 1 object (dữ liệu webhook): khóa xếp theo chữ cái, "k=v" nối bằng "&"."""
    message = "&".join(f"{key}={_stringify(data[key])}" for key in sorted(data))
    return _hmac_hex(checksum_key, message)


def sign_payment_request(checksum_key: str, *, amount: int, cancel_url: str, description: str, order_code: int, return_url: str) -> str:
    message = f"amount={amount}&cancelUrl={cancel_url}&description={description}&orderCode={order_code}&returnUrl={return_url}"
    return _hmac_hex(checksum_key, message)


def verify_webhook(payload, checksum_key: str) -> dict:
    """Kiểm tra chữ ký webhook, trả về `data` đã xác thực. Ném PayOSSignatureError nếu sai/thiếu chữ ký (so sánh hằng thời gian)."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict) or not isinstance(payload.get("signature"), str):
        raise PayOSSignatureError("Webhook thiếu data hoặc signature")
    expected = sign_object(payload["data"], checksum_key)
    if not hmac.compare_digest(expected, payload["signature"]):
        raise PayOSSignatureError("Chữ ký webhook không khớp")
    return payload["data"]


# ---------------------------------------------------------------- gọi API

def _call(config: PayOSConfig, method: str, path: str, body: dict | None = None) -> dict:
    if not config.configured:
        raise PayOSError("Chưa cấu hình PAYOS_CLIENT_ID / PAYOS_API_KEY / PAYOS_CHECKSUM_KEY")
    request = urllib.request.Request(
        config.base_url.rstrip("/") + path,
        data=None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method=method,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, "x-client-id": config.client_id, "x-api-key": config.api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()  # payOS trả lỗi nghiệp vụ kèm JSON {code, desc}: đọc để báo đúng lý do
        try:
            detail = json.loads(raw).get("desc")
        except (ValueError, AttributeError):
            detail = raw.decode("utf-8", errors="replace").strip()[:120]  # không phải JSON (vd. lớp chặn Cloudflare): giữ nội dung thô để còn chẩn đoán
        raise PayOSError(f"payOS trả HTTP {exc.code}: {detail or 'không rõ lý do'}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PayOSError(f"Không kết nối được payOS: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise PayOSError("payOS trả dữ liệu không phải JSON") from exc
    if not isinstance(parsed, dict):
        raise PayOSError("payOS trả dữ liệu không đúng định dạng")
    if parsed.get("code") != SUCCESS_CODE:
        raise PayOSError(f"payOS từ chối: {parsed.get('desc') or parsed.get('code')}")
    data = parsed.get("data")
    if not isinstance(data, dict):
        raise PayOSError("payOS trả thiếu data")
    return data


def create_payment_link(
    config: PayOSConfig, *, order_code: int, amount: int, description: str, return_url: str, cancel_url: str,
    expired_at: int | None = None, items: list[dict] | None = None,
) -> dict:
    """Trả data của payOS: checkoutUrl, paymentLinkId, ..."""
    body = {
        "orderCode": order_code, "amount": amount, "description": description, "cancelUrl": cancel_url, "returnUrl": return_url,
        "signature": sign_payment_request(config.checksum_key, amount=amount, cancel_url=cancel_url, description=description,
                                          order_code=order_code, return_url=return_url),
    }
    if items:
        body["items"] = items
    if expired_at:
        body["expiredAt"] = expired_at
    data = _call(config, "POST", "/v2/payment-requests", body)
    if not data.get("checkoutUrl") or not data.get("paymentLinkId"):
        raise PayOSError("payOS không trả checkoutUrl/paymentLinkId")
    return data


def get_payment(config: PayOSConfig, order_code: int) -> dict:
    """Thông tin đơn theo orderCode (data.status, data.amount, data.amountPaid, ...)."""
    return _call(config, "GET", f"/v2/payment-requests/{int(order_code)}")


def confirm_webhook(config: PayOSConfig, webhook_url: str) -> dict:
    """Đăng ký URL webhook với payOS (payOS gọi thử URL này, phải trả 2xx)."""
    return _call(config, "POST", "/confirm-webhook", {"webhookUrl": webhook_url})
