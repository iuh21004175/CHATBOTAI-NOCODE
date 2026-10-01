"""App Spec (AB0): bản đặc tả có cấu trúc do LLM đề xuất — NGUỒN SỰ THẬT để sinh schema DB, code giao diện (sau này cả bộ tool cho chatbot).

LLM chỉ ĐỀ XUẤT; `validate` kiểm chứng chặt và chuẩn hoá trước khi lưu/dùng (không tin LLM): tên định danh an toàn cho SQL/JS, kiểu field trong tập
cố định, tham chiếu (ref/screen/stat) phải trỏ tới collection/field có thật, trần số lượng theo Config. Mọi thứ dùng làm tên bảng/cột đều đi qua
`IDENT_RE` nên nối vào SQL/JS/HTML sau này không thể chứa ký tự nguy hiểm.
"""
from __future__ import annotations

import json
import re

from config import Config
from core.context_engine import structured

IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
RESERVED_FIELDS = frozenset({"id", "created_at", "updated_at"})
FIELD_TYPES = ("string", "text", "integer", "decimal", "boolean", "date", "datetime", "ref")
SCREEN_TYPES = ("table", "dashboard")
ACTIONS = ("view", "create", "update", "delete")  # quyền theo collection; create/update/delete luôn kèm view
MAX_ROLES = 8
AGGREGATES = ("count", "sum", "avg", "min", "max")
NUMERIC_TYPES = ("integer", "decimal")
MAX_LABEL = 120
MAX_DESCRIPTION = 600


class SpecError(ValueError):
    """Spec không đúng hợp đồng; message tiếng Việt, đủ cụ thể để đưa lại cho LLM sửa hoặc hiện cho người dùng."""


def _text(value, what: str, *, limit: int = MAX_LABEL, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise SpecError(f"{what} phải là chuỗi không rỗng")
    return value.strip()[:limit]


def _ident(value, what: str) -> str:
    if not isinstance(value, str) or not IDENT_RE.match(value):
        raise SpecError(f'{what} "{value}" không hợp lệ: chỉ gồm chữ thường a-z, số, gạch dưới, bắt đầu bằng chữ, tối đa 40 ký tự')
    return value


def validate(raw) -> dict:
    if not isinstance(raw, dict):
        raise SpecError("Spec phải là một đối tượng JSON")
    name = _text(raw.get("name"), "name", limit=100)
    description = _text(raw.get("description", ""), "description", limit=MAX_DESCRIPTION, required=False)

    raw_collections = raw.get("collections")
    if not isinstance(raw_collections, list) or not raw_collections:
        raise SpecError("collections phải là danh sách có ít nhất 1 phần tử")
    if len(raw_collections) > Config.BUILDER_MAX_COLLECTIONS:
        raise SpecError(f"tối đa {Config.BUILDER_MAX_COLLECTIONS} collection")

    collections: dict[str, dict] = {}
    for item in raw_collections:
        if not isinstance(item, dict):
            raise SpecError("mỗi collection phải là đối tượng")
        cname = _ident(item.get("name"), "tên collection")
        if cname in collections:
            raise SpecError(f'collection "{cname}" bị trùng')
        raw_fields = item.get("fields")
        if not isinstance(raw_fields, list) or not raw_fields:
            raise SpecError(f'collection "{cname}" phải có ít nhất 1 field')
        if len(raw_fields) > Config.BUILDER_MAX_FIELDS:
            raise SpecError(f'collection "{cname}" có quá {Config.BUILDER_MAX_FIELDS} field')
        fields, seen = [], set()
        for f in raw_fields:
            if not isinstance(f, dict):
                raise SpecError(f'field của "{cname}" phải là đối tượng')
            fname = _ident(f.get("name"), f'tên field của "{cname}"')
            if fname in RESERVED_FIELDS:
                raise SpecError(f'field "{fname}" của "{cname}" trùng tên cột hệ thống ({", ".join(sorted(RESERVED_FIELDS))})')
            if fname in seen:
                raise SpecError(f'field "{cname}.{fname}" bị trùng')
            seen.add(fname)
            ftype = f.get("type")
            if ftype not in FIELD_TYPES:
                raise SpecError(f'field "{cname}.{fname}": type phải thuộc {", ".join(FIELD_TYPES)}')
            field = {
                "name": fname, "label": _text(f.get("label") or fname, f'label của "{cname}.{fname}"'), "type": ftype,
                "required": f.get("required") is True,
            }
            if ftype == "ref":
                field["ref"] = _ident(f.get("ref"), f'ref của "{cname}.{fname}"')
            fields.append(field)
        collections[cname] = {"name": cname, "label": _text(item.get("label") or cname, f'label của "{cname}"'), "fields": fields}

    for coll in collections.values():  # ref phải trỏ tới collection có thật (kiểm sau khi đã biết đủ tên)
        for field in coll["fields"]:
            if field["type"] == "ref" and field["ref"] not in collections:
                raise SpecError(f'field "{coll["name"]}.{field["name"]}" tham chiếu collection "{field["ref"]}" không tồn tại')

    raw_screens = raw.get("screens")
    if not isinstance(raw_screens, list) or not raw_screens:
        raise SpecError("screens phải là danh sách có ít nhất 1 phần tử")
    if len(raw_screens) > Config.BUILDER_MAX_SCREENS:
        raise SpecError(f"tối đa {Config.BUILDER_MAX_SCREENS} màn hình")
    screens, screen_ids = [], set()
    for item in raw_screens:
        if not isinstance(item, dict):
            raise SpecError("mỗi màn hình phải là đối tượng")
        sid = _ident(item.get("id"), "id màn hình")
        if sid in screen_ids:
            raise SpecError(f'màn hình "{sid}" bị trùng')
        screen_ids.add(sid)
        stype = item.get("type")
        if stype not in SCREEN_TYPES:
            raise SpecError(f'màn hình "{sid}": type phải thuộc {", ".join(SCREEN_TYPES)}')
        screen = {"id": sid, "title": _text(item.get("title"), f'title của màn hình "{sid}"'), "type": stype,
                  "description": _text(item.get("description", ""), f'description của màn hình "{sid}"', limit=MAX_DESCRIPTION, required=False)}
        if stype == "table":
            coll = item.get("collection")
            if coll not in collections:
                raise SpecError(f'màn hình "{sid}" trỏ tới collection "{coll}" không tồn tại')
            screen["collection"] = coll
        else:
            stats = item.get("stats", [])
            if not isinstance(stats, list) or len(stats) > 12:
                raise SpecError(f'stats của màn hình "{sid}" phải là danh sách tối đa 12 phần tử')
            screen["stats"] = [_stat(s, sid, collections) for s in stats]
        screens.append(screen)

    roles = _roles(raw.get("roles", []), collections)
    return {"name": name, "description": description, "collections": list(collections.values()), "screens": screens, "roles": roles}


def _roles(raw_roles, collections: dict) -> list[dict]:
    """Vai trò + ma trận quyền (vai trò × collection × hành động). Tuỳ chọn: Spec không có vai trò = chỉ Chủ nhóm/Quản trị viên dùng được app.

    Quy tắc an toàn kiểm Ở ĐÂY (không tin LLM, không tự nới quyền ngầm): create/update/delete bắt buộc kèm view cùng collection; và vai trò dùng 1 collection có field ref
    thì phải được view collection được ref (nếu không form/bảng không dựng được nhãn) — thiếu thì BÁO LỖI để Spec được sửa, thay vì tự cấp thêm quyền."""
    if not isinstance(raw_roles, list) or len(raw_roles) > MAX_ROLES:
        raise SpecError(f"roles phải là danh sách tối đa {MAX_ROLES} vai trò")
    roles, seen = [], set()
    for item in raw_roles:
        if not isinstance(item, dict):
            raise SpecError("mỗi vai trò phải là đối tượng")
        rid = _ident(item.get("id"), "id vai trò")
        if rid in seen:
            raise SpecError(f'vai trò "{rid}" bị trùng')
        seen.add(rid)
        raw_perms = item.get("permissions", {})
        if not isinstance(raw_perms, dict):
            raise SpecError(f'permissions của vai trò "{rid}" phải là đối tượng {{collection: [hành động]}}')
        perms: dict[str, list[str]] = {}
        for cname, actions in raw_perms.items():
            if cname not in collections:
                raise SpecError(f'vai trò "{rid}" cấp quyền cho collection "{cname}" không tồn tại')
            if not isinstance(actions, list) or any(a not in ACTIONS for a in actions):
                raise SpecError(f'quyền của vai trò "{rid}" trên "{cname}" phải là danh sách thuộc {", ".join(ACTIONS)}')
            granted = set(actions)
            if granted and "view" not in granted:
                raise SpecError(f'vai trò "{rid}" có quyền ghi trên "{cname}" nhưng thiếu "view"')
            if granted:
                perms[cname] = [a for a in ACTIONS if a in granted]
        for cname in perms:
            for f in collections[cname]["fields"]:
                if f["type"] == "ref" and "view" not in perms.get(f["ref"], []):
                    raise SpecError(f'vai trò "{rid}" dùng "{cname}" (có field tham chiếu "{f["ref"]}") nên cũng cần quyền "view" trên "{f["ref"]}"')
        roles.append({"id": rid, "label": _text(item.get("label") or rid, f'label của vai trò "{rid}"'),
                      "description": _text(item.get("description", ""), f'description của vai trò "{rid}"', limit=MAX_DESCRIPTION, required=False), "permissions": perms})
    return roles


def _stat(item, sid: str, collections: dict) -> dict:
    if not isinstance(item, dict):
        raise SpecError(f'stat của màn hình "{sid}" phải là đối tượng')
    coll, agg = item.get("collection"), item.get("agg")
    if coll not in collections:
        raise SpecError(f'stat của "{sid}" trỏ tới collection "{coll}" không tồn tại')
    if agg not in AGGREGATES:
        raise SpecError(f'stat của "{sid}": agg phải thuộc {", ".join(AGGREGATES)}')
    stat = {"label": _text(item.get("label"), f'label của stat trong "{sid}"'), "collection": coll, "agg": agg}
    if agg != "count":
        by_name = {f["name"]: f for f in collections[coll]["fields"]}
        field = by_name.get(item.get("field"))
        if field is None or field["type"] not in NUMERIC_TYPES:
            raise SpecError(f'stat "{agg}" của "{sid}" cần field số (integer/decimal) có thật trong "{coll}"')
        stat["field"] = field["name"]
    return stat


def parse(text: str) -> dict:
    """Văn bản LLM trả -> Spec đã kiểm chứng. SpecError nếu không phải JSON hoặc sai hợp đồng."""
    payload = structured.extract_json(text)
    if not payload:
        raise SpecError("phản hồi rỗng")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise SpecError(f"không phải JSON hợp lệ: {exc.msg}") from exc
    return validate(data)


def collection(spec: dict, name: str) -> dict | None:
    return next((c for c in spec["collections"] if c["name"] == name), None)
