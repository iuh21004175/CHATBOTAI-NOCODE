"""Phase 5 — Historical Retrieval: tìm lại nội dung cũ TRONG CÙNG hội thoại (Bước F, hiếm).

Vector tin nhắn nằm ở collection Chroma riêng "history_<bot_id>", KHÁC collection tri thức "bot_<bot_id>" để tìm kiếm
ngữ nghĩa trên tri thức không lẫn với tìm kiếm trên lịch sử chat. Việc embed tin (index_messages) chạy ở worker nền
(core/context_engine/jobs.py), không chặn luồng trả lời; chỉ việc TÌM (search_history, embed 1 câu hỏi) chạy realtime và
chỉ khi LLM báo needs_history_lookup.
"""
from __future__ import annotations

import logging

from core import rag_engine
from core.context_engine.prompts import texts

logger = logging.getLogger("context_engine.history")

DEFAULT_TOP_K = 3
MAX_HIT_CHARS = 600  # cắt từng tin khi đưa vào prompt


def history_collection_name(bot_id: int) -> str:
    return f"history_{bot_id}"


def get_history_collection(bot_id: int):
    # Đọc chroma_client qua rag_engine để test có thể thay bằng client tạm trong bộ nhớ (1 nơi duy nhất trỏ tới Chroma)
    return rag_engine.chroma_client.get_or_create_collection(history_collection_name(bot_id))


def _message_id(message_id: int) -> str:
    return f"msg-{message_id}"


def index_messages(bot_id: int, items: list[dict]) -> int:
    """Embed + ghi (upsert — idempotent nên chạy lại sau sự cố không tạo bản trùng) các tin vào history_<bot_id>.
    items: [{"message_id", "conversation_id", "sender", "content", "created_at"(datetime)}]. Trả số tin đã ghi."""
    items = [i for i in items if i["content"] and i["content"].strip()]
    if not items:
        return 0
    collection = get_history_collection(bot_id)
    documents = [i["content"].strip() for i in items]
    collection.upsert(
        ids=[_message_id(i["message_id"]) for i in items],
        documents=documents,
        embeddings=rag_engine.embed_texts(documents),
        metadatas=[
            {
                "conversation_id": i["conversation_id"],
                "message_id": i["message_id"],
                "sender": i["sender"],
                "created_at": i["created_at"].isoformat() if i.get("created_at") else "",
            }
            for i in items
        ],
    )
    return len(items)


def search_history(
    bot_id: int,
    conversation_id: int,
    question: str,
    *,
    exclude_message_ids: set[int],
    distance_threshold: float,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """Top-k tin liên quan trong ĐÚNG conversation_id (lọc bằng where), bỏ các tin đã có sẵn trong prompt
    (exclude_message_ids) và tin có khoảng cách > ngưỡng (cùng ngưỡng khoảng cách của tri thức: đơn vị bình phương L2).
    Trả [{"message_id", "sender", "content", "distance"}] gần nhất trước."""
    collection = get_history_collection(bot_id)
    total = collection.count()
    if total == 0:
        return []
    result = collection.query(
        query_embeddings=[rag_engine.embed_texts([question])[0]],
        n_results=min(top_k + len(exclude_message_ids), total),
        where={"conversation_id": conversation_id},
        include=["documents", "metadatas", "distances"],
    )
    if not result["ids"] or not result["ids"][0]:
        return []
    hits = []
    for doc, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0]):
        if meta["message_id"] in exclude_message_ids or distance > distance_threshold:
            continue
        hits.append({"message_id": meta["message_id"], "sender": meta["sender"], "content": doc, "distance": float(distance)})
    return hits[:top_k]


def format_history_context(hits: list[dict], language: str) -> str:
    t = texts(language)
    lines = []
    for hit in sorted(hits, key=lambda h: h["message_id"]):  # theo thứ tự thời gian cho dễ đọc
        label = t["bot"] if hit["sender"] == "bot" else t["customer"]
        lines.append(f"- {label}: {hit['content'][:MAX_HIT_CHARS]}")
    return "\n".join(lines)
