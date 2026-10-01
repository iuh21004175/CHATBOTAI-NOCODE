"""Đăng ký URL webhook với payOS (chạy 1 lần sau khi đã điền PAYOS_* trong .env và có địa chỉ công khai của web).

    env/Scripts/python.exe -m scripts.payos_confirm_webhook https://abc.ngrok.app/payos/webhook

payOS sẽ gọi thử URL này (phải trả 2xx, xem app/payments/routes.py:webhook). Không in khóa ra màn hình.
"""
import sys

from config import Config
from core import payos_client as payos


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].startswith("https://"):
        print("Cách dùng: python -m scripts.payos_confirm_webhook https://<domain-công-khai>/payos/webhook")
        return 2
    config = payos.PayOSConfig(Config.PAYOS_CLIENT_ID, Config.PAYOS_API_KEY, Config.PAYOS_CHECKSUM_KEY, Config.PAYOS_API_BASE)
    try:
        data = payos.confirm_webhook(config, argv[1])
    except payos.PayOSError as exc:
        print(f"Đăng ký webhook thất bại: {exc}")
        return 1
    print(f"Đã đăng ký webhook: {data.get('webhookUrl', argv[1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
