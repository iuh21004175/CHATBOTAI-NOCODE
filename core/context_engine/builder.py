"""Phase 2 — Context Builder: chọn tin gần đây theo token, tính ngân sách, đo áp lực ngữ cảnh, nén, dựng message theo role.

Mọi phép đếm token dùng tokenizer của model embedding (rag_engine.count_tokens_many, đẩy qua run_blocking) — chỉ là
ƯỚC LƯỢNG, không phải số token DeepSeek tính tiền (số thật đọc từ usage, xem cost.py).

Bố cục message (phần tĩnh lên đầu để tận dụng Context Caching theo prefix của DeepSeek; đã đo lượt 2 cùng prefix hit 2304/2478 token):
  [system: chỉ dẫn bot + quy tắc + hợp đồng JSON + intent  |  summary + memory (đổi chậm)]
  [user/assistant xen kẽ: tin gần đây]
  [user cuối: ngữ cảnh RAG + câu hỏi + nhắc định dạng]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import rag_engine
from core.context_engine.prompts import JSON_EXAMPLE, texts
from core.context_engine.settings import JSON_OVERHEAD_TOKENS, EngineSettings
from core.context_engine.state import ConversationSnapshot, IntentSpec, MemoryItem

LIGHT_PRESSURE = 0.60  # dưới mức này: bình thường
PASSAGE_SEPARATOR = rag_engine.PASSAGE_SEPARATOR
MIN_RECENT_AFTER_COMPRESSION = 2  # luôn giữ lượt trao đổi gần nhất (câu hỏi bot vừa hỏi + trả lời của khách)


# ---------------------------------------------------------------- tin gần đây

@dataclass
class ChatTurn:
    role: str  # "user" | "assistant"
    content: str
    tokens: int
    ids: tuple[int, ...] = ()  # id các tin đã gộp vào lượt này (để Historical Retrieval loại tin đã có trong prompt)


@dataclass(frozen=True)
class RecentRow:
    id: int
    sender: str  # customer | bot (tin nhân viên đã bị loại từ trước)
    content: str


def _merge_same_role(turns: list[ChatTurn]) -> list[ChatTurn]:
    merged: list[ChatTurn] = []
    for turn in turns:
        if merged and merged[-1].role == turn.role:
            merged[-1] = ChatTurn(
                turn.role, merged[-1].content + "\n" + turn.content, merged[-1].tokens + turn.tokens + 1, merged[-1].ids + turn.ids
            )
        else:
            merged.append(turn)
    return merged


class RecentMessageSelector:
    """Lấy tối đa recent_message_limit tin gần nhất, dừng sớm khi tổng token vượt recent_token_limit (tin làm vượt
    không được lấy, kể cả tin gần nhất). Thay hẳn cơ chế HISTORY_MESSAGES/HISTORY_CHARS cũ (cắt ký tự)."""

    def __init__(self, limit: int, token_limit: int):
        self.limit = limit
        self.token_limit = token_limit

    def select(self, rows: list[RecentRow]) -> list[ChatTurn]:
        """rows theo thứ tự cũ -> mới. Trả các lượt (role đã gộp nếu 2 tin liền cùng vai) cũ -> mới."""
        rows = [r for r in rows if r.content and r.content.strip()][-self.limit:]
        counts = rag_engine.count_tokens_many([r.content for r in rows])
        chosen: list[ChatTurn] = []
        used = 0
        for row, tokens in zip(reversed(rows), reversed(counts)):
            if used + tokens > self.token_limit:
                break
            used += tokens
            chosen.append(ChatTurn("assistant" if row.sender == "bot" else "user", row.content.strip(), tokens, (row.id,)))
        chosen.reverse()
        return _merge_same_role(chosen)


# ---------------------------------------------------------------- render văn bản

def render_system_static(settings: EngineSettings, intents: list[IntentSpec]) -> str:
    t = texts(settings.language)
    parts = [settings.instructions] if settings.instructions else []
    parts.append(t["rules"])
    parts.append(t["contract"].format(example=JSON_EXAMPLE, max_tokens=settings.max_tokens))
    if intents:
        lines = [
            t["intent_line"].format(
                name=i.name,
                description=i.description or i.name,
                required=", ".join(i.required) or t["none"],
                optional=", ".join(i.optional) or t["none"],
            )
            for i in intents
        ]
        parts.append(t["intents_header"] + "\n" + "\n".join(lines))
    return "\n\n".join(parts)


def render_summary_block(summary: str | None, language: str) -> str:
    return f"{texts(language)['summary_header']}\n{summary.strip()}" if summary and summary.strip() else ""


def render_memory_block(memory: list[MemoryItem], language: str) -> str:
    if not memory:
        return ""
    lines = [f"- [{m.category}] {m.key}: {m.value}" for m in memory]
    return f"{texts(language)['memory_header']}\n" + "\n".join(lines)


def render_rag_text(passages: list[dict]) -> str:
    return PASSAGE_SEPARATOR.join(p["content"] for p in passages)


def render_final_user(
    question: str, rag_text: str, language: str, *, history_context: str | None = None, narrowing: bool = False
) -> str:
    t = texts(language)
    parts = [f"{t['context']}\n{rag_text or t['no_context']}"]
    if history_context:
        parts.append(f"{t['history_context']}\n{history_context}")
    parts.append(f"{t['question']} {question}")
    if narrowing:  # thông tin tham khảo chỉ là phần mở đầu của từng đoạn -> yêu cầu hỏi thu hẹp phạm vi, chưa trả lời nội dung
        parts.append(t["scope_note"])
    parts.append(t["history_reminder"] if history_context else t["reminder"])
    return "\n\n".join(parts)


# ---------------------------------------------------------------- kế hoạch ngữ cảnh + ngân sách

@dataclass
class ContextPlan:
    settings: EngineSettings
    system_static: str
    summary_block: str
    memory_block: str
    recent: list[ChatTurn]
    passages: list[dict]  # đã cắt theo ngân sách RAG
    question: str
    system_tokens: int = 0
    summary_tokens: int = 0
    memory_tokens: int = 0
    scaffold_tokens: int = 0  # câu hỏi + nhãn + nhắc định dạng (phần tin nhắn cuối không tính RAG)
    rag_tokens: int = 0
    recent_initial: int = 0  # số lượt lịch sử trước khi nén (đặt trong compress) — mốc "một nửa" của bước 4
    scope_narrowing: bool = False  # True: các chunk tìm được vượt ngân sách nên chỉ đưa phần mở đầu của từng chunk (xem refit_rag)
    overflow_info: dict | None = None  # số liệu vượt ngân sách để ghi vào decision_trace
    compression_steps: list[str] = field(default_factory=list)

    @property
    def recent_tokens(self) -> int:
        return sum(t.tokens for t in self.recent)

    @property
    def input_tokens(self) -> int:
        return (
            self.system_tokens + self.summary_tokens + self.memory_tokens
            + self.recent_tokens + self.rag_tokens + self.scaffold_tokens
        )

    @property
    def pressure(self) -> float:
        return self.input_tokens / self.settings.max_context_tokens

    def recount_rag(self) -> None:
        text = render_rag_text(self.passages)
        self.rag_tokens = rag_engine.count_tokens_many([text])[0] if text else 0


class ContextBudgetManager:
    @staticmethod
    def output_reserve(settings: EngineSettings) -> int:
        return settings.max_tokens + JSON_OVERHEAD_TOKENS

    @classmethod
    def available_rag_tokens(cls, plan_without_rag: ContextPlan) -> int:
        """AVAILABLE_RAG_TOKENS = max_context - system - memory - summary - recent - question - output_reserve
        (có thể âm khi các phần khác đã ăn hết ngân sách)."""
        p = plan_without_rag
        return (
            p.settings.max_context_tokens
            - p.system_tokens - p.memory_tokens - p.summary_tokens - p.recent_tokens - p.scaffold_tokens
            - cls.output_reserve(p.settings)
        )

    @staticmethod
    def rag_budget(settings: EngineSettings, available: int) -> int:
        """Trần token cho RAG: min(AVAILABLE_RAG_TOKENS, rag_max_context_tokens); không bao giờ âm."""
        return max(0, min(available, settings.rag_max_context_tokens))


def pressure_level(pressure: float, settings: EngineSettings) -> str:
    """normal (<0.60) | light (0.60-warning) | strong (warning-hard_limit) | hard (>hard_limit)."""
    if pressure > settings.context_pressure_hard_limit:
        return "hard"
    if pressure >= settings.context_pressure_warning:
        return "strong"
    if pressure >= LIGHT_PRESSURE:
        return "light"
    return "normal"


def build_plan(
    settings: EngineSettings,
    intents: list[IntentSpec],
    snapshot: ConversationSnapshot,
    recent: list[ChatTurn],
    retrieved_passages: list[dict],
    question: str,
    *,
    allow_scope_narrowing: bool = False,
) -> ContextPlan:
    """Dựng kế hoạch: đếm token các phần tĩnh/động (1 lần run_blocking), tính AVAILABLE_RAG_TOKENS rồi cắt RAG.
    allow_scope_narrowing: cho phép chế độ "thu hẹp phạm vi" khi chunk tìm được vượt ngân sách (xem refit_rag)."""
    plan = ContextPlan(
        settings=settings,
        system_static=render_system_static(settings, intents),
        summary_block=render_summary_block(snapshot.summary, settings.language) if settings.summary_enabled else "",
        memory_block=render_memory_block(snapshot.memory, settings.language) if settings.structured_memory_enabled else "",
        recent=list(recent),
        passages=[],
        question=question,
    )
    scaffold = render_final_user(question, "", settings.language)
    counts = rag_engine.count_tokens_many([plan.system_static, plan.summary_block, plan.memory_block, scaffold])
    plan.system_tokens, plan.summary_tokens, plan.memory_tokens, plan.scaffold_tokens = counts
    refit_rag(plan, retrieved_passages, allow_scope_narrowing=allow_scope_narrowing)
    return plan


def refit_rag(plan: ContextPlan, retrieved_passages: list[dict], *, allow_scope_narrowing: bool = False) -> None:
    """Cắt (lại) ngữ cảnh RAG theo AVAILABLE_RAG_TOKENS hiện tại của plan. Gọi lại sau khi nén lịch sử giải phóng chỗ
    mà lần cắt đầu không còn ngân sách nào cho RAG.

    Ngân sách = "Ngân sách token cho thông tin tra cứu" = min(AVAILABLE_RAG_TOKENS, rag_max_context_tokens). Tổng token các chunk
    tìm được:
    - vừa ngân sách  -> đưa nguyên văn (chunk lân cận thêm nếu còn chỗ), bot trả lời trực tiếp;
    - vượt ngân sách và allow_scope_narrowing -> chế độ THU HẸP PHẠM VI: đưa phần mở đầu của TỪNG chunk (tổng <= ngân sách) và
      bảo LLM đặt 1 câu hỏi thu hẹp; lượt sau khách nêu rõ hơn thì tìm lại, tới khi vừa ngân sách thì trả lời;
    - vượt ngân sách nhưng không được hỏi (hết lượt/tắt hỏi làm rõ) -> giữ các chunk liên quan nhất còn vừa ngân sách."""
    budget = ContextBudgetManager.rag_budget(plan.settings, ContextBudgetManager.available_rag_tokens(plan))
    plan.scope_narrowing, plan.overflow_info = False, None
    if not retrieved_passages or budget <= 0:
        plan.passages = []
    else:
        found_tokens, found_chunks = rag_engine.hit_tokens(retrieved_passages)
        if allow_scope_narrowing and found_tokens > budget:
            plan.passages = rag_engine.excerpt_passages(retrieved_passages, budget)
            plan.scope_narrowing = True
            plan.overflow_info = {
                "budget": budget, "found_tokens": found_tokens, "found_chunks": found_chunks,
                "shown_chunks": len(plan.passages), "truncated_chunks": sum(1 for p in plan.passages if p["chunks"][0].get("truncated")),
            }
        else:
            plan.passages = rag_engine.fit_passages_to_budget(retrieved_passages, budget)[0]
    plan.recount_rag()


# ---------------------------------------------------------------- nén ngữ cảnh

def _normalize_block(text: str) -> str:
    return " ".join(text.lower().split())


def _dedupe_repeated_blocks(plan: ContextPlan) -> bool:
    """Bước 1: bỏ đoạn văn lặp lại (overlap giữa các chunk liền nhau làm cùng 1 đoạn xuất hiện 2 lần)."""
    seen: set[str] = set()
    changed = False
    for passage in plan.passages:
        chunks = []
        for chunk in passage["chunks"]:
            blocks = []
            for block in re.split(r"\n{2,}", chunk["content"]):
                key = _normalize_block(block)
                if not key or key in seen:
                    changed = changed or bool(key)
                    continue
                seen.add(key)
                blocks.append(block)
            if blocks:
                chunks.append({**chunk, "content": "\n\n".join(blocks)})
        passage["chunks"] = chunks
        passage["content"] = "\n\n".join(c["content"] for c in chunks)
    plan.passages = [p for p in plan.passages if p["chunks"]]
    return changed


def _drop_lowest_passage(plan: ContextPlan) -> bool:
    """Bước 2: bỏ đoạn kém liên quan nhất (đoạn cuối — danh sách đã sắp tốt nhất trước), luôn giữ ít nhất 1 đoạn."""
    if len(plan.passages) <= 1:
        return False
    plan.passages.pop()
    return True


def _compress_passage_content(plan: ContextPlan) -> bool:
    """Bước 3: chỉ giữ chunk TRÚNG (bỏ chunk lân cận kéo thêm) và gọn khoảng trắng/dòng trống thừa."""
    changed = False
    for passage in plan.passages:
        chunks = [c for c in passage["chunks"] if c["hit"]] or passage["chunks"]
        tidy = []
        for c in chunks:
            content = re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", c["content"])).strip()
            tidy.append({**c, "content": content})
        content = "\n\n".join(c["content"] for c in tidy)
        if content != passage["content"]:
            changed = True
        passage["chunks"], passage["content"] = tidy, content
    return changed


def _reduce_history(plan: ContextPlan) -> bool:
    """Bước 4: bỏ dần tin cũ nhất, tối đa tới còn MỘT NỬA số tin ban đầu (không dưới MIN_RECENT_AFTER_COMPRESSION).
    Phần còn lại chỉ bỏ ở bước 5 khi có summary thay thế — nếu bước 4 tự chạy tới mức tối thiểu thì bước 5 vô nghĩa
    và lịch sử bị mất mà không có gì bù."""
    floor = max(MIN_RECENT_AFTER_COMPRESSION, (plan.recent_initial + 1) // 2)
    if len(plan.recent) <= floor:
        return False
    plan.recent.pop(0)
    return True


def _use_summary_instead_of_history(plan: ContextPlan) -> bool:
    """Bước 5: có summary thì bỏ toàn bộ tin thô, chỉ giữ lượt trao đổi gần nhất; không có summary thì không làm
    (bỏ hết lịch sử mà không có gì thay thế là mất ngữ cảnh)."""
    if not plan.summary_block or len(plan.recent) <= MIN_RECENT_AFTER_COMPRESSION:
        return False
    del plan.recent[:-MIN_RECENT_AFTER_COMPRESSION]
    return True


# (tên, hàm, lặp tới khi hết tác dụng?, thay đổi RAG?) — đúng thứ tự ưu tiên của đặc tả
_STEPS = [
    ("dedupe_repeated_blocks", _dedupe_repeated_blocks, False, True),
    ("drop_low_score_chunks", _drop_lowest_passage, True, True),
    ("compress_chunk_content", _compress_passage_content, False, True),
    ("reduce_history_messages", _reduce_history, True, False),
    ("use_summary_instead_of_recent", _use_summary_instead_of_history, False, False),
]
_LIGHT_STEPS = 3  # mức "nên nén bớt": chỉ phần nội dung RAG, chưa đụng tới lịch sử hội thoại


def ensure_rag_room(plan: ContextPlan, retrieved_passages: list[dict]) -> bool:
    """Có ứng viên đạt ngưỡng nhưng ngân sách đã cạn nên KHÔNG đoạn nào vào được prompt: nhường chỗ cho tài liệu bằng cách
    bỏ dần tin lịch sử cũ nhất (tới MIN_RECENT_AFTER_COMPRESSION) rồi cắt lại RAG. Tài liệu quan trọng hơn tin cũ, và bot
    KHÔNG được hỏi lại khách chỉ vì ngữ cảnh lớn (đặc tả mục 15). Trả True nếu sau cùng đã có ít nhất 1 đoạn."""
    if not retrieved_passages or plan.passages:
        return bool(plan.passages)
    refit_rag(plan, retrieved_passages)
    dropped = False
    while not plan.passages and len(plan.recent) > MIN_RECENT_AFTER_COMPRESSION:
        plan.recent.pop(0)
        dropped = True
        refit_rag(plan, retrieved_passages)
    if dropped and "reduce_history_for_rag" not in plan.compression_steps:
        plan.compression_steps.append("reduce_history_for_rag")
    return bool(plan.passages)


def compress(plan: ContextPlan, level: str) -> list[str]:
    """ContextPressureAnalyzer + nén. Mức light: chỉ các bước nén RAG tới khi <0.60. Mức strong/hard: lần lượt cả 5
    bước (bỏ trùng -> bỏ chunk điểm thấp -> nén nội dung chunk -> giảm tin lịch sử -> dùng summary) tới khi áp lực
    xuống dưới ngưỡng cảnh báo hoặc hết bước. Dùng ngưỡng cảnh báo (không chỉ vừa qua hard_limit) làm đích để chừa lề.
    Trả tên các bước đã áp dụng (cũng ghi vào plan.compression_steps)."""
    if level == "normal":
        return []
    plan.recent_initial = len(plan.recent)
    target = LIGHT_PRESSURE if level == "light" else plan.settings.context_pressure_warning
    steps = _STEPS[:_LIGHT_STEPS] if level == "light" else _STEPS
    applied: list[str] = []
    for name, step, repeat, touches_rag in steps:
        if plan.pressure < target:
            break
        while plan.pressure >= target and step(plan):
            if name not in applied:
                applied.append(name)
            if touches_rag:
                plan.recount_rag()  # tin gần đây thì recent_tokens tự tính lại từ danh sách
            if not repeat:
                break
    plan.compression_steps = applied
    return applied


# ---------------------------------------------------------------- dựng message theo role

class MessageBuilder:
    @staticmethod
    def build(plan: ContextPlan, *, history_context: str | None = None) -> list[dict]:
        """System (tĩnh -> động) + tin gần đây xen kẽ user/assistant + tin user cuối (RAG + câu hỏi)."""
        system = "\n\n".join(p for p in (plan.system_static, plan.summary_block, plan.memory_block) if p)
        messages = [{"role": "system", "content": system}]
        messages += [{"role": t.role, "content": t.content} for t in plan.recent]
        messages.append({
            "role": "user",
            "content": render_final_user(
                plan.question, render_rag_text(plan.passages), plan.settings.language,
                history_context=history_context, narrowing=plan.scope_narrowing,
            ),
        })
        return messages
