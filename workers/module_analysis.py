"""Worker phân tích module (Phase M2 vá): quét bảng bot_modules có status=pending, giải nén file .zip đã tải lên + phân tích từng module. Mỗi lần xử lý 1 module.

Cùng khuôn workers/process_documents.py: khoá Redis RIÊNG (không dùng chung với worker tài liệu/việc nền), chuyển pending -> analyzing bằng UPDATE có điều kiện
để 2 worker không nhận trùng, module kẹt 'analyzing' do worker trước chết thì đưa về pending khi khởi động. Chạy nhúng trong server (python run.py, mặc định)
hoặc tách riêng: EMBEDDED_MODULE_WORKER=false + `python -m workers.module_analysis`.
"""
import atexit
import logging
import time
import uuid

from app import create_app
from app.models import BotModule
from app.modules import runner
from extensions import db, redis_client, socketio

logger = logging.getLogger(__name__)

POLL_SECONDS = 3
LOCK_KEY = "module-analysis-worker-lock"
LOCK_TTL = 120  # giây; phân tích 1 module (vài URL, mỗi URL 1-2 lệnh gọi LLM) có thể lâu — vòng lặp gia hạn giữa các URL qua keepalive

_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class WorkerLock:
    def __init__(self) -> None:
        self.token = uuid.uuid4().hex

    def acquire(self) -> bool:
        return bool(redis_client.set(LOCK_KEY, self.token, nx=True, ex=LOCK_TTL)) or self.renew()

    def renew(self) -> bool:
        return bool(redis_client.eval(_RENEW, 1, LOCK_KEY, self.token, LOCK_TTL))

    def release(self) -> None:
        try:
            redis_client.eval(_RELEASE, 1, LOCK_KEY, self.token)
        except Exception:
            pass


def claim_next_module() -> BotModule | None:
    """Nhận 1 module pending — UPDATE có điều kiện để 2 worker không nhận trùng."""
    module = BotModule.query.filter_by(status="pending").order_by(BotModule.id.asc()).first()
    if module is None:
        return None
    claimed = BotModule.query.filter_by(id=module.id, status="pending").update({"status": "analyzing"})
    db.session.commit()
    return module if claimed else None


def handle(module: BotModule, keepalive=None) -> None:
    module_id = module.id
    started = time.time()
    try:
        status = runner.analyze_module(module_id, keepalive=keepalive)
    except Exception as exc:
        # Lỗi ngoài dự kiến (DB/Redis...): module không được kẹt 'analyzing' — ghi lỗi rõ ràng để chủ bot thấy và bấm phân tích lại
        db.session.rollback()
        logger.exception("[module-worker] FAILED module=%s", module_id)
        row = db.session.get(BotModule, module_id)
        if row is not None:
            row.status, row.error_message = "failed", f"Lỗi hệ thống khi phân tích: {type(exc).__name__}: {exc}"[:2000]
            db.session.commit()
        return
    print(f"[module-worker] {status} module={module_id} ({time.time() - started:.0f}s)", flush=True)


def run_forever(app=None) -> None:
    app = app or create_app()
    lock = WorkerLock()
    atexit.register(lock.release)
    recovered = False
    with app.app_context():
        print("[module-worker] sẵn sàng, đang chờ module pending...", flush=True)
        while True:
            try:
                db.session.rollback()  # kết thúc giao dịch cũ để thấy module mới (MySQL REPEATABLE READ giữ ảnh chụp dữ liệu)
                if not lock.acquire():
                    time.sleep(POLL_SECONDS * 2)
                    continue
                if not recovered:
                    BotModule.query.filter_by(status="analyzing").update({"status": "pending"})
                    db.session.commit()
                    recovered = True
                module = claim_next_module()
                if module is None:
                    time.sleep(POLL_SECONDS)
                    continue
                handle(module, keepalive=lock.renew)
            except Exception:
                logger.exception("[module-worker] lỗi trong vòng lặp, thử lại sau %ss", POLL_SECONDS)
                try:
                    db.session.rollback()
                except Exception:
                    pass
                time.sleep(POLL_SECONDS)


def start_embedded(app) -> None:
    """Chạy ngay trong tiến trình web (gọi từ run.py): greenlet của Socket.IO; crawl dùng socket đã vá eventlet nên không chặn server."""
    if app.config["EMBEDDED_MODULE_WORKER"]:
        socketio.start_background_task(run_forever, app)


if __name__ == "__main__":
    run_forever()
