"""Hợp đồng giữa 3 bên của chế độ AI Agent — phần THUẦN (không mạng, không DB, không SDK) để test từng điều khoản:

  Flask (engine)  --Redis-->  agent worker (giữ tiến trình dsh)  --stdio-->  dsh runtime  --MCP stdio-->  kb_mcp_server.py
       ^                                                                                                      |
       +--------------------------- POST /internal/rag/search (token ký, chỉ loopback) --------------------------+

Các quy ước (mỗi điều đều có test trong tests/test_agent_protocol.py):
- Tên công cụ model nhìn thấy = MCP_PREFIX + tên gốc (dsh đặt `mcp__<serverName>__<tool>`).
- Công cụ KẾT THÚC (finish_answer / ask_clarification / decline) là cách agent báo quyết định có cấu trúc; công cụ tra cứu chỉ là
  search_knowledge_base. Agent không có công cụ nào khác (shell/filesystem/web bị gỡ khỏi profile, xem harness_backend.py).
- Danh tính bot/lượt chạy KHÔNG bao giờ do model truyền: bot_id nằm trong biến môi trường của tiến trình MCP, run_id + token do
  Flask ký rồi worker ghi vào tệp ngữ cảnh mà MCP đọc. Token chỉ dùng được cho ĐÚNG (bot_id, run_id) và hết hạn nhanh.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

SERVER_NAME = "kb"
MCP_PREFIX = f"mcp__{SERVER_NAME}__"
SEARCH_TOOL = "search_knowledge_base"
SUMMARIZE_TOOL = "summarize_conversation"  # công cụ NGOÀI nhóm kết thúc: chỉ mở khi áp lực ngữ cảnh của lượt >= strong (xem AgentJob.allow_summarize)
FINISH_TOOL = "finish_answer"
CLARIFY_TOOL = "ask_clarification"
DECLINE_TOOL = "decline"
TERMINAL_TOOLS = (FINISH_TOOL, CLARIFY_TOOL, DECLINE_TOOL)
MODEL_TOOL_NAMES = (SEARCH_TOOL, SUMMARIZE_TOOL, *TERMINAL_TOOLS)
SUMMARIZE_TOOL_ALLOWANCE = 1  # số lần gọi SUMMARIZE_TOOL tối đa/lượt: cộng vào trần dừng cứng max_tool_calls (ngoài ngân sách tra cứu)

# Kết quả 1 lần agent gọi công cụ tóm tắt (route nội bộ trả, worker/MCP hiển thị, agent_executions/trace ghi lại)
SUMMARY_SUMMARIZED = "summarized"            # vừa tóm tắt xong, summary đã lưu
SUMMARY_NOTHING = "nothing_to_summarize"    # mọi tin cũ đã nằm trong summary (hoặc chưa có tin ngoài cửa sổ gần đây)
SUMMARY_BUSY = "busy"                       # việc nền đang tóm tắt đúng hội thoại này
SUMMARY_NOT_ALLOWED = "not_allowed"         # lượt này không được phép (áp lực thấp, tắt tóm tắt, không có hội thoại lưu)
SUMMARY_ALREADY_USED = "already_used"       # đã gọi 1 lần trong lượt này
SUMMARY_ERROR = "error"                     # LLM/DB lỗi (không có bản tóm tắt giả)

# Trạng thái kết thúc của 1 lượt chạy agent (cột agent_executions.status)
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_MAX_ITERATIONS = "max_iterations_reached"
STATUS_MAX_TOOL_CALLS = "max_tool_calls_reached"
STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_TIMEOUT, STATUS_MAX_ITERATIONS, STATUS_MAX_TOOL_CALLS)

TOKEN_TTL_SECONDS = 180  # token của 1 lượt: dài hơn max_runtime + thời gian chờ hàng đợi một chút, rồi vô hiệu


class Keys:
    """Khoá Redis. prefix tách môi trường (dev/test dùng chung 1 Redis, xem config.AGENT_REDIS_PREFIX)."""

    def __init__(self, prefix: str = "agent"):
        self.prefix = prefix

    @property
    def jobs(self) -> str:
        return f"{self.prefix}:jobs"

    @property
    def worker_alive(self) -> str:
        return f"{self.prefix}:worker-alive"

    def result(self, job_id: str) -> str:
        return f"{self.prefix}:result:{job_id}"

    def searches(self, run_id: str) -> str:
        return f"{self.prefix}:run:{run_id}:searches"

    def run_context(self, run_id: str) -> str:
        """Ngữ cảnh do FLASK đặt cho lượt (hội thoại nào, có được tóm tắt không). Model không truyền, không đọc/ghi được."""
        return f"{self.prefix}:run:{run_id}:context"

    def progress(self, job_id: str) -> str:
        """Danh sách mã tiến trình thật của lượt (worker rpush khi agent tra cứu/thao tác/soạn câu trả lời; Flask đọc cùng lúc chờ kết quả)."""
        return f"{self.prefix}:progress:{job_id}"

    def summary_used(self, run_id: str) -> str:
        return f"{self.prefix}:run:{run_id}:summary-used"

    def summaries(self, run_id: str) -> str:
        return f"{self.prefix}:run:{run_id}:summaries"

    def actions(self, run_id: str) -> str:
        """Bộ đếm số lần agent gọi công cụ hành động website trong lượt (Flask là nơi quyết định giới hạn, không tin bộ đếm phía MCP)."""
        return f"{self.prefix}:run:{run_id}:actions"


# ---------------------------------------------------------------- token của 1 lượt chạy

def _signature(secret: str, bot_id: int, run_id: str, expires: int) -> str:
    return hmac.new(secret.encode("utf-8"), f"{bot_id}:{run_id}:{expires}".encode("utf-8"), hashlib.sha256).hexdigest()


def sign_run_token(secret: str, bot_id: int, run_id: str, *, now: float | None = None, ttl: int = TOKEN_TTL_SECONDS) -> str:
    expires = int((time.time() if now is None else now) + ttl)
    return f"{expires}.{_signature(secret, bot_id, run_id, expires)}"


def verify_run_token(secret: str, bot_id, run_id, token, *, now: float | None = None) -> bool:
    """Đúng chữ ký cho ĐÚNG (bot_id, run_id) và chưa hết hạn. Mọi đầu vào sai kiểu/định dạng -> False (không ném lỗi)."""
    if isinstance(bot_id, bool) or not isinstance(bot_id, int) or not isinstance(run_id, str) or not run_id or not isinstance(token, str):
        return False
    expires_text, _, signature = token.partition(".")
    if not expires_text.isdigit() or not signature:
        return False
    expires = int(expires_text)
    if expires < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(signature, _signature(secret, bot_id, run_id, expires))


# ---------------------------------------------------------------- công việc gửi cho worker

@dataclass
class AgentLimits:
    max_search_calls: int = 3       # số lần gọi search_knowledge_base tối đa (MCP từ chối lần vượt, model được nhắc kết luận)
    max_tool_calls: int = 3        # trần TỔNG số lần gọi công cụ TRƯỚC khi có quyết định (vượt -> worker dừng cứng)
    max_iterations: int = 4        # số lượt suy luận (lệnh gọi LLM) tối đa
    max_runtime_seconds: float = 25.0
    max_action_calls: int = 2      # số lần gọi công cụ hành động website tối đa/lượt (Phase M) — nằm TRONG max_tool_calls
    action_wait_seconds: float = 8.0   # thời gian chờ widget báo kết quả cho mỗi lần gọi công cụ hành động

    def as_dict(self) -> dict:
        return {
            "max_search_calls": self.max_search_calls, "max_tool_calls": self.max_tool_calls,
            "max_iterations": self.max_iterations, "max_runtime_seconds": self.max_runtime_seconds,
            "max_action_calls": self.max_action_calls, "action_wait_seconds": self.action_wait_seconds,
        }


@dataclass
class AgentJob:
    job_id: str
    bot_id: int
    token: str
    persona: str            # system prompt cố định theo bot (chỉ dẫn + quy tắc + hướng dẫn công cụ) -> quyết định tiến trình dsh
    max_tokens: int         # trần token đầu ra mỗi lệnh gọi LLM (đặt lúc khởi tạo tiến trình)
    language: str
    input: str              # phần động của lượt: tóm tắt, bộ nhớ, tin gần đây, tra cứu ban đầu, câu hỏi
    internal_url: str       # URL gốc của Flask mà MCP gọi (loopback)
    model: str
    limits: AgentLimits = field(default_factory=AgentLimits)
    deadline: float = 0.0   # epoch giây; worker bỏ qua việc đã quá hạn (Flask đã ngừng chờ)
    allow_summarize: bool = False  # lượt này được gọi công cụ tóm tắt (theo lượt, KHÔNG thuộc process_key). Chỉ để MCP từ chối sớm; Flask mới là nơi quyết định
    # Công cụ hành động trên website khách (Phase M): [{"name", "description", "params": [{"name"}], "confirm": bool}]. THUỘC process_key: danh sách tool được
    # MCP quảng bá lúc tiến trình dsh khởi động, nên đổi danh sách (duyệt/bỏ duyệt hành động) => tiến trình mới.
    actions: list = field(default_factory=list)
    allow_actions: bool = False    # lượt này có hội thoại thật (widget) để thực thi hành động; chat thử thì không. Chỉ để MCP từ chối sớm — Flask quyết định

    def to_json(self) -> str:
        data = self.__dict__.copy()
        data["limits"] = self.limits.as_dict()
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "AgentJob":
        data = json.loads(text)
        limits = AgentLimits(**data.pop("limits"))
        return cls(limits=limits, **data)

    @property
    def process_key(self) -> str:
        """Hai lượt dùng chung tiến trình dsh khi và chỉ khi cùng bot + cùng persona/model/max_tokens/ngôn ngữ (đổi cấu hình bot
        => khóa mới => tiến trình mới; tiến trình cũ hết việc sẽ bị dọn)."""
        parts = [self.bot_id, self.persona, self.max_tokens, self.language, self.model, self.internal_url]
        if self.actions:  # chỉ khi có hành động: bot không dùng module giữ nguyên khóa như trước
            parts.append(self.actions)
        digest = hashlib.sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        return digest[:20]


# ---------------------------------------------------------------- tóm tắt event của dsh (thuần)

@dataclass
class LlmStep:
    """Usage của 1 lệnh gọi LLM trong lượt. inputTokens của dsh KHÔNG gồm phần đọc từ cache (đã đo: total = input + cacheRead + output)."""

    miss: int = 0
    hit: int = 0
    output: int = 0
    reasoning: int = 0

    @property
    def prompt(self) -> int:
        return self.miss + self.hit


@dataclass
class RunSummary:
    steps: list[LlmStep] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)   # [{"name": tên gốc, "arguments": dict}]
    terminal: dict | None = None                            # công cụ kết thúc GỌI SAU CÙNG: {"name", "arguments"}
    terminal_count: int = 0
    final_text: str = ""
    finish_reason: str | None = None

    @property
    def search_calls(self) -> int:
        return sum(1 for c in self.tool_calls if c["name"] == SEARCH_TOOL)

    @property
    def summarize_calls(self) -> int:
        return sum(1 for c in self.tool_calls if c["name"] == SUMMARIZE_TOOL)

    def as_dict(self) -> dict:
        return {
            "steps": [s.__dict__ for s in self.steps], "tool_calls": self.tool_calls, "terminal": self.terminal,
            "terminal_count": self.terminal_count, "final_text": self.final_text, "finish_reason": self.finish_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RunSummary":
        return cls(
            steps=[LlmStep(**{k: int(v) for k, v in s.items()}) for s in data.get("steps", [])],
            tool_calls=list(data.get("tool_calls", [])), terminal=data.get("terminal"),
            terminal_count=int(data.get("terminal_count", 0)), final_text=data.get("final_text", "") or "",
            finish_reason=data.get("finish_reason"),
        )


def _int(value) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and value >= 0 else 0


def short_tool_name(name) -> str | None:
    """'mcp__kb__finish_answer' -> 'finish_answer'; công cụ không thuộc máy chủ kb (không nên có) -> None."""
    if isinstance(name, str) and name.startswith(MCP_PREFIX):
        return name[len(MCP_PREFIX):]
    return None


def parse_tool_arguments(raw) -> dict:
    """Tham số model truyền cho công cụ: chuỗi JSON (dsh) hoặc dict; hỏng/không phải đối tượng -> {}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def usage_step(event: dict) -> LlmStep | None:
    data = event.get("data") if isinstance(event, dict) else None
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    return LlmStep(
        miss=_int(usage.get("inputTokens")), hit=_int(usage.get("cacheReadTokens")),
        output=_int(usage.get("outputTokens")), reasoning=_int(usage.get("reasoningTokens")),
    )


def apply_event(summary: RunSummary, event: dict) -> None:
    """Cộng 1 event gốc của phiên vào bản tóm tắt (dùng cả khi đếm giới hạn theo thời gian thực lẫn khi tóm tắt cuối lượt)."""
    if not isinstance(event, dict):
        return
    kind = event.get("type")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if kind == "assistant/message":
        step = usage_step(event)
        summary.steps.append(step if step is not None else LlmStep())
    elif kind == "tool/call":
        name = short_tool_name(data.get("name"))
        call = {"name": name if name is not None else str(data.get("name")), "arguments": parse_tool_arguments(data.get("arguments"))}
        summary.tool_calls.append(call)
        if name in TERMINAL_TOOLS:
            summary.terminal = call
            summary.terminal_count += 1
    elif kind == "turn/end":
        reason = data.get("reason")
        kind_value = reason.get("kind") if isinstance(reason, dict) else None
        if isinstance(kind_value, str):
            summary.finish_reason = kind_value


def summarize_events(events: list[dict], final_text: str = "", finish_reason: str | None = None) -> RunSummary:
    summary = RunSummary()
    for event in events:
        apply_event(summary, event)
    summary.final_text = final_text or ""
    if finish_reason is not None:
        summary.finish_reason = finish_reason
    return summary


# Mã tiến trình THẬT của lượt (giao diện khách dịch ra chữ theo ngôn ngữ: "Đang tra cứu tài liệu..."). Mã "analyzing" do Flask phát ngay khi nhận lượt.
PROGRESS_SEARCHING = "searching"
PROGRESS_ACTING = "acting"
PROGRESS_COMPOSING = "composing"


class ProgressTracker:
    """Đổi luồng event của phiên agent thành mã tiến trình để khách thấy agent đang làm gì (không phải chữ giả theo giờ). feed() trả mã MỚI hoặc None.
    - tool/call search_knowledge_base -> searching; công cụ hành động website -> acting; công cụ kết thúc -> composing.
    - step/end sau khi đã tra cứu mà chưa kết thúc -> composing (lượt suy luận kế tiếp là lượt soạn câu trả lời).
    Không trả lại đúng mã vừa phát (agent gọi tra cứu nhiều lần vẫn chỉ 1 dòng "đang tra cứu")."""

    def __init__(self):
        self._searched = False
        self._finishing = False
        self._last: str | None = None

    def feed(self, event) -> str | None:
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        code = None
        if kind == "tool/call":
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            name = short_tool_name(data.get("name"))
            if name == SEARCH_TOOL:
                self._searched, code = True, PROGRESS_SEARCHING
            elif name in TERMINAL_TOOLS:
                self._finishing, code = True, PROGRESS_COMPOSING
            elif name is not None and name != SUMMARIZE_TOOL:
                code = PROGRESS_ACTING
        elif kind == "step/end" and self._searched and not self._finishing:
            code = PROGRESS_COMPOSING
        if code is None or code == self._last:
            return None
        self._last = code
        return code


def tool_calls_before_terminal(summary: RunSummary) -> int:
    """Số lần gọi công cụ (không tính công cụ kết thúc) — đối tượng của max_tool_calls."""
    return sum(1 for call in summary.tool_calls if call["name"] not in TERMINAL_TOOLS)
