"""Lớp bọc `deepseek-harness-sdk` — CHỈ được import trong worker (tiến trình KHÔNG monkey-patch eventlet: SDK điều khiển tiến trình
con `dsh` qua pipe + luồng, đã đo là lỗi OSError 22 dưới eventlet trên Windows).

Bảo đảm an toàn (mỗi điều có test trong tests/test_agent_backend.py; điều 1-2 đã kiểm chứng thật bằng dsh):
1. profile `sdk-minimal` mặc định cho model MỘT công cụ duy nhất là shell (pwsh/bash) ghim `danger-full-access` -> khách nhắn "chạy whoami"
   là agent chạy lệnh trên máy chủ. Shell gồm 2 lớp, PHẢI gỡ cả hai: backend PTY (`terminal-*`) và hàng đăng ký công cụ cho model
   (`persistent-pwsh` trên Windows / `persistent-bash` trên Linux). Chỉ gỡ backend thì lệnh không chạy được nhưng công cụ vẫn được
   quảng bá (tốn token mỗi lượt, model phí 1 bước gọi rồi lộ lỗi "no PTY backend"). Sau patch chỉ còn công cụ MCP `kb`.
2. plugin `session-log-deepseek` mặc định tự tải log phiên (nội dung hội thoại của khách) lên DeepSeek -> tắt.
3. Phiên của dsh lưu bền dạng JSONL trong DSH_HOME -> xóa ngay sau mỗi lượt (dữ liệu khách chỉ nằm ở DB của ta).
4. Mỗi tiến trình dsh chỉ chạy MỘT lượt tại một thời điểm (worker giữ khóa), nên khi vượt giới hạn có thể đóng cả tiến trình mà không
   làm hỏng lượt của khách khác.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.context_engine.agent import protocol

PROFILE = "sdk-minimal"
INIT_TIMEOUT_SECONDS = 60
HARD_TOOL_MARGIN = 2  # soft limit (MCP từ chối tra cứu) + chừng này lần gọi "cố tình vượt" thì worker dừng cứng (runtime.from_config dùng cùng hằng số)

_MCP_SCRIPT = str(Path(__file__).with_name("kb_mcp_server.py").resolve())


class RunAborted(Exception):
    """Lượt bị dừng do vượt giới hạn (status = một trong protocol.STATUS_*)."""

    def __init__(self, status: str, message: str = ""):
        super().__init__(message or status)
        self.status = status


@dataclass
class RunOutput:
    status: str
    summary: protocol.RunSummary
    runtime_seconds: float
    error: str | None = None
    discard_process: bool = False   # True: tiến trình dsh không còn dùng được/không còn đáng tin -> worker đóng nó
    stop_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status, "summary": self.summary.as_dict(), "runtime_seconds": round(self.runtime_seconds, 3),
            "error": self.error, "stop_reason": self.stop_reason,
        }


# ---------------------------------------------------------------- cấu hình profile

def patch_yaml(python_executable: str | None = None, mcp_script: str | None = None) -> str:
    """Patch YAML cho profile: gỡ shell + tắt tải log, chèn máy chủ MCP `kb`. Giá trị theo bot đi qua BIẾN MÔI TRƯỜNG (`!!js
    process.env.X`) nên 1 tệp dùng chung mọi bot. Hàng mới PHẢI nằm trong `insert:` (nếu không dsh coi là sửa hàng có sẵn và bỏ qua)."""
    py = (python_executable or sys.executable).replace("\\", "/")
    script = (mcp_script or _MCP_SCRIPT).replace("\\", "/")
    return f"""# Tự sinh bởi core/context_engine/agent/harness_backend.py — không sửa tay
- id: terminal-pwsh
  disabled: true
- id: terminal-bash
  disabled: true
- id: persistent-pwsh
  disabled: true
- id: persistent-bash
  disabled: true
- id: session-log-deepseek
  disabled: true
- id: plugin-package-inventory-deepseek
  disabled: true
- insert:
    - id: mcp-kb
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: {protocol.SERVER_NAME}
        transport: stdio
        command: '{py}'
        args: ['{script}']
        env:
          KB_BOT_ID: !!js process.env.KB_BOT_ID
          KB_INTERNAL_URL: !!js process.env.KB_INTERNAL_URL
          KB_RUN_FILE: !!js process.env.KB_RUN_FILE
          KB_LANGUAGE: !!js process.env.KB_LANGUAGE
          KB_ACTIONS: !!js process.env.KB_ACTIONS
        failOnStartupError: true
"""


@dataclass
class AgentPaths:
    root: Path
    dsh_home: Path = field(init=False)
    workspace: Path = field(init=False)
    runs: Path = field(init=False)
    patch: Path = field(init=False)

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        self.dsh_home = self.root / "dsh_home"
        self.workspace = self.root / "workspace"
        self.runs = self.root / "runs"
        self.patch = self.root / "kb.patch.yml"

    def ensure(self) -> None:
        for directory in (self.dsh_home, self.workspace, self.runs):
            directory.mkdir(parents=True, exist_ok=True)
        content = patch_yaml()
        if not self.patch.exists() or self.patch.read_text(encoding="utf-8") != content:
            self.patch.write_text(content, encoding="utf-8")

    def run_file(self, process_key: str) -> Path:
        return self.runs / f"{process_key}.run.json"


def ensure_profile(paths: AgentPaths) -> None:
    """Khởi tạo profile trong DSH_HOME MỘT LẦN trước khi có tiến trình dsh nào (nhiều tiến trình khởi tạo đồng thời dễ tranh chấp)."""
    from deepseek_harness_runtime import resolve_bundled_launch_args

    env = {**os.environ, "DSH_HOME": str(paths.dsh_home)}
    subprocess.run(
        [*resolve_bundled_launch_args(), "--profile", PROFILE, "--patch", str(paths.patch), "--dump-config"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=INIT_TIMEOUT_SECONDS, check=True,
    )


def purge_session(paths: AgentPaths, session_id: str) -> int:
    """Xóa thư mục phiên của dsh (DSH_HOME/sessions/<workspace-đã-mã-hóa>/<session_id>). Trả số thư mục đã xóa. session_id do ta sinh
    (chỉ [A-Za-z0-9-]) nên không thể trỏ ra ngoài DSH_HOME."""
    if not session_id or not all(c.isalnum() or c == "-" for c in session_id):
        return 0
    removed = 0
    sessions = paths.dsh_home / "sessions"
    if not sessions.is_dir():
        return 0
    for workspace_dir in sessions.iterdir():
        target = workspace_dir / session_id
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed += 1
    return removed


# ---------------------------------------------------------------- tiến trình dsh

class HarnessRunner:
    """1 tiến trình dsh (1 DeepSeekHarness) cho 1 process_key. KHÔNG an toàn khi gọi run() đồng thời — worker giữ khóa."""

    def __init__(self, job: protocol.AgentJob, paths: AgentPaths, api_key: str):
        self.process_key = job.process_key
        self.paths = paths
        self.job = job
        self._api_key = api_key
        self._harness = None
        self.last_used = time.monotonic()
        # progress(code): worker đặt trước mỗi lượt để báo tiến trình thật (protocol.ProgressTracker); None = không báo
        self.progress = None

    def _start(self):
        from deepseek_harness import DeepSeekHarness

        env = {
            "DSH_SYSTEM_PROMPT": self.job.persona,
            "KB_BOT_ID": str(self.job.bot_id),
            "KB_INTERNAL_URL": self.job.internal_url,
            "KB_RUN_FILE": str(self.paths.run_file(self.process_key)),
            "KB_LANGUAGE": self.job.language,
            "KB_ACTIONS": json.dumps(self.job.actions, ensure_ascii=False),  # công cụ hành động website đã duyệt (Phase M); đổi danh sách => process_key mới
            "DSH_TELEMETRY_DISABLED": "1",
        }
        self._harness = DeepSeekHarness(
            provider="deepseek-official", model=self.job.model, max_tokens=self.job.max_tokens,
            cwd=str(self.paths.workspace), dsh_home=str(self.paths.dsh_home), profile=PROFILE,
            patches=(str(self.paths.patch),), env=env, api_key=self._api_key,
            initialize_timeout_seconds=INIT_TIMEOUT_SECONDS,
        )
        self._harness.start()

    @property
    def alive(self) -> bool:
        proc = getattr(getattr(self._harness, "client", None), "_proc", None)
        return self._harness is not None and proc is not None and proc.poll() is None

    def close(self) -> None:
        harness, self._harness = self._harness, None
        if harness is not None:
            try:
                harness.close()
            except Exception:
                pass

    def run(self, job: protocol.AgentJob) -> RunOutput:
        started = time.monotonic()
        limits = job.limits
        summary = protocol.RunSummary()
        session_id = f"j-{job.job_id}"
        timed_out = threading.Event()
        timer: threading.Timer | None = None
        try:
            if not self.alive:
                self.close()
                self._start()
            self.paths.run_file(self.process_key).write_text(json.dumps({
                "run_id": job.job_id, "token": job.token, "max_search_calls": limits.max_search_calls,
                "allow_summarize": bool(job.allow_summarize), "allow_actions": bool(job.allow_actions),
                "max_action_calls": limits.max_action_calls, "action_wait_seconds": limits.action_wait_seconds,
            }), encoding="utf-8")

            def on_timeout():
                timed_out.set()
                self.close()  # đóng runtime làm run() ném lỗi thay vì treo (Session.run chờ notification không có hạn)

            timer = threading.Timer(limits.max_runtime_seconds, on_timeout)
            timer.daemon = True
            timer.start()

            tracker = protocol.ProgressTracker()

            def on_notification(notification) -> None:
                payload = notification.payload
                if notification.method != "session.event" or payload.get("sessionId") != session_id:
                    return
                event = payload.get("event")
                protocol.apply_event(summary, event)
                if self.progress is not None:
                    code = tracker.feed(event)
                    if code is not None:
                        self.progress(code)
                if summary.terminal is None:
                    if protocol.tool_calls_before_terminal(summary) > limits.max_tool_calls:
                        raise RunAborted(protocol.STATUS_MAX_TOOL_CALLS, "vượt số lần gọi công cụ cho phép")
                    # Chỉ kiểm tra khi 1 lượt suy luận KẾT THÚC (step/end): event assistant/message của lượt N đến TRƯỚC lệnh gọi công cụ
                    # kết thúc của chính lượt đó, nên đếm ở đó sẽ cắt oan lượt cuối đang định kết luận.
                    if isinstance(event, dict) and event.get("type") == "step/end" and len(summary.steps) >= limits.max_iterations:
                        raise RunAborted(protocol.STATUS_MAX_ITERATIONS, f"đã dùng {len(summary.steps)} lượt suy luận mà chưa kết luận")

            result = self._harness.run(job.input, session_id=session_id, on_notification=on_notification)
            summary = protocol.summarize_events(result.events, result.final_response, result.finish_reason)
            elapsed = time.monotonic() - started
            return RunOutput(protocol.STATUS_COMPLETED, summary, elapsed, stop_reason=result.finish_reason or "")
        except RunAborted as exc:
            self.close()  # agent có thể vẫn đang chạy: đóng cả tiến trình (chỉ chạy 1 lượt/lúc nên không ảnh hưởng ai)
            return RunOutput(exc.status, summary, time.monotonic() - started, error=str(exc), discard_process=True, stop_reason=exc.status)
        except Exception as exc:
            elapsed = time.monotonic() - started
            self.close()
            if timed_out.is_set():
                return RunOutput(protocol.STATUS_TIMEOUT, summary, elapsed, error=f"quá {limits.max_runtime_seconds:g}s", discard_process=True, stop_reason="timeout")
            return RunOutput(protocol.STATUS_FAILED, summary, elapsed, error=f"{type(exc).__name__}: {exc}", discard_process=True, stop_reason="error")
        finally:
            if timer is not None:
                timer.cancel()
            self.last_used = time.monotonic()
            purge_session(self.paths, session_id)
