"""Phía Flask của chế độ AI Agent: dựng nội dung cho agent, giao việc cho worker qua Redis, đọc kết quả và đổi về `StructuredOutput` để
cây quyết định (decision.py) chạy TIẾP TRÊN đầu ra của agent — nhờ vậy các chốt an toàn của engine vẫn còn nguyên (không có tài liệu liên
quan -> câu chủ bot cấu hình, ngưỡng ý định/slot, hết hạn hỏi làm rõ...) bất kể agent quyết định gì.

Engine chỉ đổi đúng Bước B (lệnh gọi LLM): xem engine.run_turn(agent_runner=...). Bước A (truy xuất + Context Builder nén) vẫn chạy và
kết quả tra cứu BAN ĐẦU được đưa sẵn cho agent; agent chỉ tra cứu THÊM khi thấy chưa đủ. Lỗi/hết giờ KHÔNG bị nuốt: ném AgentRunError /
AgentUnavailableError để route trả 502 như mọi lỗi LLM (không tạo câu trả lời giả).
"""
from __future__ import annotations

import dataclasses
import json
import math
import time
import uuid
from dataclasses import dataclass, field

from core import rag_engine
from core.context_engine import builder
from core.context_engine.agent import protocol
from core.context_engine.cost import LLMUsageTracker, Usage
from core.context_engine.prompts import texts
from core.context_engine.settings import JSON_OVERHEAD_TOKENS, EngineSettings
from core.context_engine.state import IntentSpec
from core.context_engine.structured import StructuredOutput

SUMMARIZE_LEVELS = ("strong", "hard")  # mức áp lực ngữ cảnh (builder.pressure_level, TRƯỚC khi nén) mà agent được phép tự tóm tắt
CONTEXT_TTL_MARGIN_SECONDS = 30
HARD_TOOL_MARGIN = 2  # cùng ý nghĩa với harness_backend.HARD_TOOL_MARGIN (không import để Flask không nạp SDK/subprocess)
WORKER_START_ALLOWANCE_SECONDS = 20  # + max_runtime: thời gian chờ tối đa (khởi động tiến trình dsh ~1-6s, xếp hàng sau lượt khác)


class AgentUnavailableError(RuntimeError):
    """Chưa có worker agent đang chạy (hoặc Redis không nhận việc)."""


class AgentRunError(RuntimeError):
    """Lượt chạy agent lỗi/hết giờ. `info` là bản ghi để lưu vào agent_executions (kể cả lượt lỗi)."""

    def __init__(self, message: str, info: dict):
        super().__init__(message)
        self.info = info


@dataclass
class AgentResult:
    output: StructuredOutput
    info: dict                                     # bản ghi agent_executions + trace
    searches: list[dict] = field(default_factory=list)   # các lần agent tự tra cứu thêm (từ route nội bộ)


# ---------------------------------------------------------------- dựng nội dung cho agent

def render_persona(settings: EngineSettings, intents: list[IntentSpec], actions: list[dict] | None = None) -> str:
    """System prompt CỐ ĐỊNH theo bot (đổi cấu hình bot => process_key mới => tiến trình dsh mới). Không chứa dữ liệu của lượt.
    actions: công cụ hành động website đã duyệt (Phase M) — mô tả lấy nguyên văn từ module_actions.description."""
    t = texts(settings.language)
    parts = [settings.instructions] if settings.instructions else []
    parts.append(t["rules"])
    parts.append(t["agent_rules"])
    if actions:
        parts.append(t["actions_header"] + "\n" + "\n".join(f"- {a['name']}: {a['description']}" for a in actions) + "\n" + t["actions_rules"])
    if intents:
        lines = [
            t["intent_line"].format(
                name=i.name, description=i.description or i.name,
                required=", ".join(i.required) or t["none"], optional=", ".join(i.optional) or t["none"],
            )
            for i in intents
        ]
        parts.append(t["intents_header"] + "\n" + "\n".join(lines))
    return "\n\n".join(parts)


def summarize_allowed(settings: EngineSettings, conversation_id: int | None, pressure_level: str) -> bool:
    """Lượt này có được gọi summarize_conversation không: có hội thoại đã lưu (khung chat thử thì không), tóm tắt đang bật, và Context Builder
    báo áp lực >= nén mạnh. Job nền vẫn là lưới an toàn cho mọi trường hợp còn lại."""
    return conversation_id is not None and settings.summary_enabled and pressure_level in SUMMARIZE_LEVELS


def render_input(plan: builder.ContextPlan, *, summarize_hint: bool = False) -> str:
    """Phần ĐỘNG của lượt (sau khi Context Builder đã nén): tóm tắt, bộ nhớ, tin gần đây, tra cứu ban đầu, câu hỏi.
    summarize_hint: nhắc agent rằng hội thoại đã dài và có công cụ tóm tắt (chỉ khi lượt được phép, xem summarize_allowed)."""
    t = texts(plan.settings.language)
    sections = [plan.summary_block, plan.memory_block]
    if plan.recent:
        label = {"user": t["customer"], "assistant": t["bot"]}
        sections.append(t["agent_recent"] + "\n" + "\n".join(f"{label[turn.role]}: {turn.content}" for turn in plan.recent))
    sections.append(f"{t['agent_initial']}\n{builder.render_rag_text(plan.passages) or t['no_context']}")
    if summarize_hint:
        sections.append(t["agent_summarize_hint"])
    question = f"{t['agent_question']}\n{plan.question}"
    if plan.scope_narrowing:
        question += "\n\n" + t["scope_note"].replace("proposed_answer", "finish_answer").replace("proposed_clarification_question", "ask_clarification")
    sections.append(question)
    sections.append(t["agent_reminder"])
    return "\n\n".join(s for s in sections if s)


# ---------------------------------------------------------------- đầu ra của agent -> StructuredOutput

def _clean_unit(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or not 0.0 <= value <= 1.0:
        return None
    return float(value)


def _clean_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def outcome_to_structured(summary: protocol.RunSummary) -> tuple[StructuredOutput, list[str]]:
    """Công cụ kết thúc gọi SAU CÙNG -> StructuredOutput. Trả (output, các vấn đề đã gặp — đưa vào decision_trace, không giấu):
    - finish_answer -> proposed_answer; ask_clarification -> proposed_clarification_question; decline -> cả hai rỗng (cây quyết định
      sẽ ra DECLINE bằng câu chủ bot cấu hình);
    - không gọi công cụ kết thúc mà có văn bản cuối -> dùng văn bản đó làm câu trả lời (vẫn qua cây quyết định), ghi issue;
    - không có gì -> cả hai rỗng (DECLINE).
    Trường phụ (intent, độ chắc chắn, slots) sai kiểu thì bỏ trường đó và ghi issue thay vì làm hỏng cả câu trả lời."""
    issues: list[str] = []
    terminal = summary.terminal
    args = terminal["arguments"] if terminal else {}
    name = terminal["name"] if terminal else None
    if summary.terminal_count > 1:
        issues.append("multiple_terminal_calls")

    intent = args.get("intent")
    if intent is not None and not (isinstance(intent, str) and intent.strip()):
        issues.append("invalid_intent")
        intent = None
    slots = args.get("slots")
    if slots is not None and not isinstance(slots, dict):
        issues.append("invalid_slots")
    confidence = _clean_unit(args.get("intent_confidence"))
    if args.get("intent_confidence") is not None and confidence is None:
        issues.append("invalid_intent_confidence")
    self_confidence = _clean_unit(args.get("self_assessed_confidence"))
    if args.get("self_assessed_confidence") is not None and self_confidence is None:
        issues.append("invalid_self_assessed_confidence")

    answer = clarification = ""
    if name == protocol.FINISH_TOOL:
        answer = _clean_text(args.get("answer"))
    elif name == protocol.CLARIFY_TOOL:
        clarification = _clean_text(args.get("question"))
    elif name is None:
        answer = _clean_text(summary.final_text)
        issues.append("no_terminal_tool" if answer else "no_output")

    output = StructuredOutput(
        intent=intent.strip() if isinstance(intent, str) else None,
        intent_confidence=confidence,
        slots={str(k): v for k, v in slots.items()} if isinstance(slots, dict) else {},
        memory_updates=[],
        needs_history_lookup=False,
        self_assessed_confidence=self_confidence,
        proposed_answer=answer,
        proposed_clarification_question=clarification,
        raw=json.dumps({"terminal": name}, ensure_ascii=False),
    )
    return output, issues


def merge_retrieval(base: rag_engine.RetrievalResult, searches: list[dict]) -> rag_engine.RetrievalResult:
    """Tín hiệu truy xuất HIỆU LỰC của lượt = lần tra cứu tốt nhất trong (tra cứu ban đầu + các lần agent tự tra cứu thêm): nhiều ứng
    viên nhất, hòa thì khoảng cách gần nhất. Nhờ vậy agent tìm ra tài liệu bằng câu tìm tốt hơn thì không bị nhánh "không có ngữ cảnh"
    từ chối oan; ngược lại không tìm được gì ở MỌI lần thì vẫn bị từ chối."""
    best = base
    for search in searches:
        candidates = int(search.get("candidate_count") or 0)
        top = search.get("top_distance")
        better = candidates > best.candidate_count or (
            candidates == best.candidate_count and candidates > 0 and top is not None
            and (best.top_distance is None or top < best.top_distance)
        )
        if better:
            best = dataclasses.replace(
                base, candidate_count=candidates, top_distance=top, second_distance=None,
                distance_gap=search.get("distance_gap"), knowledge_empty=False,
            )
    return best


_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens", "cache_miss_tokens")


def usage_of(step: protocol.LlmStep) -> Usage:
    return Usage(step.prompt, step.output, step.prompt + step.output, step.hit, step.miss)


# ---------------------------------------------------------------- giao việc cho worker

class AgentRunner:
    def __init__(self, redis_client, keys: protocol.Keys, secret: str, *, model: str, internal_url: str, limits: protocol.AgentLimits,
                 clock=time.time, id_factory=lambda: uuid.uuid4().hex, action_provider=None):
        self.redis = redis_client
        self.keys = keys
        self.secret = secret
        self.model = model
        self.internal_url = internal_url
        self.limits = limits
        self.clock = clock
        self.new_id = id_factory
        # action_provider(bot_id) -> [{"name","description","params","confirm"}]: hành động website ĐÃ DUYỆT của bot (Phase M). Runtime chỉ nhận danh sách
        # qua callback nên không phụ thuộc DB/Flask; None = bot không có công cụ hành động.
        self.action_provider = action_provider
        # progress_sink(code): nhận mã tiến trình THẬT của lượt (protocol.PROGRESS_*) ngay khi worker báo, trong lúc chờ kết quả. Đặt theo từng lượt
        # (dashboard.service.reply_to_customer); None = không ai cần xem tiến trình.
        self.progress_sink = None

    @classmethod
    def from_config(cls, config=None) -> "AgentRunner":
        from config import Config
        from extensions import redis_client

        cfg = config or Config
        limits = protocol.AgentLimits(
            max_search_calls=cfg.AGENT_MAX_TOOL_CALLS, max_tool_calls=cfg.AGENT_MAX_TOOL_CALLS + HARD_TOOL_MARGIN + protocol.SUMMARIZE_TOOL_ALLOWANCE,
            max_iterations=cfg.AGENT_MAX_ITERATIONS, max_runtime_seconds=cfg.AGENT_MAX_RUNTIME_SECONDS,
            max_action_calls=cfg.AGENT_MAX_ACTION_CALLS, action_wait_seconds=cfg.AGENT_ACTION_WAIT_SECONDS,
        )
        return cls(redis_client, protocol.Keys(cfg.AGENT_REDIS_PREFIX), cfg.SECRET_KEY, model=cfg.AGENT_MODEL,
                   internal_url=cfg.AGENT_INTERNAL_URL, limits=limits)

    def _info(self, job_id: str, status: str, summary: protocol.RunSummary, started: float, *, stop_reason: str = "", error: str | None = None,
              runtime: float | None = None) -> dict:
        return {
            "execution_id": job_id, "status": status,
            "iterations_used": len(summary.steps), "total_llm_calls": len(summary.steps),
            "tool_calls_used": len(summary.tool_calls), "search_calls": summary.search_calls,
            "tools": [c["name"] for c in summary.tool_calls],
            "terminal_tool": summary.terminal["name"] if summary.terminal else None,
            "stop_reason": stop_reason or (summary.finish_reason or ""), "error": error,
            "started_at": started, "finished_at": self.clock(),
            "runtime_seconds": runtime if runtime is not None else self.clock() - started,
        }

    def run(self, *, bot_id: int, settings: EngineSettings, intents: list[IntentSpec], plan: builder.ContextPlan,
            tracker: LLMUsageTracker, conversation_id: int | None = None, pressure_level: str = "normal") -> AgentResult:
        started = self.clock()
        if not self.redis.exists(self.keys.worker_alive):
            raise AgentUnavailableError("AI Agent đang bật nhưng chưa có worker chạy (python -m workers.agent_worker)")
        job_id = self.new_id()
        allow_summarize = summarize_allowed(settings, conversation_id, pressure_level)
        actions = list(self.action_provider(bot_id)) if self.action_provider is not None else []
        limits = self.limits
        if actions:  # bot có công cụ hành động: cộng số lần gọi được phép vào trần dừng cứng, và thời gian chờ widget báo kết quả vào trần thời gian của lượt
            limits = dataclasses.replace(
                limits, max_tool_calls=limits.max_tool_calls + limits.max_action_calls,
                max_runtime_seconds=limits.max_runtime_seconds + limits.max_action_calls * (limits.action_wait_seconds + 1),
            )
        wait = limits.max_runtime_seconds + WORKER_START_ALLOWANCE_SECONDS
        # Ngữ cảnh do FLASK giữ (model không truyền được): hội thoại nào + có được tóm tắt không. Đặt TRƯỚC khi giao việc cho worker.
        self.redis.set(self.keys.run_context(job_id), json.dumps({
            "bot_id": bot_id, "conversation_id": conversation_id, "summarize_allowed": allow_summarize,
        }), ex=int(wait) + CONTEXT_TTL_MARGIN_SECONDS)
        try:
            return self._run(job_id, started, wait, bot_id, settings, intents, plan, tracker, allow_summarize, actions, limits, conversation_id is not None)
        finally:
            self.redis.delete(self.keys.run_context(job_id), self.keys.summary_used(job_id), self.keys.progress(job_id))

    def _run(self, job_id: str, started: float, wait: float, bot_id: int, settings: EngineSettings, intents: list[IntentSpec],
             plan: builder.ContextPlan, tracker: LLMUsageTracker, allow_summarize: bool, actions: list[dict], limits: protocol.AgentLimits,
             has_conversation: bool) -> AgentResult:
        job = protocol.AgentJob(
            job_id=job_id, bot_id=bot_id, token=protocol.sign_run_token(self.secret, bot_id, job_id, now=started),
            persona=render_persona(settings, intents, actions), max_tokens=settings.max_tokens + JSON_OVERHEAD_TOKENS, language=settings.language,
            input=render_input(plan, summarize_hint=allow_summarize), internal_url=self.internal_url, model=self.model, limits=limits,
            deadline=started + wait, allow_summarize=allow_summarize, actions=actions, allow_actions=bool(actions) and has_conversation,
        )
        self.redis.rpush(self.keys.jobs, job.to_json())
        popped = self._wait_result(job_id, wait)
        if popped is None:
            info = self._info(job_id, protocol.STATUS_TIMEOUT, protocol.RunSummary(), started, stop_reason="worker_no_reply", error="worker không trả kết quả kịp")
            info["summaries"] = self._take_summaries(job_id, tracker)
            info["usage_calls"] = list(tracker.calls)
            raise AgentRunError("AI Agent không trả kết quả kịp", info)
        result = json.loads(popped[1])
        summary = protocol.RunSummary.from_dict(result.get("summary") or {})
        status = result.get("status")
        info = self._info(job_id, status if status in protocol.STATUSES else protocol.STATUS_FAILED, summary, started,
                          stop_reason=result.get("stop_reason") or "", error=result.get("error"), runtime=result.get("runtime_seconds"))
        for step in summary.steps:  # mỗi lệnh gọi LLM của agent là 1 lệnh gọi chính (usage cộng dồn vào Message.usage_*)
            tracker.record("main", usage_of(step), reasoning_tokens=step.reasoning)
        # Lệnh gọi tóm tắt do công cụ kích hoạt cũng tốn tiền của lượt này: ghi cả khi lượt sau đó lỗi (info vẫn mang trạng thái)
        info["summaries"] = self._take_summaries(job_id, tracker)
        if info["status"] in (protocol.STATUS_FAILED, protocol.STATUS_TIMEOUT):
            info["usage_calls"] = list(tracker.calls)  # engine không trả tracker khi lỗi: mang usage đã đo theo info để Phase D vẫn tính phí phần đã tốn
            raise AgentRunError(f"AI Agent {info['status']}: {info['error'] or info['stop_reason']}", info)

        searches = self._take_searches(job_id)
        output, issues = outcome_to_structured(summary)
        info["issues"] = issues
        info["searches"] = [{"query": s.get("query"), "candidate_count": s.get("candidate_count")} for s in searches]
        return AgentResult(output, info, searches)

    def _wait_result(self, job_id: str, wait: float):
        """Chờ kết quả của lượt tối đa `wait` giây; trong lúc chờ chuyển các mã tiến trình worker báo cho progress_sink. Trả (khóa, giá trị) của kết
        quả hoặc None nếu quá hạn. BLPOP nhiều khóa ưu tiên khóa đứng trước: kết quả đã có thì lấy ngay, mã tiến trình còn sót không còn ý nghĩa."""
        result_key, progress_key = self.keys.result(job_id), self.keys.progress(job_id)
        end = time.monotonic() + max(1, int(wait))
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return None
            popped = self.redis.blpop([result_key, progress_key], timeout=max(1, math.ceil(remaining)))
            if popped is None:
                return None
            if popped[0] == result_key:
                return popped
            if self.progress_sink is not None:
                self.progress_sink(popped[1])

    def _take_summaries(self, job_id: str, tracker: LLMUsageTracker) -> list[str]:
        """Kết quả các lần agent gọi công cụ tóm tắt (route nội bộ ghi vào Redis). Trả danh sách trạng thái; usage của lệnh gọi LLM tóm tắt
        được ghi vào tracker (kind "summary" -> extra_calls, tách khỏi lệnh gọi chính)."""
        key = self.keys.summaries(job_id)
        raw = self.redis.lrange(key, 0, -1)
        self.redis.delete(key)
        statuses = []
        for item in raw:
            try:
                value = json.loads(item)
            except ValueError:
                continue
            if not isinstance(value, dict):
                continue
            statuses.append(str(value.get("status")))
            for call in value.get("calls") or []:
                if isinstance(call, dict):
                    extra = {k: v for k, v in call.items() if k not in _USAGE_FIELDS and k != "kind"}
                    tracker.record("summary", Usage(*(call.get(f) for f in _USAGE_FIELDS)), **extra)
        return statuses

    def _take_searches(self, job_id: str) -> list[dict]:
        key = self.keys.searches(job_id)
        raw = self.redis.lrange(key, 0, -1)
        self.redis.delete(key)
        searches = []
        for item in raw:
            try:
                value = json.loads(item)
            except ValueError:
                continue
            if isinstance(value, dict):
                searches.append(value)
        return searches
