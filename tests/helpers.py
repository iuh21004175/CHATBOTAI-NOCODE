"""Tiện ích test dùng chung. Chạy toàn bộ: `env\\Scripts\\python.exe -m unittest discover -s tests -t .`

Test DB-backed (test_db_flow.py) CHỈ chạy khi DATABASE_URL trỏ tới một database có tên kết thúc bằng `_test` — không bao
giờ động vào DB thật `aichatbot`. Tạo DB thử: xem tests/README.md.
"""
from __future__ import annotations

import json
import os
import re

import numpy as np

from core.context_engine.decision import Signals
from core.context_engine.settings import EngineSettings
from core.context_engine.structured import LLMReply, StructuredOutput

DIM = 4096
_VOCAB: dict[str, int] = {}  # từ -> chiều riêng (không va chạm như băm mod DIM) nên độ giống của dữ liệu thử là chính xác


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Embedding giả tất định: túi từ vào 4096 chiều rồi chuẩn hóa L2 (giống _embed_batch) — 2 văn bản dùng chung từ thì
    cosine cao, nên test kiểm soát được độ liên quan mà không cần nạp model ONNX 2,2 GB."""
    vectors = []
    for text in texts:
        v = np.zeros(DIM)
        for word in re.findall(r"\w+", text.lower()):
            v[_VOCAB.setdefault(word, len(_VOCAB))] += 1.0
        norm = np.linalg.norm(v)
        vectors.append((v / norm if norm else v).tolist())
    return vectors


def settings(**overrides) -> EngineSettings:
    return EngineSettings.defaults(**overrides)


def signals(**overrides) -> Signals:
    """Tín hiệu 'mọi thứ đều ổn' (=> ANSWER); mỗi test đổi đúng tín hiệu cần thử."""
    base = dict(
        rag_used=True, candidate_count=3, top_distance=1.0, distance_gap=0.3, spread_ambiguous=False,
        context_pressure=0.30, context_compressed=False, intent_confidence=0.95, slot_completion=1.0,
        has_required_slots=False, clarification_turns_used=0, is_ambiguous_reference=False,
    )
    base.update(overrides)
    return Signals(**base)


def output(**overrides) -> StructuredOutput:
    base = dict(
        intent="ask_price", intent_confidence=0.95, slots={}, memory_updates=[], needs_history_lookup=False,
        self_assessed_confidence=0.9, proposed_answer="Gói Pro giá 500.000đ.", proposed_clarification_question="Bạn cần gói nào?",
    )
    base.update(overrides)
    return StructuredOutput(**base)


def llm_json(**overrides) -> str:
    data = {
        "intent": "ask_price", "intent_confidence": 0.95, "slots": {}, "memory_updates": [],
        "needs_history_lookup": False, "self_assessed_confidence": 0.9,
        "proposed_answer": "Gói Pro giá 500.000đ.", "proposed_clarification_question": "",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


def usage(prompt=1000, completion=50, hit=0, miss=None) -> dict:
    return {
        "prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
        "prompt_cache_hit_tokens": hit, "prompt_cache_miss_tokens": prompt - hit if miss is None else miss,
    }


class FakeLLM:
    """LLMCall giả: trả lần lượt các phản hồi đã dựng sẵn; ghi lại các message đã nhận."""

    def __init__(self, *replies, token_usage=None):
        self.replies = [r if isinstance(r, LLMReply) else LLMReply(r, token_usage or usage()) for r in replies]
        self.calls: list[list[dict]] = []

    def __call__(self, messages):
        self.calls.append(messages)
        if not self.replies:
            raise AssertionError("FakeLLM: bị gọi nhiều hơn số phản hồi đã dựng")
        return self.replies.pop(0)


def db_test_enabled() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    return url.rsplit("/", 1)[-1].split("?")[0].endswith("_test")
