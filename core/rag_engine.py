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
import logging
import re
from dataclasses import dataclass, field

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


def _count_tokens_batch(texts: list[str]) -> list[int]:
    if not texts:
        return []
    return [len(ids) for ids in _tokenizer()(texts, add_special_tokens=False)["input_ids"]]


def count_tokens_many(texts: list[str]) -> list[int]:
    """Đếm token cả lô trong 1 lần run_blocking (Context Builder đếm hàng chục đoạn mỗi lượt — gọi run_blocking
    từng đoạn tốn 1 lần chuyển luồng cho mỗi đoạn). Chỉ là ƯỚC LƯỢNG theo tokenizer của model embedding, không
    phải số token DeepSeek tính tiền."""
    return run_blocking(_count_tokens_batch, list(texts))


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


# ---- Truy vấn (RAG Controller) ----

NEIGHBOR_WINDOW = 1  # ghép thêm bấy nhiêu chunk liền trước/sau mỗi kết quả tìm được

# Hai chunk KHÁC tài liệu có cosine >= mức này coi là trùng ngữ nghĩa (chỉ giữ chunk gần câu hỏi hơn). Chunk liền kề
# trong cùng tài liệu chỉ giống nhau ~0,8-0,9 do overlap nên không bao giờ bị loại nhầm ở mức này.
DUPLICATE_COSINE = 0.97
# Hai ứng viên tốt nhất cách nhau < mức này (đơn vị bình phương L2 ≡ cosine 0,025) coi là "liên quan ngang nhau".
# Đo trên bot_4/bot_21 (6 câu hỏi): khoảng cách giữa hạng 1 và 2 dao động 0,03-0,26.
DISTANCE_GAP_SMALL = 0.05

logger = logging.getLogger(__name__)


def similarity_from_distance(distance: float) -> float:
    """Chroma trả BÌNH PHƯƠNG khoảng cách L2 (collection dùng space="l2" mặc định — đã xác nhận bằng cách đọc cấu hình
    collection thật, và đo: d Chroma trả == 2·(1−cos) tới 4 chữ số thập phân). Vector đã chuẩn hóa độ dài 1
    (_embed_batch) nên cos = 1 − d/2 (1 = giống hệt, 0 = không liên quan). KHÔNG dùng 1 − d²/2 (đó là công thức khi
    d là khoảng cách L2 chưa bình phương)."""
    return 1.0 - distance / 2.0


def _expand_with_neighbors(collection, hits: list[dict], window: int) -> list[dict]:
    """Ngữ nghĩa trải trên nhiều chunk vẫn tìm thấy 1 chunk: ghép thêm chunk liền kề (cùng tài
    liệu) rồi nối các chunk liên tiếp thành 1 đoạn ngữ cảnh. Chunk trùng giữa nhiều kết quả chỉ
    xuất hiện 1 lần; thứ tự đoạn theo thứ hạng của kết quả tốt nhất trong đoạn.

    hits: [{"id", "content", "metadata", "distance"}] đã sắp theo khoảng cách tăng dần. Mỗi đoạn trả về giữ danh
    sách chunk (đánh dấu chunk nào là kết quả trúng, chunk nào chỉ là lân cận) và khoảng cách tốt nhất trong đoạn —
    để bước cắt theo ngân sách token giữ được chunk trúng trước, bỏ chunk lân cận trước."""
    known = {h["id"]: (h["content"], h["metadata"]) for h in hits}
    wanted = {
        _chunk_id(h["metadata"]["document_id"], h["metadata"]["chunk_index"] + offset)
        for h in hits
        for offset in range(-window, window + 1)
        if h["metadata"]["chunk_index"] + offset >= 0
    } - known.keys()
    if wanted:
        fetched = collection.get(ids=sorted(wanted))  # id không tồn tại (đầu/cuối tài liệu) tự bị bỏ qua
        known.update({cid: (c, m) for cid, c, m in zip(fetched["ids"], fetched["documents"], fetched["metadatas"])})

    rank: dict[tuple[int, int], int] = {}  # (document_id, chunk_index) -> thứ hạng tốt nhất
    distance: dict[tuple[int, int], float] = {}
    is_hit: set[tuple[int, int]] = set()
    for position, h in enumerate(hits):
        meta = h["metadata"]
        is_hit.add((meta["document_id"], meta["chunk_index"]))
        for offset in range(-window, window + 1):
            key = (meta["document_id"], meta["chunk_index"] + offset)
            if _chunk_id(*key) in known:
                rank[key] = min(rank.get(key, position), position)
                distance[key] = min(distance.get(key, h["distance"]), h["distance"])

    passages: list[dict] = []
    for document_id, index in sorted(rank):
        chunk = {
            "index": index,
            "content": known[_chunk_id(document_id, index)][0],
            "hit": (document_id, index) in is_hit,
        }
        last = passages[-1] if passages else None
        if last and last["metadata"]["document_id"] == document_id and last["metadata"]["chunk_indexes"][-1] == index - 1:
            last["metadata"]["chunk_indexes"].append(index)
            last["chunks"].append(chunk)
            last["rank"] = min(last["rank"], rank[(document_id, index)])
            last["distance"] = min(last["distance"], distance[(document_id, index)])
        else:
            passages.append({
                "metadata": {"document_id": document_id, "chunk_indexes": [index]},
                "chunks": [chunk],
                "rank": rank[(document_id, index)],
                "distance": distance[(document_id, index)],
            })
    passages.sort(key=lambda p: p["rank"])
    for p in passages:
        p["content"] = _BLOCK_SEP.join(c["content"] for c in p["chunks"])
        del p["rank"]
    return passages


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def drop_duplicates(hits: list[dict], cosine_threshold: float = DUPLICATE_COSINE) -> list[dict]:
    """DuplicateFilter. hits đã sắp theo khoảng cách tăng dần nên khi 2 chunk trùng, giữ chunk gần câu hỏi hơn.
    - Trùng nguyên văn (sau chuẩn hóa khoảng trắng/hoa thường): loại ở bất kỳ vị trí nào.
    - Trùng ngữ nghĩa (cosine giữa 2 vector chunk >= ngưỡng): chỉ xét giữa các tài liệu KHÁC nhau.
    Mỗi hit cần có "embedding" (numpy) cho bước ngữ nghĩa; thiếu embedding thì chỉ lọc nguyên văn."""
    kept: list[dict] = []
    seen_text: set[str] = set()
    for hit in hits:
        normalized = _normalize_text(hit["content"])
        if normalized in seen_text:
            continue
        vector = hit.get("embedding")
        duplicate = False
        if vector is not None:
            for other in kept:
                other_vector = other.get("embedding")
                if other_vector is None or other["metadata"]["document_id"] == hit["metadata"]["document_id"]:
                    continue
                if float(np.dot(vector, other_vector)) >= cosine_threshold:  # vector đã chuẩn hóa: dot = cosine
                    duplicate = True
                    break
        if duplicate:
            continue
        seen_text.add(normalized)
        kept.append(hit)
    return kept


def group_candidates(hits: list[dict]) -> list[float]:
    """Gộp các chunk trúng LIỀN KỀ nhau trong cùng tài liệu thành 1 ứng viên (chúng cùng nói về 1 vùng nội dung,
    và _expand_with_neighbors cũng nối chúng thành 1 đoạn). Trả khoảng cách tốt nhất của từng ứng viên, tăng dần."""
    by_document: dict[int, list[tuple[int, float]]] = {}
    for hit in hits:
        meta = hit["metadata"]
        by_document.setdefault(meta["document_id"], []).append((meta["chunk_index"], hit["distance"]))
    distances: list[float] = []
    for chunks in by_document.values():
        chunks.sort()
        previous_index = None
        for index, distance in chunks:
            if previous_index is not None and index == previous_index + 1:
                distances[-1] = min(distances[-1], distance)
            else:
                distances.append(distance)
            previous_index = index
    return sorted(distances)


@dataclass
class RetrievalResult:
    """Kết quả truy xuất + tín hiệu cho Decision Engine. Khoảng cách là BÌNH PHƯƠNG L2 của Chroma: NHỎ = liên quan."""

    passages: list[dict] = field(default_factory=list)  # đoạn ngữ cảnh (đã mở rộng lân cận), tốt nhất trước; chưa cắt theo ngân sách
    top_distance: float | None = None
    second_distance: float | None = None
    distance_gap: float | None = None  # second - top; None khi chỉ có <2 ứng viên
    candidate_count: int = 0  # số ứng viên (vùng nội dung) khác nhau đạt ngưỡng, TRƯỚC khi cắt còn rerank_top_n
    considered: int = 0  # số chunk Chroma trả về
    over_threshold: int = 0  # số chunk bị loại vì khoảng cách > ngưỡng
    duplicates_dropped: int = 0
    knowledge_empty: bool = False  # bot chưa có chunk nào

    def spread_is_ambiguous(self, max_candidate_count: int) -> bool:
        """Nhiều nguồn cùng liên quan ngang nhau (tín hiệu "cần thu hẹp yêu cầu") — chỉ là tín hiệu, không cắt bớt gì."""
        return (
            self.candidate_count > max_candidate_count
            and self.distance_gap is not None
            and self.distance_gap < DISTANCE_GAP_SMALL
        )


def retrieve(
    bot_id: int,
    question: str,
    *,
    top_k: int,
    distance_threshold: float,
    rerank_top_n: int,
    neighbors: int = NEIGHBOR_WINDOW,
) -> RetrievalResult:
    """RAG Controller: query top_k -> lọc theo ngưỡng khoảng cách -> loại trùng -> tính tín hiệu (top/second/gap/
    candidate_count) -> giữ rerank_top_n chunk tốt nhất -> mở rộng lân cận. Chưa cắt theo ngân sách token (do Context
    Builder quyết, vì ngân sách phụ thuộc phần còn lại của prompt — xem fit_passages_to_budget).

    Chọn đúng collection của bot đã tự giới hạn trong dữ liệu của 1 khách hàng (không cần where).
    "Rerank": chỉ có 1 model embedding trong tiến trình (~2,2 GB) và không có cross-encoder nào trên đĩa; cosine tính từ
    khoảng cách L2 là hàm ĐƠN ĐIỆU của khoảng cách nên KHÔNG đổi thứ hạng — bước này thực chất là cắt còn rerank_top_n
    chunk gần nhất. Rerank ngữ nghĩa thật cần model mới (xem báo cáo)."""
    collection = get_collection(bot_id)
    total = collection.count()
    if total == 0:
        return RetrievalResult(knowledge_empty=True)

    query_embedding = embed_texts([question])[0]
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, total),
        include=["documents", "metadatas", "distances", "embeddings"],
    )
    ids = result["ids"][0] if result["ids"] else []
    found = [
        {
            "id": cid,
            "content": doc,
            "metadata": meta,
            "distance": float(distance),
            "embedding": None if emb is None else np.asarray(emb, dtype=float),
        }
        for cid, doc, meta, distance, emb in zip(
            ids,
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
            result["embeddings"][0] if result.get("embeddings") is not None else [None] * len(ids),
        )
    ]  # Chroma đã sắp theo khoảng cách tăng dần

    within = [h for h in found if h["distance"] <= distance_threshold]
    unique = drop_duplicates(within)
    candidates = group_candidates(unique)
    outcome = RetrievalResult(
        top_distance=candidates[0] if candidates else None,
        second_distance=candidates[1] if len(candidates) > 1 else None,
        candidate_count=len(candidates),
        considered=len(found),
        over_threshold=len(found) - len(within),
        duplicates_dropped=len(within) - len(unique),
    )
    if outcome.second_distance is not None:
        outcome.distance_gap = outcome.second_distance - outcome.top_distance
    logger.info(
        "retrieve bot=%s considered=%d over_threshold=%d dup=%d candidates=%d top=%s gap=%s threshold=%.2f",
        bot_id, outcome.considered, outcome.over_threshold, outcome.duplicates_dropped, outcome.candidate_count,
        None if outcome.top_distance is None else round(outcome.top_distance, 3),
        None if outcome.distance_gap is None else round(outcome.distance_gap, 3), distance_threshold,
    )
    if not unique:
        return outcome

    best = unique[:max(rerank_top_n, 1)]
    outcome.passages = _expand_with_neighbors(collection, best, neighbors) if neighbors > 0 else [
        {
            "metadata": {"document_id": h["metadata"]["document_id"], "chunk_indexes": [h["metadata"]["chunk_index"]]},
            "chunks": [{"index": h["metadata"]["chunk_index"], "content": h["content"], "hit": True}],
            "content": h["content"],
            "distance": h["distance"],
        }
        for h in best
    ]
    return outcome


def _truncate_to_tokens(text: str, budget: int) -> str:
    """Cắt text theo dòng cho vừa `budget` token (giữ phần đầu). Trả rỗng nếu ngay dòng đầu đã vượt."""
    lines = text.split("\n")
    kept: list[str] = []
    used = 0
    for line, tokens in zip(lines, count_tokens_many(lines)):
        if used + tokens + 1 > budget:
            break
        kept.append(line)
        used += tokens + 1
    return "\n".join(kept)


def fit_passages_to_budget(passages: list[dict], budget: int) -> tuple[list[dict], int]:
    """RAGTokenBudget: giữ các đoạn (đã sắp tốt nhất trước) sao cho tổng token <= budget. Đoạn không vừa được rút
    gọn: bỏ chunk lân cận trước, giữ chunk trúng. Đoạn ĐẦU TIÊN (tốt nhất) luôn còn lại — nếu chunk trúng của nó vẫn
    vượt ngân sách thì cắt theo dòng — để không bao giờ trả ngữ cảnh rỗng khi đã có ứng viên đạt ngưỡng.
    Trả (đoạn đã giữ, tổng token). Mỗi đoạn trả về là bản sao, có thêm "tokens"."""
    kept: list[dict] = []
    used = 0
    for position, passage in enumerate(passages):
        chunk_tokens = count_tokens_many([c["content"] for c in passage["chunks"]])
        separator = _BLOCK_SEP_TOKENS * (len(chunk_tokens) - 1)
        cost = sum(chunk_tokens) + separator
        room = budget - used
        if cost <= room:
            kept.append({**passage, "chunks": list(passage["chunks"]), "tokens": cost})
            used += cost
            continue
        # Rút gọn: chunk trúng trước (theo thứ tự tài liệu), rồi lân cận nếu còn chỗ
        order = sorted(range(len(chunk_tokens)), key=lambda i: (not passage["chunks"][i]["hit"], i))
        chosen: list[int] = []
        cost = 0
        for i in order:
            add = chunk_tokens[i] + (_BLOCK_SEP_TOKENS if chosen else 0)
            if cost + add <= room:
                chosen.append(i)
                cost += add
        if chosen:
            chosen.sort()
            chunks = [passage["chunks"][i] for i in chosen]
            kept.append({**passage, "chunks": chunks, "content": _BLOCK_SEP.join(c["content"] for c in chunks), "tokens": cost})
            used += cost
        elif position == 0:
            text = _truncate_to_tokens(passage["chunks"][order[0]]["content"], max(room, 0))
            if text:
                cost = count_tokens_many([text])[0]
                kept.append({**passage, "chunks": [{**passage["chunks"][order[0]], "content": text}], "content": text, "tokens": cost})
                used += cost
        break  # đã hết chỗ: các đoạn kém liên quan hơn không được thêm nữa
    return kept, used


# ---- Thu hẹp phạm vi: quá nhiều nội dung tìm được so với ngân sách ----

PASSAGE_SEPARATOR = "\n\n---\n\n"  # ngăn cách các đoạn trong phần "Thông tin tham khảo" của prompt
MIN_EXCERPT_TOKENS = 24  # phần mở đầu của 1 chunk ngắn hơn mức này thì không đủ để khách/AI nhận ra chunk nói về gì


def _hit_chunks(passages: list[dict]) -> list[tuple[dict, dict]]:
    """Các chunk TRÚNG (không tính chunk lân cận), theo thứ tự ưu tiên của đoạn: [(đoạn, chunk)]."""
    return [(p, c) for p in passages for c in p["chunks"] if c["hit"]]


def hit_tokens(passages: list[dict]) -> tuple[int, int]:
    """(tổng token nếu đưa NGUYÊN VĂN mọi chunk trúng vào prompt kể cả ký tự ngăn cách, số chunk trúng). Chunk lân cận
    không tính: chúng chỉ là phần thêm cho đủ ngữ cảnh và luôn bị bỏ trước khi hết ngân sách."""
    chunks = _hit_chunks(passages)
    if not chunks:
        return 0, 0
    counts = count_tokens_many([c["content"] for _, c in chunks] + [PASSAGE_SEPARATOR])
    return sum(counts[:-1]) + counts[-1] * (len(chunks) - 1), len(chunks)


def _head_tokens_batch(items: list[tuple[str, int]]) -> list[str]:
    tokenizer = _tokenizer()
    heads = []
    for text, budget in items:
        ids = tokenizer(text, add_special_tokens=False)["input_ids"]
        if len(ids) <= budget:
            heads.append(text)
        else:  # chừa 1 token cho dấu "…" báo phần sau đã bị cắt
            heads.append(tokenizer.decode(ids[:max(budget - 1, 1)], skip_special_tokens=True).rstrip() + " …")
    return heads


def _waterfill(sizes: list[int], room: int) -> list[int]:
    """Chia `room` token cho các chunk: chunk ngắn hơn phần chia đều thì lấy trọn, phần dư chia tiếp cho chunk dài."""
    allowance = [0] * len(sizes)
    remaining = room
    for done, index in enumerate(sorted(range(len(sizes)), key=sizes.__getitem__)):
        share = max(remaining, 0) // (len(sizes) - done)
        allowance[index] = min(sizes[index], share)
        remaining -= allowance[index]
    return allowance


def excerpt_passages(passages: list[dict], budget: int) -> list[dict]:
    """Khi tổng chunk trúng vượt ngân sách: thay vì bỏ hẳn các chunk kém liên quan, đưa PHẦN MỞ ĐẦU của TỪNG chunk trúng
    (đường dẫn heading nằm ở đầu chunk nên vẫn cho biết chunk nói về mục nào) sao cho tổng <= budget. Chunk ngắn giữ
    nguyên, phần token dư chia tiếp cho chunk dài. Nhiều tới mức mỗi chunk chưa được MIN_EXCERPT_TOKENS thì chỉ giữ các chunk
    liên quan nhất (đủ mỗi chunk MIN_EXCERPT_TOKENS). Mỗi chunk thành 1 đoạn riêng, không kèm chunk lân cận."""
    chunks = _hit_chunks(passages)
    if not chunks or budget <= 0:
        return []
    counts = count_tokens_many([c["content"] for _, c in chunks] + [PASSAGE_SEPARATOR])
    separator, sizes = counts[-1], counts[:-1]

    keep = len(chunks)
    while keep > 1 and (budget - separator * (keep - 1)) // keep < MIN_EXCERPT_TOKENS:
        keep -= 1
    chunks, sizes = chunks[:keep], sizes[:keep]
    allowance = _waterfill(sizes, budget - separator * (keep - 1))
    heads = run_blocking(_head_tokens_batch, [(c["content"], a) for (_, c), a in zip(chunks, allowance)])

    return [
        {
            "metadata": {"document_id": p["metadata"]["document_id"], "chunk_indexes": [c["index"]]},
            "chunks": [{"index": c["index"], "content": head, "hit": True, "truncated": head != c["content"]}],
            "content": head,
            "distance": p["distance"],
            "tokens": allowed,
        }
        for (p, c), head, allowed in zip(chunks, heads, allowance)
    ]


