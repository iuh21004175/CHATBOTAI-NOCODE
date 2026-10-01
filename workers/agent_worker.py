"""Worker AI Agent: nhận việc từ Redis, chạy agent (Harness) và trả kết quả. KHÔNG monkey-patch eventlet (SDK điều khiển tiến trình con
`dsh` bằng pipe + luồng, lỗi dưới eventlet trên Windows) nên PHẢI là tiến trình riêng, không phải greenlet trong run.py.

  python -m workers.agent_worker        # chạy riêng; hoặc run.py tự sinh nó khi AGENT_ENABLED=true và EMBEDDED_AGENT_WORKER=true

Nhẹ CPU: chỉ tốn khi đang chạy lượt. Số tiến trình dsh tối đa = AGENT_MAX_PROCESSES (mặc định 1; ~130 MB RAM mỗi tiến trình), tiến trình
rảnh quá AGENT_IDLE_SECONDS bị đóng. Không import `app`/`extensions` (tránh nạp Chroma/MinIO) — chỉ cần Redis.

Quy ước: worker không chạm DB; mọi thứ về hội thoại nằm trong việc (job) do Flask gửi và phiên của dsh bị xóa sau mỗi lượt.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from pathlib import Path

import redis

from config import Config
from core.context_engine.agent import protocol
from core.context_engine.agent.harness_backend import AgentPaths, HarnessRunner, RunOutput, ensure_profile

logger = logging.getLogger("agent_worker")

HEARTBEAT_SECONDS = 5
HEARTBEAT_TTL = 15
POP_TIMEOUT_SECONDS = 2
RESULT_TTL_SECONDS = 120


class RunnerPool:
    """Tối đa `max_processes` tiến trình. Một process_key chỉ có 1 tiến trình và chạy 1 lượt tại 1 thời điểm; thiếu chỗ thì đóng tiến trình
    RẢNH lâu nhất (LRU); tất cả đang bận thì chờ. `factory(job) -> runner` (runner: run(job)->RunOutput, close(), last_used, alive)."""

    def __init__(self, factory, max_processes: int):
        self._factory = factory
        self._max = max(1, max_processes)
        self._runners: dict[str, object] = {}
        self._busy: set[str] = set()
        self._cond = threading.Condition()

    def acquire(self, job: protocol.AgentJob):
        key = job.process_key
        with self._cond:
            while True:
                if key in self._runners and key not in self._busy:
                    self._busy.add(key)
                    return self._runners[key]
                if key not in self._runners:
                    if len(self._runners) < self._max:
                        break
                    idle = [k for k in self._runners if k not in self._busy]
                    if idle:
                        victim = min(idle, key=lambda k: self._runners[k].last_used)
                        self._runners.pop(victim).close()
                        break
                self._cond.wait(timeout=1.0)
            runner = self._factory(job)
            self._runners[key] = runner
            self._busy.add(key)
            return runner

    def release(self, job: protocol.AgentJob, *, discard: bool = False) -> None:
        key = job.process_key
        with self._cond:
            self._busy.discard(key)
            if discard and key in self._runners:
                self._runners.pop(key).close()
            self._cond.notify_all()

    def close_idle(self, older_than_seconds: float) -> int:
        closed = 0
        now = time.monotonic()
        with self._cond:
            for key in [k for k in self._runners if k not in self._busy and now - self._runners[k].last_used > older_than_seconds]:
                self._runners.pop(key).close()
                closed += 1
            self._cond.notify_all()
        return closed

    def close_all(self) -> None:
        with self._cond:
            for runner in self._runners.values():
                runner.close()
            self._runners.clear()
            self._busy.clear()

    def size(self) -> int:
        with self._cond:
            return len(self._runners)


class AgentWorker:
    def __init__(self, client, keys: protocol.Keys, factory, *, max_processes: int, idle_seconds: float, clock=time.time):
        self.redis = client
        self.keys = keys
        self.pool = RunnerPool(factory, max_processes)
        self.idle_seconds = idle_seconds
        self.clock = clock
        self._slots = threading.Semaphore(max(1, max_processes))  # mỗi việc đang chạy cần 1 chỗ: không rút thêm việc khi đã đầy
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # -- 1 việc --
    def handle(self, raw: str) -> None:
        try:
            job = protocol.AgentJob.from_json(raw)
        except (ValueError, TypeError, KeyError):
            logger.exception("việc hỏng, bỏ qua")
            return
        if job.deadline and self.clock() > job.deadline:
            # Flask đã ngừng chờ (hết hạn) -> chạy chỉ tốn tiền; không đẩy kết quả (không ai đọc)
            logger.warning("bỏ việc %s đã quá hạn %.1fs", job.job_id, self.clock() - job.deadline)
            return
        runner = self.pool.acquire(job)
        runner.progress = lambda code: self._push_progress(job.job_id, code)  # tiến trình thật của CHÍNH lượt này (runner được tái dùng giữa các lượt)
        discard = True
        try:
            output = runner.run(job)
            discard = output.discard_process
        except Exception as exc:  # runner tự bắt lỗi của lượt; đây là lỗi ngoài dự kiến -> báo lỗi, không nuốt
            logger.exception("runner lỗi")
            output = RunOutput(protocol.STATUS_FAILED, protocol.RunSummary(), 0.0, error=f"{type(exc).__name__}: {exc}", discard_process=True)
        finally:
            runner.progress = None
            self.pool.release(job, discard=discard)
        self._reply(job.job_id, {"job_id": job.job_id, **output.as_dict()})

    def _push_progress(self, job_id: str, code: str) -> None:
        """Báo cho Flask agent đang làm bước gì. Chỉ là thông tin hiển thị cho khách: Redis lỗi thì ghi log và lượt vẫn chạy tiếp (kết quả cuối
        đi qua _reply, nơi lỗi Redis vẫn được báo bình thường)."""
        key = self.keys.progress(job_id)
        try:
            pipe = self.redis.pipeline()
            pipe.rpush(key, code)
            pipe.expire(key, RESULT_TTL_SECONDS)
            pipe.execute()
        except redis.RedisError:
            logger.warning("không đẩy được tiến trình %s của việc %s", code, job_id, exc_info=True)

    def _reply(self, job_id: str, payload: dict) -> None:
        key = self.keys.result(job_id)
        pipe = self.redis.pipeline()
        pipe.rpush(key, json.dumps(payload, ensure_ascii=False))
        pipe.expire(key, RESULT_TTL_SECONDS)
        pipe.execute()

    def _run_and_release_slot(self, raw: str) -> None:
        try:
            self.handle(raw)
        except Exception:
            logger.exception("việc lỗi ngoài dự kiến")
        finally:
            self._slots.release()

    # -- vòng lặp --
    def heartbeat(self) -> None:
        self.redis.set(self.keys.worker_alive, str(self.clock()), ex=HEARTBEAT_TTL)

    def step(self) -> bool:
        """Rút tối đa 1 việc rồi giao cho 1 luồng. True nếu đã nhận việc. (Tách ra để test.)"""
        self.heartbeat()
        self.pool.close_idle(self.idle_seconds)
        if not self._slots.acquire(timeout=1.0):
            return False
        popped = self.redis.blpop(self.keys.jobs, timeout=POP_TIMEOUT_SECONDS)
        if not popped:
            self._slots.release()
            return False
        thread = threading.Thread(target=self._run_and_release_slot, args=(popped[1],), daemon=True, name="agent-job")
        thread.start()
        self._threads = [t for t in self._threads if t.is_alive()] + [thread]
        return True

    def run_forever(self) -> None:
        logger.info("[agent-worker] sẵn sàng (tối đa %d tiến trình dsh)", self.pool._max)
        while not self._stop.is_set():
            try:
                self.step()
            except redis.RedisError:
                logger.exception("[agent-worker] Redis lỗi, thử lại sau 3s")
                time.sleep(3)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=2)
        self.pool.close_all()


def build_worker() -> AgentWorker:
    paths = AgentPaths(Path(Config.AGENT_HOME))
    paths.ensure()
    ensure_profile(paths)
    api_key = Config.DEEPSEEK_API_KEY
    client = redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)
    return AgentWorker(
        client, protocol.Keys(Config.AGENT_REDIS_PREFIX), lambda job: HarnessRunner(job, paths, api_key),
        max_processes=Config.AGENT_MAX_PROCESSES, idle_seconds=Config.AGENT_IDLE_SECONDS,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = build_worker()
    try:
        worker.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        worker.stop()


def spawn_embedded():
    """Gọi từ run.py (đã monkey-patch eventlet): sinh worker thành TIẾN TRÌNH CON thật bằng subprocess NGUYÊN BẢN (bản đã patch của
    eventlet lỗi với pipe trên Windows). Trả Popen; run.py không cần quản lý thêm — tiến trình con tự thoát khi cha thoát (atexit)."""
    import atexit
    import os

    try:
        from eventlet import patcher

        subprocess_module = patcher.original("subprocess")
    except ImportError:  # pragma: no cover
        import subprocess as subprocess_module

    child = subprocess_module.Popen([sys.executable, "-m", "workers.agent_worker"], cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _stop_child():
        if child.poll() is None:
            child.terminate()

    atexit.register(_stop_child)
    return child


if __name__ == "__main__":
    main()
