"""Sinh Spec và code giao diện bằng LLM (AB0). Không đụng DB/Flask — chỉ nhận hàm gọi LLM nên test được không cần mạng (cùng khuôn core/website_actions/analysis.py).

Phân vai (xem định hướng mục 2): LLM chỉ viết PHẦN LOGIC HIỂN THỊ của từng màn hình — 1 file JS ráp từ UI Kit + platform-sdk (static/builder). Khung HTML của
mọi trang (nạp script đúng thứ tự, thanh điều hướng, meta, CSS) do NỀN TẢNG dựng cố định ở `build_page`: LLM không viết được thẻ script/inline handler
nào, CSP chặt (script-src 'self') chạy được, và mọi trang nhất quán. Mỗi JS qua quét tĩnh (scan.py); lỗi -> đưa lại cho LLM sửa tối đa Config.BUILDER_MAX_FIX_ROUNDS vòng.
"""
from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass, field

from app.builder import scan, spec as spec_mod
from config import Config
from core.context_engine import structured
from core.context_engine.cost import LLMUsageTracker, parse_usage

logger = logging.getLogger("builder.codegen")

SPEC_SYSTEM_PROMPT = """Bạn là kiến trúc sư phần mềm. Từ mô tả của người dùng (tiếng Việt), hãy thiết kế một ứng dụng quản lý nhỏ gọn và trả về DUY NHẤT một đối tượng JSON (json), không kèm giải thích:
{
 "name": "Quản lý kho điện máy",
 "description": "1-2 câu mô tả",
 "collections": [
   {"name": "san_pham", "label": "Sản phẩm", "fields": [
      {"name": "ten", "label": "Tên sản phẩm", "type": "string", "required": true},
      {"name": "gia", "label": "Giá bán", "type": "decimal", "required": false},
      {"name": "kho_id", "label": "Kho", "type": "ref", "ref": "kho", "required": true}
   ]}
 ],
 "screens": [
   {"id": "tong_quan", "title": "Tổng quan", "type": "dashboard", "description": "...", "stats": [{"label": "Số sản phẩm", "collection": "san_pham", "agg": "count"}, {"label": "Tổng tồn", "collection": "ton_kho", "agg": "sum", "field": "so_luong"}]},
   {"id": "san_pham", "title": "Sản phẩm", "type": "table", "collection": "san_pham", "description": "..."}
 ],
 "roles": [
   {"id": "thu_kho", "label": "Thủ kho", "description": "Nhập kho, xem tồn", "permissions": {"san_pham": ["view"], "kho": ["view"], "phieu_nhap": ["view", "create", "update"]}},
   {"id": "quan_ly", "label": "Quản lý kho", "description": "Toàn quyền nghiệp vụ kho", "permissions": {"san_pham": ["view", "create", "update", "delete"], "kho": ["view", "create", "update", "delete"], "phieu_nhap": ["view", "create", "update", "delete"]}}
 ]
}
Quy tắc:
- "name" (tên ứng dụng) là tiếng Việt có dấu, ngắn gọn. Tên collection/field/màn hình (name, id): chữ thường không dấu a-z, số, gạch dưới, bắt đầu bằng chữ, tối đa 40 ký tự. Không đặt field tên id, created_at, updated_at (hệ thống tự có).
- type của field CHỈ thuộc: string (≤255 ký tự), text (văn bản dài), integer, decimal (tiền, số lẻ), boolean, date, datetime, ref (tham chiếu tới 1 bản ghi của collection khác; bắt buộc có "ref": tên collection đó).
- Quan hệ giữa các bảng dùng field type "ref". Ví dụ phiếu nhập có sản phẩm -> field "san_pham_id" type ref, ref "san_pham".
- screens: type "table" (danh sách + thêm/sửa/xoá cho 1 collection, bắt buộc có "collection") hoặc "dashboard" (thẻ thống kê; "stats" với agg count|sum|avg|min|max; sum/avg/min/max bắt buộc có "field" là field integer/decimal).
- Mỗi collection nên có 1 màn hình table. Có thể thêm 1 màn hình dashboard đầu tiên.
- roles: 2-4 vai trò nghiệp vụ (người dùng là thành viên nhóm, KHÔNG thiết kế đăng nhập). permissions = {tên collection: danh sách hành động}, hành động thuộc view|create|update|delete. Theo nguyên tắc quyền tối thiểu: vai trò chỉ xem thì chỉ có "view"; chỉ vai trò quản lý mới có "delete". Mọi vai trò đã có create/update/delete trên 1 collection BẮT BUỘC có thêm "view" trên cùng collection. Nếu collection có field ref tới collection khác thì vai trò dùng nó BẮT BUỘC có "view" trên collection được ref (vd phiếu nhập có ref "san_pham" -> vai trò có phieu_nhap phải có "san_pham": ["view"]). Collection không liệt kê = vai trò đó không truy cập được. Chủ nhóm/Quản trị viên luôn toàn quyền, KHÔNG tạo vai trò cho họ.
- Chỉ thiết kế dữ liệu, màn hình và vai trò. Giữ gọn: tối đa 8 collection, 10 field mỗi collection.
- Mọi nhãn (label, title) bằng tiếng Việt có dấu."""

UI_KIT_REFERENCE = """THƯ VIỆN CÓ SẴN (đã nạp trong trang, KHÔNG được tự thêm thư viện, KHÔNG viết lại): jQuery ($), PF (platform-sdk), UI (UI Kit).

PF — gọi dữ liệu (tất cả trả Promise của jQuery: dùng .then(ok, fail); lỗi fail nhận {status, message, errors}):
  PF.records.list(collection, {page, per_page, q, sort, order, filters}) -> {items:[{id, ...field, created_at, updated_at}], total, page, per_page}
      q = tìm trong các field chữ; sort = tên field hoặc id/created_at; order = 'asc'|'desc'; filters = {field: giá trị} (so sánh bằng)
  PF.records.get(collection, id)            PF.records.create(collection, data)
  PF.records.update(collection, id, data)   PF.records.remove(collection, id)
  PF.records.aggregate(collection, {agg: 'count'|'sum'|'avg'|'min'|'max', field, filters}) -> {value}
  PF.can(collection, action) -> true/false, action thuộc 'view'|'create'|'update'|'delete': quyền của người đang dùng (UI.crudTable tự áp dụng; chỉ cần khi tự vẽ nút ghi). Server luôn kiểm tra lại.
  PF.schema() -> Promise(Spec: {collections:[{name,label,fields:[{name,label,type,required,ref}]}], screens})
  Giá trị decimal trả về là CHUỖI số (vd "12500"); ref là id số.

UI — thành phần giao diện (dựng bằng jQuery, tự escape dữ liệu):
  UI.crudTable($container, {collection, columns?: ['ten','gia'], pageSize?: 20, canCreate?: true, canEdit?: true, canDelete?: true})
      Bảng đầy đủ: tìm kiếm, sắp xếp, phân trang, nút Thêm/Sửa/Xoá (form + xác nhận xoá tự có). Đây là cách chuẩn để làm màn hình danh sách. Trả về {reload()}.
  UI.stats($container, [{label, collection, agg, field?}])   Các thẻ số liệu.
  UI.card($container, title) -> $body        Khối có tiêu đề, trả vùng nội dung để đặt thành phần khác vào.
  UI.form(collection, {record?, onSubmit(data) -> Promise, onCancel?, submitLabel?}) -> $form
  UI.modal({title, content: $element, width?}) -> {close()}
  UI.confirm(message) -> Promise (resolve true nếu đồng ý)
  UI.toast(message, 'success'|'error'|'info')
  UI.escape(text) -> chuỗi đã escape HTML (chỉ cần khi thật sự phải ghép chuỗi; ưu tiên .text())
  UI.format(value, fieldDef) -> chuỗi hiển thị (số tiền, ngày, boolean)

Trang có sẵn phần tử <div id="screen"></div> để bạn dựng nội dung vào. Tiêu đề trang và thanh điều hướng do nền tảng dựng."""

SCREEN_SYSTEM_PROMPT = """Bạn viết code JavaScript (jQuery thuần, ES5/ES6, không module) cho MỘT màn hình của ứng dụng web. Chỉ trả về nội dung file .js — không giải thích, không dùng ```.

""" + UI_KIT_REFERENCE + """

QUY TẮC BẮT BUỘC (code vi phạm sẽ bị máy quét từ chối):
- Bọc toàn bộ trong $(function () { ... });  Dựng nội dung vào $('#screen').
- Ưu tiên ráp từ UI.crudTable / UI.stats / UI.card. Màn hình 'table' của 1 collection thường chỉ cần UI.crudTable. Chỉ viết thêm logic khi mô tả màn hình yêu cầu rõ.
- Dữ liệu CHỈ lấy qua PF.records.*. CẤM: fetch, XMLHttpRequest, $.ajax/$.get/$.post, .load(), WebSocket, eval, new Function, document.write, innerHTML/outerHTML, .html(...), localStorage/sessionStorage/cookie, URL tuyệt đối (http://, https://), window.open, thẻ <script>/<iframe>/<style>/<link>.
- Hiển thị dữ liệu người dùng chỉ bằng .text() hoặc $('<td>', {text: ...}). KHÔNG dựng HTML bằng nối chuỗi ('<td>' + x + '</td>').
- Không gắn thuộc tính sự kiện inline (onclick=...): dùng .on('click', fn). Không đặt style bằng thuộc tính; dùng class có sẵn: pf-row, pf-muted, pf-btn, pf-btn-primary.
- Chỉ dùng tên collection và field CÓ TRONG Spec được cung cấp. Không bịa."""


class CodegenError(RuntimeError):
    """LLM không tạo được kết quả hợp lệ sau số lần thử cho phép."""


def plain_call(temperature: float, max_tokens: int) -> structured.LLMCall:
    """LLMCall thật KHÔNG dùng JSON mode (code JS). thinking đã tắt ở core.llm_client."""
    from core.llm_client import get_llm

    llm = get_llm(temperature, max_tokens)

    def call(messages: list[dict]) -> structured.LLMReply:
        response = llm.invoke(messages)
        content = response.content if isinstance(response.content, str) else ""
        metadata = getattr(response, "response_metadata", None) or {}
        return structured.LLMReply(content=content, token_usage=metadata.get("token_usage"))

    return call


def generate_spec(prompt: str, call: structured.LLMCall, tracker: LLMUsageTracker) -> dict:
    messages = [{"role": "system", "content": SPEC_SYSTEM_PROMPT}, {"role": "user", "content": f"Mô tả ứng dụng (trả về json):\n{prompt}"}]
    last_error = None
    for attempt in range(structured.MAX_JSON_ATTEMPTS):
        reply = call(messages)
        tracker.record("spec" if attempt == 0 else "spec_retry", parse_usage(reply.token_usage))
        try:
            return spec_mod.parse(reply.content)
        except spec_mod.SpecError as exc:
            last_error = exc
            logger.warning("Spec LLM không hợp lệ (lần %d/%d): %s", attempt + 1, structured.MAX_JSON_ATTEMPTS, exc)
            messages += [{"role": "assistant", "content": reply.content}, {"role": "user", "content": f"JSON vừa rồi sai: {exc}. Trả lại json đầy đủ đã sửa."}]
    raise CodegenError(f"Không tạo được Spec hợp lệ sau {structured.MAX_JSON_ATTEMPTS} lần: {last_error}")


_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n(.*?)\n?```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    match = _FENCE.match(text)
    return (match.group(1) if match else text).strip()


def _screen_request(spec: dict, screen: dict) -> str:
    return (
        f"Spec ứng dụng:\n{json.dumps(spec, ensure_ascii=False)}\n\n"
        f"Viết file JS cho màn hình id=\"{screen['id']}\" (type={screen['type']}, tiêu đề \"{screen['title']}\")."
        + (f" Collection: {screen['collection']}." if screen["type"] == "table" else f" Các số liệu cần hiển thị: {json.dumps(screen.get('stats', []), ensure_ascii=False)}.")
        + (f"\nMô tả: {screen['description']}" if screen.get("description") else "")
    )


@dataclass
class ScreenResult:
    code: str
    problems: list[str] = field(default_factory=list)  # lỗi quét tĩnh CÒN LẠI sau khi hết vòng sửa


def generate_screen(spec: dict, screen: dict, call: structured.LLMCall, tracker: LLMUsageTracker) -> ScreenResult:
    collections = {c["name"] for c in spec["collections"]}
    path = f"{screen['id']}.js"
    messages = [{"role": "system", "content": SCREEN_SYSTEM_PROMPT}, {"role": "user", "content": _screen_request(spec, screen)}]
    code, problems = "", []
    for attempt in range(1 + Config.BUILDER_MAX_FIX_ROUNDS):
        reply = call(messages)
        tracker.record("screen" if attempt == 0 else "screen_fix", parse_usage(reply.token_usage), screen=screen["id"])
        code = _strip_fence(reply.content)
        problems = scan.scan_js(path, code, collections=collections) if code else [f"{path}: phản hồi rỗng"]
        if not problems:
            return ScreenResult(code)
        logger.warning("Màn hình %s: quét tĩnh báo %d lỗi (lần %d/%d)", screen["id"], len(problems), attempt + 1, 1 + Config.BUILDER_MAX_FIX_ROUNDS)
        messages += [
            {"role": "assistant", "content": reply.content},
            {"role": "user", "content": "Máy quét từ chối code vì:\n- " + "\n- ".join(problems[:10]) + "\nViết lại TOÀN BỘ file JS đã sửa, chỉ trả code."},
        ]
    return ScreenResult(code, problems)


# ----------------------------------------------------------------------------------------------------------- khung trang cố định
PAGE_TEMPLATE = """<!doctype html>
<html lang="vi">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {app}</title>
<meta name="pf-app" content="{public_id}">
<meta name="pf-csrf" content="__PF_CSRF__">
<meta name="pf-perms" content="__PF_PERMS__">
<link rel="stylesheet" href="/static/builder/ui-kit.css">
</head>
<body>
<div class="pf-layout">
<nav class="pf-nav"><div class="pf-brand">{app}</div>{links}</nav>
<main class="pf-main"><h1 class="pf-title">{title}</h1><div id="screen"></div></main>
</div>
<div id="pf-toasts"></div>
<script src="/static/builder/vendor/jquery-3.7.1.min.js"></script>
<script src="/static/builder/platform-sdk.js"></script>
<script src="/static/builder/ui-kit.js"></script>
<script src="{screen_id}.js"></script>
</body>
</html>
"""
CSRF_PLACEHOLDER = "__PF_CSRF__"
PERMS_PLACEHOLDER = "__PF_PERMS__"  # server điền {collection: [hành động]} của người đang xem lúc phục vụ trang (chỉ để giao diện ẩn nút/menu; server vẫn tự kiểm tra)


def _nav_collection(screen: dict) -> str:
    """Màn hình table gắn data-collection để ui-kit.js ẩn mục menu khi người xem không có quyền view collection đó."""
    return f' data-collection="{screen["collection"]}"' if screen["type"] == "table" else ""


def build_page(spec: dict, screen: dict, public_id: str) -> str:
    links = "".join(
        f'<a class="pf-nav-link{" active" if s["id"] == screen["id"] else ""}" href="{s["id"]}.html"{_nav_collection(s)}>{html.escape(s["title"])}</a>' for s in spec["screens"]
    )
    return PAGE_TEMPLATE.format(
        title=html.escape(screen["title"]), app=html.escape(spec["name"]), public_id=html.escape(public_id, quote=True), links=links, screen_id=screen["id"],
    )


def build_files(spec: dict, public_id: str, screen_code: dict[str, str]) -> dict[str, str]:
    files: dict[str, str] = {}
    for screen in spec["screens"]:
        files[f"{screen['id']}.html"] = build_page(spec, screen, public_id)
        files[f"{screen['id']}.js"] = screen_code[screen["id"]]
    files["index.html"] = files[f"{spec['screens'][0]['id']}.html"]
    return files
