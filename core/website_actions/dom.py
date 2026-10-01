"""DOM nhẹ dựng từ HTML bằng thư viện chuẩn (html.parser) — CHỈ dùng thư viện chuẩn, không phụ thuộc gói ngoài.

Hai việc:
1. reduce(): rút gọn HTML để gửi LLM — bỏ <script>/<style>/svg/comment nhưng GIỮ đủ "địa chỉ" widget sẽ dùng để thao tác DOM thật (id, class, name, type,
   href, action, data-*, role, aria-label...). Vượt ngân sách thì chuyển sang chế độ chỉ-phần-tử-tương-tác thay vì cắt cụt giữa chừng.
2. select(): bộ khớp selector CSS TẬP CON (tag, #id, .class, [attr], [attr=v|^=|$=|*=], tổ hợp " " và ">"). Dùng để KIỂM CHỨNG selector do LLM đề xuất có
   thật trong trang đã crawl (loại selector bịa) — tập con này cũng chính là tập selector prompt cho phép LLM dùng, nên widget (querySelector) hiểu trọn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "template", "iframe", "canvas", "link", "meta", "object", "embed"})
INTERACTIVE_TAGS = frozenset({"a", "button", "input", "select", "textarea", "form", "label", "option"})
# Thuộc tính giữ lại khi rút gọn (đủ để dựng selector và hiểu ý nghĩa phần tử). data-* luôn được giữ.
KEEP_ATTRS = ("id", "class", "name", "type", "href", "action", "method", "value", "placeholder", "role", "aria-label", "title", "alt", "for", "onclick", "autocomplete")
MAX_TEXT_CHARS = 100
MAX_ATTR_CHARS = 120


@dataclass(eq=False)
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list["Node"] = field(default_factory=list)
    text: str = ""            # văn bản trực tiếp của phần tử (gộp các text node con)
    parent: "Node | None" = None

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        yield self
        for child in self.children:
            yield from child.iter()

    def all_text(self, limit: int = 200) -> str:
        parts = []
        for node in self.iter():
            if node.text:
                parts.append(node.text)
        return re.sub(r"\s+", " ", " ".join(parts)).strip()[:limit]


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if self._skip_depth:
            if tag in SKIP_TAGS and tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in SKIP_TAGS:
            if tag not in VOID_TAGS:
                self._skip_depth = 1
            return
        node = Node(tag, {name: (value if value is not None else "") for name, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        # <div/> kiểu tự đóng: HTML5 coi như thẻ mở, nhưng với thẻ void/lạ ta không đẩy vào stack
        if self._skip_depth:
            return
        if tag in SKIP_TAGS:
            return
        node = Node(tag, {name: (value if value is not None else "") for name, value in attrs}, parent=self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag in SKIP_TAGS and tag not in VOID_TAGS:
                self._skip_depth -= 1
            return
        for i in range(len(self.stack) - 1, 0, -1):  # đóng tới phần tử mở gần nhất cùng tên; thẻ đóng thừa bị bỏ qua
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if self._skip_depth or len(self.stack) < 1:
            return
        text = data.strip()
        if text:
            node = self.stack[-1]
            node.text = (node.text + " " + text).strip() if node.text else text


def parse(html: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


# ---------------------------------------------------------------- rút gọn để gửi LLM

def _attr_repr(node: Node) -> str:
    parts = []
    for name, value in node.attrs.items():
        if name in KEEP_ATTRS or name.startswith("data-"):
            value = re.sub(r"\s+", " ", value).strip()
            if value:
                parts.append(f'{name}="{value[:MAX_ATTR_CHARS]}"')
    return (" " + " ".join(parts)) if parts else ""


def _has_signal(node: Node) -> bool:
    return bool(node.attrs.get("id") or node.attrs.get("class") or node.tag in INTERACTIVE_TAGS or any(k.startswith("data-") for k in node.attrs))


def _render_full(node: Node, depth: int, out: list[str]) -> None:
    for child in node.children:
        if not _has_signal(child) and not child.children:
            if child.text:
                out.append(" " * depth + child.text[:MAX_TEXT_CHARS])
            continue
        text = child.text[:MAX_TEXT_CHARS]
        out.append(f"{' ' * depth}<{child.tag}{_attr_repr(child)}>{text}")
        _render_full(child, min(depth + 1, 12), out)


def _render_interactive(root: Node) -> list[str]:
    out = []
    for node in root.iter():
        if node.tag in INTERACTIVE_TAGS:
            out.append(f"<{node.tag}{_attr_repr(node)}>{node.all_text(MAX_TEXT_CHARS)}")
    return out


def reduce(root: Node, max_chars: int) -> str:
    """HTML rút gọn dạng văn bản (mỗi phần tử 1 dòng, thụt lề theo độ sâu), không vượt max_chars."""
    title = next((n for n in root.iter() if n.tag == "title"), None)
    lines: list[str] = []
    _render_full(root, 0, lines)
    text = "\n".join(lines)
    if len(text) > max_chars:  # quá dài: chỉ giữ phần tử tương tác (địa chỉ thao tác) thay vì cắt cụt nửa trang
        text = "\n".join(_render_interactive(root))
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n<!-- đã cắt bớt vì quá dài -->"
    return (f"<title>{title.text}</title>\n" if title and title.text else "") + text


# ---------------------------------------------------------------- bộ khớp selector tập con

class SelectorError(ValueError):
    """Selector nằm ngoài tập con được hỗ trợ (hoặc sai cú pháp)."""


_IDENT = r"[A-Za-z_][\w-]*"
_TOKEN_RE = re.compile(
    rf"""\s*(?:
        (?P<comb>>)
      | (?P<tag>{_IDENT}|\*)
      | \#(?P<id>{_IDENT})
      | \.(?P<cls>{_IDENT})
      | \[\s*(?P<attr>[\w:-]+)\s*(?:(?P<op>[\^$*]?=)\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\]\s]+)))?\s*\]
    )""",
    re.VERBOSE,
)
MAX_SELECTOR_CHARS = 300


@dataclass
class _Compound:
    tag: str | None = None
    id: str | None = None
    classes: list[str] = field(default_factory=list)
    attrs: list[tuple[str, str | None, str | None]] = field(default_factory=list)  # (tên, toán tử, giá trị)


def parse_selector(selector: str) -> list[tuple[str, _Compound]]:
    """-> [(combinator, compound)]; combinator của phần tử đầu là ''. Ném SelectorError nếu ngoài tập con."""
    if not isinstance(selector, str) or not selector.strip() or len(selector) > MAX_SELECTOR_CHARS:
        raise SelectorError("selector rỗng hoặc quá dài")
    parts: list[tuple[str, _Compound]] = []
    pos, comb, current = 0, "", _Compound()
    started = False
    text = selector.strip()
    while pos < len(text):
        gap = re.match(r"\s+", text[pos:])
        if gap:  # khoảng trắng = tổ hợp con cháu (nếu không đứng cạnh '>')
            pos += gap.end()
            nxt = text[pos:pos + 1]
            if started and nxt and nxt != ">":
                parts.append((comb, current))
                comb, current, started = " ", _Compound(), False
            continue
        m = _TOKEN_RE.match(text, pos)
        if not m or m.end() == pos:
            raise SelectorError(f"không đọc được selector tại vị trí {pos}")
        pos = m.end()
        if m.group("comb"):
            if not started:
                raise SelectorError("tổ hợp '>' thiếu vế trái")
            parts.append((comb, current))
            comb, current, started = ">", _Compound(), False
            continue
        started = True
        if m.group("tag"):
            if current.tag or current.id or current.classes or current.attrs:
                raise SelectorError("thẻ phải đứng đầu selector đơn")
            current.tag = None if m.group("tag") == "*" else m.group("tag").lower()
        elif m.group("id"):
            current.id = m.group("id")
        elif m.group("cls"):
            current.classes.append(m.group("cls"))
        else:
            value = m.group("dq") if m.group("dq") is not None else m.group("sq") if m.group("sq") is not None else m.group("bare")
            current.attrs.append((m.group("attr").lower(), m.group("op"), value))
    if not started:
        raise SelectorError("selector kết thúc bằng tổ hợp")
    parts.append((comb, current))
    return parts


def _matches(node: Node, comp: _Compound) -> bool:
    if comp.tag and node.tag != comp.tag:
        return False
    if comp.id is not None and node.attrs.get("id") != comp.id:
        return False
    if comp.classes and not set(comp.classes) <= node.classes():
        return False
    for name, op, value in comp.attrs:
        if name not in node.attrs:
            return False
        actual = node.attrs[name]
        if op == "=" and actual != value:
            return False
        if op == "^=" and not (value and actual.startswith(value)):
            return False
        if op == "$=" and not (value and actual.endswith(value)):
            return False
        if op == "*=" and not (value and value in actual):
            return False
    return True


def _match_chain(node: Node, parts: list, index: int) -> bool:
    """Khớp parts[0..index] với node là phần tử cuối (index) — duyệt từ phải sang trái."""
    comb, comp = parts[index]
    if not _matches(node, comp):
        return False
    if index == 0:
        return True
    left_comb = comb
    parent = node.parent
    if left_comb == ">":
        return parent is not None and parent.tag != "#root" and _match_chain(parent, parts, index - 1)
    while parent is not None and parent.tag != "#root":
        if _match_chain(parent, parts, index - 1):
            return True
        parent = parent.parent
    return False


def select(root: Node, selector: str) -> list[Node]:
    """Các phần tử khớp selector (theo thứ tự tài liệu). Ném SelectorError nếu selector ngoài tập con được hỗ trợ."""
    parts = parse_selector(selector)
    return [node for node in root.iter() if node.tag != "#root" and _match_chain(node, parts, len(parts) - 1)]
