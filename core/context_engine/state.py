"""Phase 1 — Context State: conversation_state, structured_memory, điều kiện kích hoạt rolling summary.

Chia đôi: phần TÍNH TOÁN THUẦN (merge slot, slot_completion, chuyển intent) không đụng DB để test từng nhánh; phần
GHI/ĐỌC DB (load/save) mỏng, chỉ chuyển kết quả đã tính vào bảng. Rolling summary KHÔNG chạy ở đây: chỉ đặt cờ
`summary_pending` (xem maybe_request_summary) để worker nền xử lý (core/context_engine/jobs.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from core import rag_engine
from core.context_engine.settings import EngineSettings
from core.context_engine.structured import StructuredOutput


# ---------------------------------------------------------------- kiểu dữ liệu thuần

@dataclass(frozen=True)
class IntentSpec:
    name: str
    description: str = ""
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryItem:
    category: str
    key: str
    value: str
    confidence: float


@dataclass
class ConversationSnapshot:
    """Ảnh chụp trạng thái hội thoại đầu lượt (đầu vào bất biến của engine)."""

    current_intent: str | None = None
    previous_intent: str | None = None
    intent_confidence: float | None = None
    slots: dict = field(default_factory=dict)
    summary: str | None = None
    last_summarized_message_id: int | None = None
    clarification_turns_used: int = 0
    memory: list[MemoryItem] = field(default_factory=list)


@dataclass(frozen=True)
class StateUpdate:
    """Trạng thái mới sau lượt (ghi vào conversation_state ở Bước D)."""

    current_intent: str | None
    previous_intent: str | None
    intent_confidence: float | None
    intent_changed: bool
    slots: dict
    slot_completion: float
    required_slots: tuple[str, ...]
    clarification_turns_used: int


# ---------------------------------------------------------------- tính toán thuần

def is_filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True  # số 0 / False là giá trị hợp lệ


def merge_slots(old: dict | None, new: dict | None) -> dict:
    """Ghi đè CHỈ khi lượt này có giá trị mới; slot cũ không bị xóa khi lượt này không nhắc lại (hoặc trả null/rỗng)."""
    merged = dict(old or {})
    for key, value in (new or {}).items():
        if is_filled(value):
            merged[key] = value
        else:
            merged.setdefault(key, None)  # ghi nhận slot đã được hỏi tới nhưng chưa có giá trị
    return merged


def compute_slot_completion(slots: dict, required: tuple[str, ...]) -> float:
    """Tỉ lệ required-slot đã điền; intent không khai báo required_slots thì coi như luôn đạt (1.0)."""
    if not required:
        return 1.0
    return sum(1 for name in required if is_filled(slots.get(name))) / len(required)


def find_intent(intents: list[IntentSpec], name: str | None) -> IntentSpec | None:
    if not name:
        return None
    return next((i for i in intents if i.name == name), None)


def resolve_state(
    snapshot: ConversationSnapshot, output: StructuredOutput, intents: list[IntentSpec], settings: EngineSettings
) -> StateUpdate:
    """Trạng thái sau lượt này TRƯỚC khi biết quyết định (Bước C cần slot_completion; clarification_turns_used do
    finalize_turns() đặt sau khi có quyết định).

    Intent mới chỉ thay intent hiện tại khi chưa có intent nào hoặc độ chắc chắn đạt ngưỡng: 1 lượt mơ hồ (vd. "ok",
    "cái này") không được xóa intent đang theo đuổi cùng các required_slots của nó. Độ chắc chắn thấp vẫn được ghi
    trong decision_trace và vẫn kích hoạt CLARIFY ở Bước C."""
    confidence = output.intent_confidence
    accept = bool(output.intent) and (
        snapshot.current_intent is None
        or confidence is None
        or confidence >= settings.intent_confidence_threshold
    )
    current = output.intent if accept else snapshot.current_intent
    changed = bool(snapshot.current_intent and current and current != snapshot.current_intent)
    slots = merge_slots(snapshot.slots, output.slots)
    spec = find_intent(intents, current)
    required = spec.required if spec else ()
    return StateUpdate(
        current_intent=current,
        previous_intent=snapshot.current_intent if changed else snapshot.previous_intent,
        intent_confidence=confidence if accept else snapshot.intent_confidence,
        intent_changed=changed,
        slots=slots,
        slot_completion=compute_slot_completion(slots, required),
        required_slots=required,
        clarification_turns_used=snapshot.clarification_turns_used,
    )


def finalize_turns(update: StateUpdate, snapshot: ConversationSnapshot, decision_is_clarify: bool) -> StateUpdate:
    """Số lượt CLARIFY LIÊN TIẾP: +1 khi lượt này hỏi làm rõ, về 0 khi đã trả lời/từ chối (đợt hỏi kết thúc)."""
    return replace(update, clarification_turns_used=snapshot.clarification_turns_used + 1 if decision_is_clarify else 0)


def select_memory_to_keep(rows: list[dict], max_items: int) -> list[dict]:
    """Khi vượt max_items: loại item có confidence THẤP NHẤT trước, cùng confidence thì loại item CŨ NHẤT trước.
    rows: [{"confidence", "updated_at", ...}]; trả các dòng cần GIỮ."""
    if len(rows) <= max_items:
        return list(rows)
    ranked = sorted(rows, key=lambda r: (r["confidence"], r["updated_at"]), reverse=True)
    return ranked[:max_items]


# ---------------------------------------------------------------- đọc/ghi DB

def get_or_create_state(conversation):
    """Tạo lười. 2 request cùng hội thoại cùng lúc (khách bấm gửi 2 lần) đều thấy "chưa có" rồi cùng tạo: ràng buộc
    unique(conversation_id) chặn bản thứ 2 — khi đó dùng lại bản đã được request kia tạo thay vì báo lỗi."""
    from sqlalchemy.exc import IntegrityError

    from app.models import ConversationState
    from extensions import db

    state = ConversationState.query.filter_by(conversation_id=conversation.id).first()
    if state is None:
        try:
            with db.session.begin_nested():  # savepoint: lỗi unique chỉ hủy phần tạo state, không hủy cả giao dịch của request
                state = ConversationState(conversation_id=conversation.id, bot_id=conversation.bot_id, slots={})
                db.session.add(state)
        except IntegrityError:
            state = ConversationState.query.filter_by(conversation_id=conversation.id).one()
    return state


def load_memory(conversation_id: int) -> list[MemoryItem]:
    from app.models import StructuredMemory

    rows = StructuredMemory.query.filter_by(conversation_id=conversation_id).order_by(StructuredMemory.category, StructuredMemory.id).all()
    return [MemoryItem(r.category, r.mem_key, r.value, r.confidence) for r in rows]


def load_intents(bot_id: int) -> list[IntentSpec]:
    from app.models import BotIntentConfig

    rows = BotIntentConfig.query.filter_by(bot_id=bot_id).order_by(BotIntentConfig.id).all()
    return [
        IntentSpec(r.intent_name, (r.description or "").strip(), tuple(r.required_slots or ()), tuple(r.optional_slots or ()))
        for r in rows
    ]


def load_snapshot(state, settings: EngineSettings) -> ConversationSnapshot:
    return ConversationSnapshot(
        current_intent=state.current_intent,
        previous_intent=state.previous_intent,
        intent_confidence=state.intent_confidence,
        slots=dict(state.slots or {}),
        summary=state.summary if settings.summary_enabled else None,
        last_summarized_message_id=state.last_summarized_message_id,
        clarification_turns_used=state.clarification_turns_used or 0,
        memory=load_memory(state.conversation_id) if settings.structured_memory_enabled else [],
    )


def fetch_recent_messages(conversation_id: int, *, before_message_id: int | None, after_message_id: int | None, limit: int):
    """Tối đa `limit` tin gần nhất (cũ -> mới), bỏ tin nhân viên (như luồng cũ), bỏ tin đã nằm trong summary
    (id <= after_message_id) và tin hiện tại/mới hơn (id >= before_message_id)."""
    from app.models import Message

    query = Message.query.filter(Message.conversation_id == conversation_id, Message.sender != "staff")
    if before_message_id is not None:
        query = query.filter(Message.id < before_message_id)
    if after_message_id:
        query = query.filter(Message.id > after_message_id)
    return list(reversed(query.order_by(Message.id.desc()).limit(limit).all()))


def save_state_update(state, update: StateUpdate) -> None:
    """Ghi trạng thái mới (tạo dict slots MỚI để SQLAlchemy nhận ra JSON đã đổi)."""
    state.previous_intent = update.previous_intent
    state.current_intent = update.current_intent
    state.intent_confidence = update.intent_confidence
    state.intent_changed = update.intent_changed
    state.slots = dict(update.slots)
    state.slot_completion = update.slot_completion
    state.clarification_turns_used = update.clarification_turns_used


def store_memory(bot_id: int, conversation_id: int, updates: list[dict], settings: EngineSettings, source_message_id: int | None) -> int:
    """extract_and_store: chỉ lưu mục có confidence >= memory_min_confidence; cùng (category, key) thì cập nhật giá trị
    mới; vượt memory_max_items thì loại theo select_memory_to_keep. Trả số mục được ghi. Không commit (service commit)."""
    from app.models import StructuredMemory
    from extensions import db

    accepted = [u for u in updates if u["confidence"] >= settings.memory_min_confidence]
    if not accepted:
        return 0
    now = datetime.utcnow()
    existing = {(r.category, r.mem_key): r for r in StructuredMemory.query.filter_by(conversation_id=conversation_id).all()}
    for item in accepted:
        row = existing.get((item["category"], item["key"]))
        if row is None:
            row = StructuredMemory(
                bot_id=bot_id, conversation_id=conversation_id, category=item["category"], mem_key=item["key"],
                value=item["value"], confidence=item["confidence"], source_message_id=source_message_id, created_at=now, updated_at=now,
            )
            db.session.add(row)
            existing[(item["category"], item["key"])] = row
        else:
            row.value, row.confidence, row.source_message_id, row.updated_at = item["value"], item["confidence"], source_message_id, now

    rows = list(existing.values())
    ranked = select_memory_to_keep(
        [{"confidence": r.confidence, "updated_at": r.updated_at or now, "row": r} for r in rows], settings.memory_max_items
    )
    kept = {id(item["row"]) for item in ranked}
    for row in rows:
        if id(row) not in kept:
            if row.id is None:
                db.session.expunge(row)
            else:
                db.session.delete(row)
    return len(accepted)


MAX_UNSUMMARIZED_SCAN = 500


def maybe_request_summary(state, settings: EngineSettings) -> bool:
    """Bước E: nếu tổng token các tin CHƯA nằm trong summary vượt summary_trigger_tokens thì đặt cờ summary_pending
    (worker nền tóm tắt). Chỉ đếm token (nhẹ), không gọi LLM. Trả True khi vừa đặt cờ. Không commit."""
    from app.models import Message

    if not settings.summary_enabled or state.summary_pending:
        return False
    rows = (
        Message.query.filter(Message.conversation_id == state.conversation_id, Message.id > (state.last_summarized_message_id or 0))
        .order_by(Message.id.asc())
        .limit(MAX_UNSUMMARIZED_SCAN)
        .all()
    )
    if not rows:
        return False
    total = sum(rag_engine.count_tokens_many([m.content for m in rows]))
    if total <= settings.summary_trigger_tokens:
        return False
    state.summary_pending = True
    return True
