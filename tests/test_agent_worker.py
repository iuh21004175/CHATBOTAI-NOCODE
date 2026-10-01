"""Worker AI Agent (workers/agent_worker.py): nhận việc qua Redis, pool tiến trình dsh giới hạn, dọn tiến trình rảnh, bỏ việc quá hạn.
Dùng Redis thật (như test_worker.py) với prefix riêng mỗi test; runner GIẢ (không chạy dsh)."""
import json
import threading
import time
import unittest
import uuid

import redis

from config import Config
from core.context_engine.agent import protocol as p
from core.context_engine.agent.harness_backend import RunOutput
from workers.agent_worker import AgentWorker, RunnerPool


def redis_client():
    return redis.Redis.from_url(Config.REDIS_URL, decode_responses=True)


class FakeRunner:
    created = []

    def __init__(self, job, behaviour=None):
        self.job = job
        self.key = job.process_key
        self.closed = False
        self.last_used = time.monotonic()
        self.runs = []
        self.behaviour = behaviour or (lambda j: RunOutput(p.STATUS_COMPLETED, p.summarize_events([], "ok"), 0.01))
        FakeRunner.created.append(self)

    def run(self, job):
        self.runs.append(job.job_id)
        self.last_used = time.monotonic()
        return self.behaviour(job)

    def close(self):
        self.closed = True


def job(job_id="j1", bot_id=1, persona="P", deadline=0.0, **over):
    base = dict(job_id=job_id, bot_id=bot_id, token="t", persona=persona, max_tokens=500, language="vi", input="x",
                internal_url="http://127.0.0.1:5000", model="m", deadline=deadline)
    base.update(over)
    return p.AgentJob(**base)


class PoolCase(unittest.TestCase):
    def setUp(self):
        FakeRunner.created = []

    def pool(self, max_processes=2):
        return RunnerPool(lambda j: FakeRunner(j), max_processes)


class Pool(PoolCase):
    def test_same_key_reuses_the_same_process(self):
        pool = self.pool()
        a = pool.acquire(job("1"))
        pool.release(job("1"))
        b = pool.acquire(job("2"))
        self.assertIs(a, b)
        self.assertEqual(len(FakeRunner.created), 1)

    def test_different_bots_get_different_processes_up_to_the_limit(self):
        pool = self.pool(2)
        pool.acquire(job(bot_id=1))
        pool.acquire(job(bot_id=2))
        self.assertEqual(pool.size(), 2)

    def test_lru_idle_process_is_evicted_when_full(self):
        pool = self.pool(2)
        first = pool.acquire(job(bot_id=1))
        pool.release(job(bot_id=1))
        time.sleep(0.01)
        second = pool.acquire(job(bot_id=2))
        pool.release(job(bot_id=2))
        third = pool.acquire(job(bot_id=3))
        self.assertTrue(first.closed and not second.closed, "đóng tiến trình rảnh LÂU NHẤT")
        self.assertEqual(pool.size(), 2)
        self.assertFalse(third.closed)

    def test_config_change_gets_a_new_process_and_old_one_is_evicted_at_limit_1(self):
        pool = self.pool(1)
        old = pool.acquire(job(persona="cũ"))
        pool.release(job(persona="cũ"))
        new = pool.acquire(job(persona="mới"))
        self.assertTrue(old.closed)
        self.assertIsNot(old, new)
        self.assertEqual(pool.size(), 1)

    def test_a_busy_process_is_never_evicted_and_caller_waits(self):
        pool = self.pool(1)
        pool.acquire(job(bot_id=1))
        got = []
        thread = threading.Thread(target=lambda: got.append(pool.acquire(job(bot_id=2))), daemon=True)
        thread.start()
        time.sleep(0.3)
        self.assertEqual(got, [], "phải chờ, không được giành tiến trình đang chạy lượt khác")
        self.assertFalse(FakeRunner.created[0].closed)
        pool.release(job(bot_id=1))
        thread.join(timeout=5)
        self.assertEqual(len(got), 1)
        self.assertTrue(FakeRunner.created[0].closed)

    def test_same_bot_turns_are_serialised(self):
        pool = self.pool(2)
        pool.acquire(job("1"))
        second = []
        thread = threading.Thread(target=lambda: second.append(pool.acquire(job("2"))), daemon=True)
        thread.start()
        time.sleep(0.3)
        self.assertEqual(second, [])
        pool.release(job("1"))
        thread.join(timeout=5)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(FakeRunner.created), 1)

    def test_discard_closes_the_process_so_the_next_turn_gets_a_fresh_one(self):
        pool = self.pool()
        first = pool.acquire(job("1"))
        pool.release(job("1"), discard=True)
        self.assertTrue(first.closed)
        second = pool.acquire(job("2"))
        self.assertIsNot(first, second)

    def test_close_idle_only_closes_idle_and_old_enough(self):
        pool = self.pool(3)
        idle_old = pool.acquire(job(bot_id=1))
        pool.release(job(bot_id=1))
        busy = pool.acquire(job(bot_id=2))
        idle_old.last_used -= 1000
        busy.last_used -= 1000
        idle_new = pool.acquire(job(bot_id=3))
        pool.release(job(bot_id=3))
        self.assertEqual(pool.close_idle(300), 1)
        self.assertTrue(idle_old.closed)
        self.assertFalse(busy.closed or idle_new.closed)
        self.assertEqual(pool.size(), 2)

    def test_close_all(self):
        pool = self.pool()
        pool.acquire(job(bot_id=1))
        pool.acquire(job(bot_id=2))
        pool.close_all()
        self.assertTrue(all(r.closed for r in FakeRunner.created))
        self.assertEqual(pool.size(), 0)

    def test_limit_below_one_is_treated_as_one(self):
        pool = RunnerPool(lambda j: FakeRunner(j), 0)
        pool.acquire(job())
        self.assertEqual(pool.size(), 1)


class WorkerCase(unittest.TestCase):
    def setUp(self):
        FakeRunner.created = []
        self.redis = redis_client()
        self.keys = p.Keys(f"test-agent-{uuid.uuid4().hex[:10]}")
        self.addCleanup(self.cleanup)
        self.now = 1000.0

    def cleanup(self):
        for key in self.redis.scan_iter(f"{self.keys.prefix}:*"):
            self.redis.delete(key)

    def worker(self, factory=None, max_processes=1, idle=300):
        return AgentWorker(self.redis, self.keys, factory or (lambda j: FakeRunner(j)), max_processes=max_processes, idle_seconds=idle,
                           clock=lambda: self.now)

    def result(self, job_id, timeout=5):
        raw = self.redis.blpop(self.keys.result(job_id), timeout=timeout)
        return json.loads(raw[1]) if raw else None


class Handling(WorkerCase):
    def test_handle_runs_the_job_and_pushes_the_result(self):
        worker = self.worker()
        worker.handle(job("r1").to_json())
        result = self.result("r1")
        self.assertEqual((result["job_id"], result["status"]), ("r1", p.STATUS_COMPLETED))
        self.assertEqual(FakeRunner.created[0].runs, ["r1"])

    def test_result_key_expires(self):
        self.worker().handle(job("r2").to_json())
        self.assertTrue(0 < self.redis.ttl(self.keys.result("r2")) <= 120)

    def test_stale_job_is_dropped_without_running_or_replying(self):
        worker = self.worker()
        worker.handle(job("old", deadline=self.now - 5).to_json())
        self.assertEqual(FakeRunner.created, [])
        self.assertIsNone(self.result("old", timeout=1))

    def test_job_without_deadline_or_in_the_future_runs(self):
        worker = self.worker()
        worker.handle(job("a", deadline=0.0).to_json())
        worker.handle(job("b", deadline=self.now + 60).to_json())
        self.assertIsNotNone(self.result("a"))
        self.assertIsNotNone(self.result("b"))

    def test_malformed_jobs_are_ignored_not_fatal(self):
        worker = self.worker()
        for raw in ("khong-phai-json", "{}", '{"job_id": 1}', "[]", ""):
            worker.handle(raw)
        self.assertEqual(FakeRunner.created, [])
        worker.handle(job("ok").to_json())
        self.assertIsNotNone(self.result("ok"))

    def test_failed_run_is_reported_and_process_discarded(self):
        failing = lambda j: RunOutput(p.STATUS_TIMEOUT, p.RunSummary(), 25.0, error="quá 25s", discard_process=True, stop_reason="timeout")
        worker = self.worker(lambda j: FakeRunner(j, failing))
        worker.handle(job("t1").to_json())
        result = self.result("t1")
        self.assertEqual((result["status"], result["error"]), (p.STATUS_TIMEOUT, "quá 25s"))
        self.assertTrue(FakeRunner.created[0].closed)
        self.assertEqual(worker.pool.size(), 0)

    def test_runner_exception_becomes_a_failed_result_and_frees_the_slot(self):
        def boom(_job):
            raise RuntimeError("nổ")

        worker = self.worker(lambda j: FakeRunner(j, boom))
        worker.handle(job("e1").to_json())
        result = self.result("e1")
        self.assertEqual(result["status"], p.STATUS_FAILED)
        self.assertIn("nổ", result["error"])
        self.assertEqual(worker.pool.size(), 0, "tiến trình lỗi không được giữ lại")
        worker.handle(job("e2", bot_id=1).to_json())  # pool không bị kẹt
        self.assertIsNotNone(self.result("e2"))

    def test_successful_process_is_kept_for_the_next_turn(self):
        worker = self.worker()
        worker.handle(job("k1").to_json())
        worker.handle(job("k2").to_json())
        self.assertEqual(len(FakeRunner.created), 1)
        self.assertEqual(FakeRunner.created[0].runs, ["k1", "k2"])


class Loop(WorkerCase):
    def test_step_consumes_a_queued_job_and_sets_heartbeat(self):
        worker = self.worker()
        self.redis.rpush(self.keys.jobs, job("q1").to_json())
        self.assertTrue(worker.step())
        result = self.result("q1")
        self.assertEqual(result["status"], p.STATUS_COMPLETED)
        self.assertTrue(self.redis.exists(self.keys.worker_alive))
        self.assertTrue(0 < self.redis.ttl(self.keys.worker_alive) <= 15)

    def test_step_returns_false_when_idle_but_still_heartbeats(self):
        worker = self.worker()
        self.assertFalse(worker.step())
        self.assertTrue(self.redis.exists(self.keys.worker_alive))

    def test_jobs_are_processed_in_fifo_order(self):
        order = []

        def behaviour(j):
            order.append(j.job_id)
            return RunOutput(p.STATUS_COMPLETED, p.RunSummary(), 0.0)

        worker = self.worker(lambda j: FakeRunner(j, behaviour), max_processes=1)
        for i in range(3):
            self.redis.rpush(self.keys.jobs, job(f"f{i}").to_json())
        for _ in range(3):
            worker.step()
            for t in list(worker._threads):
                t.join(timeout=5)
        self.assertEqual(order, ["f0", "f1", "f2"])

    def test_concurrency_never_exceeds_max_processes(self):
        active, peak, lock = [0], [0], threading.Lock()

        def slow(j):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.15)
            with lock:
                active[0] -= 1
            return RunOutput(p.STATUS_COMPLETED, p.RunSummary(), 0.15)

        worker = self.worker(lambda j: FakeRunner(j, slow), max_processes=2)
        for i in range(6):
            self.redis.rpush(self.keys.jobs, job(f"c{i}", bot_id=i).to_json())
        for _ in range(6):
            worker.step()
        for t in list(worker._threads):
            t.join(timeout=10)
        self.assertEqual([self.result(f"c{i}") is not None for i in range(6)], [True] * 6)
        self.assertLessEqual(peak[0], 2)

    def test_slot_is_released_even_if_reply_fails(self):
        worker = self.worker(max_processes=1)
        worker.redis = None  # làm _reply lỗi
        worker._slots.acquire()
        worker._run_and_release_slot(job("x").to_json())
        self.assertTrue(worker._slots.acquire(timeout=1), "chỗ phải được nhả để worker không tự khóa mình")

    def test_idle_processes_are_closed_by_step(self):
        worker = self.worker(idle=0)
        worker.handle(job("i1").to_json())
        FakeRunner.created[0].last_used -= 10
        worker.step()
        self.assertTrue(FakeRunner.created[0].closed)

    def test_stop_closes_everything(self):
        worker = self.worker()
        worker.handle(job("s1").to_json())
        worker.stop()
        self.assertTrue(FakeRunner.created[0].closed)


class Prefixes(WorkerCase):
    def test_workers_with_different_prefixes_do_not_see_each_others_jobs(self):
        other = AgentWorker(self.redis, p.Keys(self.keys.prefix + "-b"), lambda j: FakeRunner(j), max_processes=1, idle_seconds=300)
        self.addCleanup(lambda: [self.redis.delete(k) for k in self.redis.scan_iter(f"{self.keys.prefix}-b:*")])
        self.redis.rpush(self.keys.jobs, job("mine").to_json())
        self.assertFalse(other.step())
        self.assertTrue(self.worker().step())


if __name__ == "__main__":
    unittest.main()
