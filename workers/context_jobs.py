"""Worker việc nền của Decision Engine: rolling summary hội thoại + embed tin nhắn cho Historical Retrieval.

Cùng pattern workers/process_documents.py: DB là hàng đợi (cờ conversation_state.summary_pending; tin nhắn chưa có dòng trong
conversation_message_embeddings), chỉ 1 worker hoạt động tại 1 thời điểm (khoá Redis RIÊNG — không dùng chung khoá với worker
tài liệu để hai loại việc không chặn nhau), việc idempotent nên chết giữa chừng thì lần sau làm lại an toàn.

Hai cách chạy (cùng 1 mã): nhúng trong server (run.py gọi start_embedded khi EMBEDDED_WORKER=true) hoặc tách riêng
`python -m workers.context_jobs`. Không chạy trong request của khách.
"""
import atexit
import logging
import time
import uuid

from app import create_app
from app.models import BotSettings, ConversationState
from core.context_engine import jobs
from core.context_engine.settings import EngineSettings
from extensions import db, redis_client, socketio

logger = logging.getLogger(__name__)

POLL_SECONDS = 3
LOCK_KEY = "context-jobs-worker-lock"
LOCK_TTL = 60
SUMMARY_BACKOFF_SECONDS = 300  # tóm tắt lỗi (LLM/mạng) thì đợi chừng này rồi mới thử lại cuộc hội thoại đó
SUMMARY_SCAN = 20

_RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class ContextJobsLock:
    """Khoá phân tán trên Redis: chỉ chủ khoá mới xử lý, chỉ chủ khoá mới gia hạn/nhả được."""

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
            logger.warning("[context-jobs] không nhả được khoá Redis khi tắt (sẽ tự hết hạn sau %ss)", LOCK_TTL, exc_info=True)


def _backoff_key(state_id: int) -> str:
    return f"context-jobs:summary-backoff:{state_id}"


def process_summaries_once() -> int:
    """Tóm tắt các hội thoại đang có cờ summary_pending (không nằm trong thời gian chờ sau lỗi). Trả số bản tóm tắt đã ghi."""
    written = 0
    pending = ConversationState.query.filter_by(summary_pending=True).order_by(ConversationState.id.asc()).limit(SUMMARY_SCAN).all()
    for state in pending:
        if redis_client.exists(_backoff_key(state.id)):
            continue
        row = BotSettings.query.filter_by(bot_id=state.bot_id).first()
        try:
            if jobs.summarize_conversation(state, EngineSettings.from_model(row)):
                written += 1
        except Exception:
            db.session.rollback()
            redis_client.set(_backoff_key(state.id), "1", ex=SUMMARY_BACKOFF_SECONDS)
            logger.exception("[context-jobs] tóm tắt lỗi (conversation_id=%s), thử lại sau %ss", state.conversation_id, SUMMARY_BACKOFF_SECONDS)
    return written


def run_once() -> int:
    """1 vòng việc: embed lô tin chưa embed + tóm tắt hội thoại đang chờ. Trả số việc đã làm (0 = rảnh)."""
    return jobs.embed_pending_messages() + process_summaries_once()


def run_forever(app=None) -> None:
    app = app or create_app()
    lock = ContextJobsLock()
    atexit.register(lock.release)
    with app.app_context():
        print("[context-jobs] sẵn sàng (tóm tắt hội thoại + embed lịch sử chat)...", flush=True)
        while True:
            try:
                db.session.rollback()  # kết thúc giao dịch cũ (MySQL REPEATABLE READ giữ ảnh chụp cũ suốt giao dịch)
                if not lock.acquire():
                    time.sleep(POLL_SECONDS * 2)
                    continue
                if run_once() == 0:
                    time.sleep(POLL_SECONDS)
            except Exception:
                logger.exception("[context-jobs] lỗi trong vòng lặp, thử lại sau %ss", POLL_SECONDS)
                try:
                    db.session.rollback()
                except Exception:
                    logger.warning("[context-jobs] rollback sau lỗi cũng thất bại", exc_info=True)
                time.sleep(POLL_SECONDS)


def start_embedded(app) -> None:
    """Chạy trong tiến trình web (gọi từ run.py): 1 greenlet Socket.IO; embed/tokenizer nặng đã được đẩy sang luồng
    thật bởi rag_engine.run_blocking nên server vẫn phản hồi bình thường."""
    if app.config["EMBEDDED_WORKER"]:
        socketio.start_background_task(run_forever, app)


if __name__ == "__main__":
    run_forever()
