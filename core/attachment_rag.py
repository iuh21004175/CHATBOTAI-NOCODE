"""Tra cứu trong tệp khách gửi (module "Đọc tài liệu"): cắt chunk + embed văn bản đã trích, tìm đoạn gần câu hỏi nhất.

Dùng chung chunker + model embedding với rag_engine nhưng collection Chroma RIÊNG (`attach_<bot_id>`), không bao giờ lẫn vào Cơ sở tri thức (`bot_<bot_id>`):
tệp của khách chỉ có nghĩa trong hội thoại của họ, còn kiến thức của chủ bot thì dùng chung cho mọi khách. Mỗi bot 1 collection (cùng lý do như rag_engine: chọn đúng
collection thay vì lọc bằng where loại bỏ rủi ro lộ dữ liệu chéo bot); trong collection, truy vấn LUÔN lọc theo attachment_id của hội thoại đang hỏi.

Trả về đoạn theo ĐÚNG hình dạng rag_engine.retrieve() trả (metadata/chunks/content/distance) để Context Builder cắt theo ngân sách token, nén và dựng prompt như
với tri thức của bot mà không cần biết nguồn.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from config import Config
from core import rag_engine
from extensions import chroma_client

logger = logging.getLogger(__name__)

_HEADER = "[Tệp khách gửi: {name}]"
_BLOCK_SEP = "\n\n"


def collection_name(bot_id: int) -> str:
    return f"attach_{bot_id}"


def _collection(bot_id: int):
    return chroma_client.get_or_create_collection(collection_name(bot_id))


def _chunk_id(attachment_id: int, index: int) -> str:
    return f"a{attachment_id}-{index}"


def index_text(bot_id: int, attachment_id: int, text: str) -> int:
    """Cắt chunk + embed + ghi. Idempotent (xoá chunk cũ của tệp trước). Lỗi giữa chừng thì xoá phần đã ghi. Trả số chunk."""
    collection = _collection(bot_id)
    collection.delete(where={"attachment_id": attachment_id})
    chunks = rag_engine.run_blocking(rag_engine.chunk_markdown, text)
    if not chunks:
        return 0
    try:
        for start in range(0, len(chunks), rag_engine.UPSERT_BATCH_SIZE):
            batch = chunks[start:start + rag_engine.UPSERT_BATCH_SIZE]
            documents = [c["content"] for c in batch]
            indexes = range(start, start + len(batch))
            collection.add(
                ids=[_chunk_id(attachment_id, i) for i in indexes],
                documents=documents,
                metadatas=[{"attachment_id": attachment_id, "chunk_index": i} for i in indexes],
                embeddings=rag_engine.embed_texts(documents),
            )
    except Exception:
        delete(bot_id, attachment_id)
        raise
    return len(chunks)


def delete(bot_id: int, attachment_id: int) -> None:
    try:
        _collection(bot_id).delete(where={"attachment_id": attachment_id})
    except Exception:
        logger.exception("attachment_rag: không xoá được chunk của tệp %s (bot %s)", attachment_id, bot_id)


@dataclass
class AttachmentMatch:
    passages: list[dict]
    best_distance: float | None


def search(bot_id: int, attachments: dict[int, str], question: str, top_k: int | None = None) -> AttachmentMatch:
    """attachments: {attachment_id: tên tệp} của hội thoại đang hỏi (đã được tầng service kiểm quyền sở hữu). Lấy top_k chunk gần câu hỏi nhất TRONG các tệp đó, ghép
    chunk liền kề cùng tệp thành 1 đoạn (giữ thứ tự đọc), sắp đoạn tốt nhất trước. KHÔNG lọc theo ngưỡng khoảng cách: khách chủ động gửi tệp nên câu như
    "tóm tắt tệp này" (không giống về ngữ nghĩa với bất kỳ đoạn nào) vẫn phải nhận được nội dung tệp."""
    top_k = top_k or Config.ATTACHMENT_TOP_K
    if not attachments:
        return AttachmentMatch([], None)
    collection = _collection(bot_id)
    ids = list(attachments)
    where = {"attachment_id": ids[0]} if len(ids) == 1 else {"attachment_id": {"$in": ids}}
    available = collection.count()
    if available == 0:
        return AttachmentMatch([], None)
    result = collection.query(
        query_embeddings=[rag_engine.embed_texts([question])[0]], n_results=min(top_k, available), where=where,
        include=["documents", "metadatas", "distances"],
    )
    if not result["ids"] or not result["ids"][0]:
        return AttachmentMatch([], None)
    hits = [
        {"attachment_id": meta["attachment_id"], "index": meta["chunk_index"], "content": doc, "distance": float(distance)}
        for doc, meta, distance in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
    ]

    # Ghép các chunk liền kề cùng tệp (đọc liền mạch), rồi sắp đoạn theo khoảng cách tốt nhất.
    hits.sort(key=lambda h: (h["attachment_id"], h["index"]))
    passages: list[dict] = []
    for hit in hits:
        chunk = {"index": hit["index"], "content": hit["content"], "hit": True}
        last = passages[-1] if passages else None
        if last and last["metadata"]["attachment_id"] == hit["attachment_id"] and last["metadata"]["chunk_indexes"][-1] == hit["index"] - 1:
            last["metadata"]["chunk_indexes"].append(hit["index"])
            last["chunks"].append(chunk)
            last["distance"] = min(last["distance"], hit["distance"])
        else:
            passages.append({
                "metadata": {"attachment_id": hit["attachment_id"], "chunk_indexes": [hit["index"]]},
                "chunks": [chunk], "distance": hit["distance"],
            })
    passages.sort(key=lambda p: p["distance"])
    for passage in passages:
        name = attachments.get(passage["metadata"]["attachment_id"], "")
        passage["chunks"][0]["content"] = f"{_HEADER.format(name=name)}\n{passage['chunks'][0]['content']}"  # nguồn gốc: LLM biết đây là tệp khách gửi, không phải tri thức của shop
        passage["content"] = _BLOCK_SEP.join(c["content"] for c in passage["chunks"])
    return AttachmentMatch(passages, passages[0]["distance"] if passages else None)
