"""Worker xử lý tài liệu: quét bảng documents có status=pending, tải file từ MinIO, cắt chunk + embed +
ghi ChromaDB. Mỗi lần chỉ xử lý 1 tài liệu để RAM/CPU không bị nghẽn.

Hai cách chạy (cùng 1 mã):
- MẶC ĐỊNH — nhúng trong server: `python run.py` tự khởi động worker cùng app (start_embedded), nạp model
  ngay lúc khởi động và xử lý luôn các tài liệu đang chờ. Không cần bật thêm tiến trình nào.
- Tách riêng (production): đặt EMBEDDED_WORKER=false rồi chạy `python -m workers.process_documents`.

Chỉ 1 worker hoạt động tại 1 thời điểm (khoá Redis) nên bật nhầm cả hai cách cũng không xử lý trùng.
Mỗi thay đổi trạng thái (đang xử lý + tiến độ, đã huấn luyện, thất bại) được đẩy realtime tới trình duyệt
đang mở trang Cơ sở tri thức qua Socket.IO.
"""
import atexit
import logging
import time
import uuid

from app import create_app
from app.dashboard import service
from app.dashboard.events import emit_document_status
from app.models import Bot, Document
from core import rag_engine, storage_service
from extensions import db, redis_client, socketio

logger = logging.getLogger(__name__)

POLL_SECONDS = 3
LOCK_KEY = "knowledge-worker-lock"
LOCK_TTL = 60  # giây; worker chết đột ngột thì sau chừng này worker khác tự tiếp quản

_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class WorkerLock:
    """Khoá phân tán trên Redis: chỉ chủ khoá mới được xử lý tài liệu, và chỉ chủ khoá mới gia hạn/nhả được."""

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


def claim_next_document() -> Document | None:
    """Nhận 1 tài liệu pending — UPDATE có điều kiện để 2 worker không nhận trùng."""
    doc = Document.query.filter_by(status="pending").order_by(Document.id.asc()).first()
    if doc is None:
        return None
    claimed = Document.query.filter_by(id=doc.id, status="pending").update({"status": "processing"})
    db.session.commit()
    return doc if claimed else None


def handle(doc: Document, keepalive=None) -> None:
    """keepalive(): được gọi sau mỗi lô embed để gia hạn khoá (tài liệu lớn có thể chạy hàng chục phút)."""
    started = time.time()
    label = f"doc={doc.id} {doc.filename}"
    emit_document_status(doc, "processing", done=0, total=0)

    def on_progress(done: int, total: int) -> None:
        if keepalive:
            keepalive()
        emit_document_status(doc, "processing", done=done, total=total)

    try:
        bot = db.session.get(Bot, doc.bot_id)
        obj = storage_service.get_file(doc.storage_path)
        try:
            raw = obj.read()
        finally:
            obj.close()
            obj.release_conn()
        count = service.process_document(bot, doc, raw, on_progress)
    except Exception as e:
        db.session.rollback()
        if doc.status != "failed":  # lỗi tải file (process_document đã tự đặt failed với lỗi ở bước xử lý)
            service.mark_failed(doc, service.failure_message(e))
        print(f"[worker] FAILED {label}: {e}", flush=True)
        emit_document_status(doc, "failed", error=doc.error_message)
        return

    print(f"[worker] OK {label} ({count} chunk, {time.time() - started:.0f}s)", flush=True)
    emit_document_status(doc, "trained", chunks=count)


def run_forever(app=None) -> None:
    app = app or create_app()
    lock = WorkerLock()
    atexit.register(lock.release)  # tắt bình thường (Ctrl+C) thì nhả khoá ngay để worker khác tiếp quản nhanh
    recovered = False
    with app.app_context():
        print("[worker] sẵn sàng, đang chờ tài liệu pending...", flush=True)
        while True:
            try:
                # Kết thúc giao dịch cũ: MySQL (REPEATABLE READ) giữ ảnh chụp dữ liệu suốt giao dịch nên
                # nếu không làm thế, tài liệu mới tải lên sẽ không bao giờ hiện ra với vòng quét này.
                db.session.rollback()
                if not lock.acquire():
                    time.sleep(POLL_SECONDS * 2)  # worker khác đang làm việc, đứng chờ
                    continue
                if not recovered:
                    # Tài liệu kẹt "processing" do worker trước bị tắt giữa chừng -> đưa về pending
                    Document.query.filter_by(status="processing").update({"status": "pending"})
                    db.session.commit()
                    recovered = True
                doc = claim_next_document()
                if doc is None:
                    time.sleep(POLL_SECONDS)
                    continue
                handle(doc, keepalive=lock.renew)
            except Exception:
                # Vòng lặp không được chết vì 1 lỗi tạm thời (MySQL/Redis chập chờn): ghi log rồi thử lại
                logger.exception("[worker] lỗi trong vòng lặp, thử lại sau %ss", POLL_SECONDS)
                try:
                    db.session.rollback()
                except Exception:
                    pass
                time.sleep(POLL_SECONDS)


def start_embedded(app) -> None:
    """Chạy worker ngay trong tiến trình web (gọi từ run.py sau khi app khởi tạo): nạp model ở nền rồi
    xử lý tài liệu. Cả hai chạy trong "greenlet" của Socket.IO; phần tính toán nặng được đẩy sang luồng
    thật (rag_engine.run_blocking) nên server vẫn phản hồi bình thường khi đang embed."""
    socketio.start_background_task(rag_engine.warm_up)
    if app.config["EMBEDDED_WORKER"]:
        socketio.start_background_task(run_forever, app)


if __name__ == "__main__":
    run_forever()
