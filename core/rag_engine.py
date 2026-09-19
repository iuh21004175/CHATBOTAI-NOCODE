"""RAG engine — chunk, embedding, truy vấn ChromaDB.

Tổ chức theo khách hàng/bot: mỗi bot có đúng 1 collection ChromaDB riêng (`bot_<bot_id>`) thay
vì gộp chung rồi lọc bằng where — chọn đúng collection theo bot_id loại bỏ hoàn toàn rủi ro lỗi
filter làm lộ dữ liệu sang bot/khách hàng khác. Vì 1 bot luôn thuộc đúng 1 team, giới hạn theo
bot_id cũng tự động giới hạn đúng phạm vi dữ liệu của khách hàng đó — query không cần thêm where
nào cả.

Model embedding AITeamVN/Vietnamese_Embedding được lưu sẵn dạng ONNX export (đã pooling) tại
models/Vietnamese_Embedding — gọi thẳng onnxruntime + tokenizer, không qua sentence-transformers
(bản ONNX export này đặt tên output "sentence_embedding" khác với wrapper mặc định của
sentence-transformers nên gọi trực tiếp cho nhẹ và ổn định hơn).
"""
import re

import numpy as np
import onnxruntime as ort
from langchain_text_splitters import RecursiveCharacterTextSplitter
from transformers import AutoTokenizer

from config import Config
from extensions import chroma_client

MAX_EMBEDDING_TOKENS = 2048
_TABLE_ROW_RE = re.compile(r"^\s*\|")


try:  # eventlet là tuỳ chọn: worker chạy riêng (không monkey-patch) vẫn dùng module này bình thường
    from eventlet import patcher as _patcher, tpool as _tpool

    _threading = _patcher.original("threading")  # khoá THẬT, không phải khoá "xanh" của eventlet
except ImportError:  # pragma: no cover
    import threading as _threading

    _patcher = _tpool = None

_load_lock = _threading.Lock()
_tokenizer_instance = None
_session_instance = None


def run_blocking(fn, *args):
    """Chạy tác vụ tính toán nặng (embed ONNX, tokenizer, cắt chunk) mà KHÔNG đóng băng server.

    Khi server chạy eventlet (run.py), các luồng là "xanh" và chạy nhường nhau: 1 lần embed ~vài giây mà
    không nhường thì toàn bộ server (HTTP, WebSocket) đứng hình. Ở chế độ đó việc nặng được đẩy sang luồng
    hệ điều hành thật bằng eventlet.tpool. Worker chạy riêng (không monkey-patch) thì gọi thẳng."""
    if _tpool is not None and _patcher.is_monkey_patched("thread"):
        return _tpool.execute(fn, *args)
    return fn(*args)


def _tokenizer() -> AutoTokenizer:
    global _tokenizer_instance
    if _tokenizer_instance is None:
        with _load_lock:
            if _tokenizer_instance is None:
                _tokenizer_instance = AutoTokenizer.from_pretrained(Config.EMBEDDING_MODEL_PATH)
    return _tokenizer_instance


def _onnx_session() -> ort.InferenceSession:
    """Model 2,2GB: nạp đúng 1 lần dù nhiều luồng cùng gọi (khoá tránh nạp đôi tốn gấp đôi RAM)."""
    global _session_instance
    if _session_instance is None:
        with _load_lock:
            if _session_instance is None:
                _session_instance = ort.InferenceSession(
                    f"{Config.EMBEDDING_MODEL_PATH}/model.onnx", providers=["CPUExecutionProvider"]
                )
    return _session_instance


def count_tokens(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False))


EMBED_BATCH_SIZE = 8  # embed theo lô nhỏ để RAM không phình theo kích thước tài liệu


def _embed_batch(batch: list[str]) -> list[list[float]]:
    enc = _tokenizer()(batch, padding=True, truncation=True, max_length=MAX_EMBEDDING_TOKENS, return_tensors="np")
    outputs = _onnx_session().run(
        ["sentence_embedding"],
        {"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]},
    )
    embeddings = outputs[0]
    norm = np.linalg.norm(embeddings, axis=1, keepdims=True)
    return (embeddings / norm).tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    result: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        result.extend(run_blocking(_embed_batch, texts[start:start + EMBED_BATCH_SIZE]))
    return result


def warm_up() -> None:
    """Nạp tokenizer + model ngay lúc khởi động app (~20 giây) để tài liệu/câu hỏi đầu tiên không phải chờ."""
    embed_texts(["khởi động"])


def get_collection_name(bot_id: int) -> str:
    return f"bot_{bot_id}"


def get_collection(bot_id: int):
    return chroma_client.get_or_create_collection(get_collection_name(bot_id))


# ---- Chunking ----

DEFAULT_CHUNK_SIZE = 450
DEFAULT_CHUNK_OVERLAP = 60
CHUNK_SIZE_MIN = 100
CHUNK_SIZE_MAX = 1000  # chừa chỗ cho heading path, luôn thấp hơn MAX_EMBEDDING_TOKENS
OVERLAP_MAX_PERCENT = 30  # overlap quá lớn làm chunk lặp nội dung, tốn embed mà không thêm ngữ nghĩa

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)(?:\s+#+)?\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BLOCK_SEP = "\n\n"
_BLOCK_SEP_TOKENS = 1


def max_chunk_overlap(chunk_size: int) -> int:
    return chunk_size * OVERLAP_MAX_PERCENT // 100


def normalize_chunk_params(chunk_size: int, chunk_overlap: int) -> tuple[int, int]:
    """Đưa cấu hình chunk về khoảng hợp lệ (dữ liệu cũ/sai không làm hỏng việc cắt chunk)."""
    size = min(max(int(chunk_size), CHUNK_SIZE_MIN), CHUNK_SIZE_MAX)
    overlap = min(max(int(chunk_overlap), 0), max_chunk_overlap(size))
    return size, overlap


def format_heading_path(metadata: dict) -> str:
    parts = [metadata[k] for k in ("h1", "h2", "h3") if metadata.get(k)]
    return " > ".join(parts)


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_RE.match(line))


def _is_table_separator(line: str) -> bool:
    stripped = line.strip()
    return "-" in stripped and set(stripped) <= set("|-: ")


def _split_sections(text: str) -> list[tuple[dict, str]]:
    """Tách theo heading # / ## / ### (bỏ qua dòng '#' nằm trong khối code). Tự tách thay vì dùng
    MarkdownHeaderTextSplitter vì nó gộp các đoạn bằng "  \\n" và làm mất dòng trống giữa các đoạn."""
    sections: list[tuple[dict, str]] = []
    heads = {1: "", 2: "", 3: ""}
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        content = "\n".join(body).strip()
        if content:
            sections.append(({f"h{i}": heads[i] for i in (1, 2, 3) if heads[i]}, content))
        body.clear()

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        match = None if in_fence else _HEADING_RE.match(line)
        if match:
            flush()
            level = len(match.group(1))
            heads[level] = match.group(2)
            for deeper in range(level + 1, 4):
                heads[deeper] = ""
        else:
            body.append(line)
    flush()
    return sections


def _split_atoms(body: str) -> list[tuple[str, str]]:
    """Chia thân section thành các khối ngữ nghĩa không nên cắt giữa chừng: đoạn văn/danh sách
    (ngăn bởi dòng trống), bảng markdown, khối code. Trả về [(loại "text"|"table", nội dung)]."""
    atoms: list[tuple[str, str]] = []
    buf: list[str] = []
    kind = "text"
    in_fence = False

    def flush() -> None:
        content = "\n".join(buf).strip()
        if content:
            atoms.append((kind, content))
        buf.clear()

    for line in body.splitlines():
        if _FENCE_RE.match(line):
            if not in_fence and kind == "table":
                flush()
                kind = "text"
            in_fence = not in_fence
            buf.append(line)
            continue
        if in_fence:
            buf.append(line)
            continue
        if not line.strip():
            flush()
            kind = "text"
            continue
        line_kind = "table" if _is_table_row(line) else "text"
        if line_kind != kind:
            flush()
            kind = line_kind
        buf.append(line)
    flush()
    return atoms


def _split_table(table: str, budget: int) -> list[str]:
    """Chia bảng quá lớn theo nhóm dòng, lặp lại dòng tiêu đề ở mỗi chunk để chunk nào cũng
    biết cột nào là gì. Không bao giờ cắt giữa 1 dòng."""
    lines = table.splitlines()
    header = lines[:2] if len(lines) >= 2 and _is_table_separator(lines[1]) else []
    rows = lines[len(header):]
    header_text = "\n".join(header)
    header_tokens = count_tokens(header_text) + _BLOCK_SEP_TOKENS if header else 0

    pieces: list[str] = []
    current: list[str] = []
    used = header_tokens
    for row in rows:
        row_tokens = count_tokens(row) + 1
        if current and used + row_tokens > budget:
            pieces.append("\n".join(header + current))
            current, used = [], header_tokens
        current.append(row)
        used += row_tokens
    if current:
        pieces.append("\n".join(header + current))
    return pieces


def _overlap_tail(group: list[tuple[str, int]], overlap: int) -> list[tuple[str, int]]:
    """Các khối cuối của chunk trước (nguyên khối, tổng ≤ overlap) để lặp sang chunk sau."""
    tail: list[tuple[str, int]] = []
    total = 0
    for item in reversed(group):
        if total + item[1] > overlap:
            break
        tail.insert(0, item)
        total += item[1]
    return tail if len(tail) < len(group) else []  # lặp cả chunk trước thì vô nghĩa


def _group_tokens(group: list[tuple[str, int]]) -> int:
    return sum(t for _, t in group) + _BLOCK_SEP_TOKENS * max(len(group) - 1, 0)


def _pack_section(body: str, budget: int, overlap: int) -> list[str]:
    """Gom các khối ngữ nghĩa của 1 section vào chunk ≤ budget token: chunk luôn kết thúc ở ranh
    giới đoạn/bảng, không cắt giữa đoạn. Chỉ khi 1 khối tự nó vượt budget mới cắt nhỏ hơn
    (bảng -> theo dòng, văn bản -> theo câu, có overlap)."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=budget,
        chunk_overlap=overlap,
        length_function=count_tokens,
        separators=["\n\n", "\n", ". ", "! ", "? ", "; ", " ", ""],
        keep_separator="end",
    )

    units: list[tuple[str, int, bool]] = []  # (nội dung, số token, đứng riêng 1 chunk)
    for kind, atom in _split_atoms(body):
        tokens = count_tokens(atom)
        if tokens <= budget:
            units.append((atom, tokens, False))
            continue
        pieces = _split_table(atom, budget) if kind == "table" else splitter.split_text(atom)
        units.extend((piece, count_tokens(piece), True) for piece in pieces)

    chunks: list[str] = []
    group: list[tuple[str, int]] = []

    def flush() -> None:
        if group:
            chunks.append(_BLOCK_SEP.join(text for text, _ in group))

    for text, tokens, standalone in units:
        if standalone:
            flush()
            group = []
            chunks.append(text)
            continue
        if group and _group_tokens(group) + _BLOCK_SEP_TOKENS + tokens > budget:
            flush()
            group = _overlap_tail(group, overlap)
            if group and _group_tokens(group) + _BLOCK_SEP_TOKENS + tokens > budget:
                group = []
        group.append((text, tokens))
    flush()
    return chunks


def chunk_markdown(
    text: str, chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
) -> list[dict]:
    """Chunk văn bản markdown theo ngữ nghĩa:
    - Tách theo heading, mọi chunk đều mở đầu bằng đường dẫn heading (h1 > h2 > h3) để tự đủ ngữ cảnh.
    - Trong 1 section, gom nguyên đoạn/danh sách/bảng/khối code vào chunk tới khi đầy chunk_size;
      chỉ cắt giữa khối khi bản thân khối đó lớn hơn chunk_size.
    - Bảng lớn (CSV) chia theo dòng, lặp lại dòng tiêu đề ở mỗi chunk.
    chunk_size/chunk_overlap tính bằng token thật của model embedding (đã gồm heading)."""
    chunk_size, chunk_overlap = normalize_chunk_params(chunk_size, chunk_overlap)

    chunks = []
    for metadata, body in _split_sections(text):
        heading_path = format_heading_path(metadata)
        prefix = f"{heading_path}{_BLOCK_SEP}" if heading_path else ""
        budget = max(chunk_size - count_tokens(prefix), chunk_size // 2)
        overlap = min(chunk_overlap, budget // 2)
        for piece in _pack_section(body, budget, overlap):
            chunks.append({"content": prefix + piece, "metadata": dict(metadata)})
    return chunks


# ---- ChromaDB: ghi/xóa/đọc theo document_id ----

def _chunk_id(document_id: int, chunk_index: int) -> str:
    return f"{document_id}-{chunk_index}"


UPSERT_BATCH_SIZE = 16  # embed + ghi Chroma theo lô: tài liệu 5MB ≈ 4.000 chunk, ghi 1 lần sẽ vượt giới hạn lô của Chroma


def upsert_chunks(bot_id: int, document_id: int, chunks: list[dict], on_progress=None) -> int:
    """Embed + ghi chunk vào collection riêng của bot theo từng lô. Xóa chunk cũ của tài liệu này trước
    (idempotent khi re-embed). on_progress(đã_xong, tổng) được gọi sau mỗi lô. Lỗi giữa chừng thì xóa phần
    đã ghi để không để lại 1 nửa tài liệu."""
    collection = get_collection(bot_id)
    collection.delete(where={"document_id": document_id})

    total = len(chunks)
    if not total:
        return 0

    try:
        for start in range(0, total, UPSERT_BATCH_SIZE):
            batch = chunks[start:start + UPSERT_BATCH_SIZE]
            documents = [c["content"] for c in batch]
            indexes = range(start, start + len(batch))
            collection.add(
                ids=[_chunk_id(document_id, i) for i in indexes],
                documents=documents,
                metadatas=[{"document_id": document_id, "chunk_index": i} for i in indexes],
                embeddings=embed_texts(documents),
            )
            if on_progress:
                on_progress(start + len(batch), total)
    except Exception:
        try:
            collection.delete(where={"document_id": document_id})
        except Exception:
            pass
        raise
    return total


def delete_document(bot_id: int, document_id: int) -> None:
    """Xóa toàn bộ chunk của 1 tài liệu qua metadata document_id — không cần biết từng chunk id."""
    get_collection(bot_id).delete(where={"document_id": document_id})


def get_document_chunks(bot_id: int, document_id: int) -> list[dict]:
    result = get_collection(bot_id).get(where={"document_id": document_id})
    items = list(zip(result["ids"], result["documents"], result["metadatas"]))
    items.sort(key=lambda item: item[2].get("chunk_index", 0))
    return [{"id": i, "content": d, "metadata": m} for i, d, m in items]


def update_chunk(bot_id: int, chunk_id: str, content: str) -> int:
    """Sửa nội dung 1 chunk — tính lại token bằng đúng tokenizer, chặn nếu vượt giới hạn embed,
    rồi re-embed lại đúng chunk đó."""
    token_count = count_tokens(content)
    if token_count > MAX_EMBEDDING_TOKENS:
        raise ValueError(f"Chunk vượt quá {MAX_EMBEDDING_TOKENS} token (hiện {token_count}).")

    collection = get_collection(bot_id)
    existing = collection.get(ids=[chunk_id])
    metadata = existing["metadatas"][0] if existing["metadatas"] else {}
    embedding = embed_texts([content])[0]
    collection.update(ids=[chunk_id], documents=[content], metadatas=[metadata], embeddings=[embedding])
    return token_count


# ---- Truy vấn ----

NEIGHBOR_WINDOW = 1  # ghép thêm bấy nhiêu chunk liền trước/sau mỗi kết quả tìm được


def _expand_with_neighbors(collection, hits: list[tuple[str, str, dict]], window: int) -> list[dict]:
    """Ngữ nghĩa trải trên nhiều chunk vẫn tìm thấy 1 chunk: ghép thêm chunk liền kề (cùng tài
    liệu) rồi nối các chunk liên tiếp thành 1 đoạn ngữ cảnh. Chunk trùng giữa nhiều kết quả chỉ
    xuất hiện 1 lần; thứ tự đoạn theo thứ hạng của kết quả tốt nhất trong đoạn."""
    known = {cid: (content, meta) for cid, content, meta in hits}
    wanted = {
        _chunk_id(meta["document_id"], meta["chunk_index"] + offset)
        for _, _, meta in hits
        for offset in range(-window, window + 1)
        if meta["chunk_index"] + offset >= 0
    } - known.keys()
    if wanted:
        fetched = collection.get(ids=sorted(wanted))  # id không tồn tại (đầu/cuối tài liệu) tự bị bỏ qua
        known.update({cid: (c, m) for cid, c, m in zip(fetched["ids"], fetched["documents"], fetched["metadatas"])})

    rank: dict[tuple[int, int], int] = {}  # (document_id, chunk_index) -> thứ hạng tốt nhất
    for position, (_, _, meta) in enumerate(hits):
        for offset in range(-window, window + 1):
            key = (meta["document_id"], meta["chunk_index"] + offset)
            if _chunk_id(*key) in known:
                rank[key] = min(rank.get(key, position), position)

    passages: list[dict] = []
    for document_id, index in sorted(rank):
        last = passages[-1] if passages else None
        if last and last["metadata"]["document_id"] == document_id and last["metadata"]["chunk_indexes"][-1] == index - 1:
            last["metadata"]["chunk_indexes"].append(index)
            last["content"] += _BLOCK_SEP + known[_chunk_id(document_id, index)][0]
            last["rank"] = min(last["rank"], rank[(document_id, index)])
        else:
            passages.append({
                "content": known[_chunk_id(document_id, index)][0],
                "metadata": {"document_id": document_id, "chunk_indexes": [index]},
                "rank": rank[(document_id, index)],
            })
    passages.sort(key=lambda p: p["rank"])
    return [{"content": p["content"], "metadata": p["metadata"]} for p in passages]


def search(bot_id: int, question: str, top_k: int = 5, neighbors: int = NEIGHBOR_WINDOW) -> list[dict]:
    """Similarity search trong đúng collection của bot — không cần thêm where nào vì 1 bot luôn
    thuộc đúng 1 team; chọn đúng collection đã tự nhiên giới hạn trong phạm vi dữ liệu khách hàng đó.
    neighbors > 0: ghép thêm chunk liền kề (xem _expand_with_neighbors)."""
    collection = get_collection(bot_id)
    if collection.count() == 0:
        return []
    query_embedding = embed_texts([question])[0]
    result = collection.query(query_embeddings=[query_embedding], n_results=min(top_k, collection.count()))
    hits = list(zip(
        result["ids"][0] if result["ids"] else [],
        result["documents"][0] if result["documents"] else [],
        result["metadatas"][0] if result["metadatas"] else [],
    ))
    if neighbors > 0:
        return _expand_with_neighbors(collection, hits, neighbors)
    return [{"content": d, "metadata": m} for _, d, m in hits]


DEFAULT_LANGUAGE = "vi"

# Ngôn ngữ trả lời của bot (bot_settings.language). Nhãn prompt viết cùng ngôn ngữ đích để model
# không bị kéo về tiếng Việt; chỉ dẫn ngôn ngữ đặt ngay trước câu hỏi vì model ưu tiên phần cuối prompt.
PROMPT_TEXTS = {
    "vi": {
        "context": "Thông tin tham khảo:",
        "history": "Hội thoại trước đó:",
        "customer": "Khách",
        "assistant": "Trợ lý",
        "question": "Câu hỏi của khách:",
        "instruction": "Hãy trả lời bằng tiếng Việt.",
    },
    "en": {
        "context": "Reference information:",
        "history": "Previous conversation:",
        "customer": "Customer",
        "assistant": "Assistant",
        "question": "Customer question:",
        "instruction": (
            "Always reply in English, even if the customer's question or the reference information "
            "is written in another language. Translate the relevant information when needed."
        ),
    },
}
SUPPORTED_LANGUAGES = tuple(PROMPT_TEXTS)


def build_prompt(
    question: str,
    context: str,
    system_prompt: str = "",
    language: str = DEFAULT_LANGUAGE,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """history: [(người gửi "customer"|"bot", nội dung)] theo thứ tự cũ -> mới, không gồm câu hỏi hiện tại."""
    texts = PROMPT_TEXTS.get(language, PROMPT_TEXTS[DEFAULT_LANGUAGE])
    parts = [system_prompt] if system_prompt else []
    if context:
        parts.append(f"{texts['context']}\n{context}")
    if history:
        lines = (f"{texts['customer' if sender == 'customer' else 'assistant']}: {text}" for sender, text in history)
        parts.append(f"{texts['history']}\n" + "\n".join(lines))
    parts.append(texts["instruction"])
    parts.append(f"{texts['question']} {question}")
    return "\n\n".join(parts)


def answer(
    bot_id: int,
    question: str,
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int | None = None,
    language: str = DEFAULT_LANGUAGE,
    history: list[tuple[str, str]] | None = None,
) -> str:
    """Ghép ngữ cảnh (search() + system prompt từ bot_settings) rồi gọi core.llm_client.get_llm()
    để sinh câu trả lời. temperature/max_tokens/language lấy từ bot_settings của bot (dùng
    dashboard.service.generate_reply để tự nạp cấu hình). Dùng cho endpoint
    POST /widget/api/<bot_id>/messages (realtime, đồng bộ trong request — xem mục "Real-time hay
    theo lịch" trong tài liệu kiến trúc)."""
    from core.llm_client import get_llm

    context_chunks = search(bot_id, question, top_k=5)
    context = "\n\n---\n\n".join(c["content"] for c in context_chunks)

    llm = get_llm(temperature, max_tokens)
    response = llm.invoke(build_prompt(question, context, system_prompt, language, history))
    return response.content
