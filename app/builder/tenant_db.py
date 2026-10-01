"""Database MySQL RIÊNG của từng app (AB0) + toàn bộ truy cập dữ liệu của app (CRUD/aggregate theo Spec).

- Mỗi app = 1 database `pf_<hex>` + 1 user MySQL cùng tên chỉ có quyền trên đúng database đó (không DROP/GRANT) -> bug hay lỗ hổng trong 1 app không
  chạm được dữ liệu app khác hay database chính. Việc cấp phát dùng `TENANT_DB_ADMIN_URL` (tài khoản có quyền CREATE DATABASE/USER), tách khỏi DATABASE_URL.
- Mật khẩu user app = HMAC(SECRET_KEY, tên db): không lưu ở đâu, tính lại khi cần (đổi SECRET_KEY = mất kết nối dữ liệu app đã tạo).
- Mọi câu lệnh dữ liệu dựng bằng SQLAlchemy Core từ Spec ĐÃ validate (app/builder/spec.py): tên bảng/cột không bao giờ nối chuỗi vào SQL; giá trị luôn là
  tham số ràng buộc. Ràng buộc (kiểu, bắt buộc, ref tồn tại) kiểm ở đây = ở SERVER, bất kể code giao diện LLM sinh ra làm gì.
- Hạn mức dung lượng (Config.TENANT_DB_QUOTA_MB, mặc định 500MB): đo data_length+index_length trong information_schema; vượt hạn mức thì chặn THÊM/SỬA
  (xoá vẫn được để người dùng tự giải phóng chỗ).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import threading
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError

from config import Config

logger = logging.getLogger("builder.tenant_db")

DB_NAME_RE = re.compile(r"^pf_[0-9a-f]{16}$")
_HOST_RE = re.compile(r"^[A-Za-z0-9_.%-]{1,60}$")
_TYPE_MAP = {
    "string": lambda: sa.String(255), "text": lambda: sa.Text(), "integer": lambda: sa.BigInteger(), "decimal": lambda: sa.Numeric(18, 4),
    "boolean": lambda: sa.Boolean(), "date": lambda: sa.Date(), "datetime": lambda: sa.DateTime(), "ref": lambda: sa.BigInteger(),
}
MAX_TEXT = 20000
SYSTEM_COLUMNS = ("id", "created_at", "updated_at")
_MYSQL_UNKNOWN_SYSTEM_VARIABLE = 1193


class TenantDbError(RuntimeError):
    """Không cấp phát/kết nối được database của app (cấu hình TENANT_DB_ADMIN_URL, quyền MySQL...)."""


class QuotaExceeded(RuntimeError):
    """Database của app đã đầy hạn mức dung lượng."""


class RecordError(ValueError):
    """Dữ liệu gửi lên không hợp lệ. `errors`: {field: thông báo} để giao diện hiện đúng ô; `status`: mã HTTP phù hợp (400 mặc định, 404 khi không thấy bản ghi)."""

    def __init__(self, message: str, errors: dict | None = None, status: int = 400):
        super().__init__(message)
        self.errors = errors or {}
        self.status = status


# --------------------------------------------------------------------------------------------------------------------- cấp phát
def new_db_name() -> str:
    return "pf_" + secrets.token_hex(8)  # 19 ký tự: vừa giới hạn tên user MySQL (32) và tên database (64)


def _password(db_name: str) -> str:
    return hmac.new(Config.SECRET_KEY.encode(), f"tenant-db:{db_name}".encode(), hashlib.sha256).hexdigest()[:32]


def _admin_url() -> URL:
    return make_url(Config.TENANT_DB_ADMIN_URL)


def provision(db_name: str) -> None:
    """Tạo database + user riêng (idempotent). Ném TenantDbError kèm lý do thật khi MySQL từ chối (thiếu quyền, sai URL...)."""
    host = Config.TENANT_DB_USER_HOST
    if not DB_NAME_RE.match(db_name) or not _HOST_RE.match(host):
        raise TenantDbError("Tên database hoặc TENANT_DB_USER_HOST không hợp lệ")  # chỉ xảy ra nếu code/cấu hình sai — không nối chuỗi lạ vào DDL
    engine = sa.create_engine(_admin_url(), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
            # DDL không nhận tham số ràng buộc cho tên/mật khẩu; cả 3 giá trị đã được kiểm bằng regex/hex ở trên nên nối chuỗi an toàn.
            conn.execute(sa.text(f"CREATE USER IF NOT EXISTS '{db_name}'@'{host}' IDENTIFIED BY '{_password(db_name)}'"))
            conn.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX ON `{db_name}`.* TO '{db_name}'@'{host}'"))
    except DBAPIError as exc:
        raise TenantDbError(f"MySQL từ chối cấp phát database cho app: {exc.orig}") from exc
    finally:
        engine.dispose()


_engines: dict[str, sa.Engine] = {}
_engines_lock = threading.Lock()


def engine_for(db_name: str) -> sa.Engine:
    if not DB_NAME_RE.match(db_name or ""):
        raise TenantDbError("Tên database của app không hợp lệ")
    with _engines_lock:
        engine = _engines.get(db_name)
        if engine is None:
            admin = _admin_url()
            url = URL.create(admin.drivername, username=db_name, password=_password(db_name), host=admin.host, port=admin.port, database=db_name,
                             query={"charset": "utf8mb4"})
            engine = _engines[db_name] = sa.create_engine(url, pool_size=2, max_overflow=3, pool_recycle=280, pool_pre_ping=True)
        return engine


# ----------------------------------------------------------------------------------------------------------------------- schema
def _table(spec: dict, collection: str, metadata: sa.MetaData | None = None) -> sa.Table:
    coll = next((c for c in spec["collections"] if c["name"] == collection), None)
    if coll is None:
        raise RecordError(f'Collection "{collection}" không tồn tại', status=404)
    columns = [sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True)]
    for field in coll["fields"]:
        columns.append(sa.Column(field["name"], _TYPE_MAP[field["type"]](), nullable=True, index=field["type"] == "ref"))
    columns.append(sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()))
    columns.append(sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()))
    return sa.Table(collection, metadata or sa.MetaData(), *columns, mysql_engine="InnoDB", mysql_charset="utf8mb4")


def apply_spec(db_name: str, spec: dict) -> list[str]:
    """Tạo bảng còn thiếu và THÊM cột còn thiếu (cột mới luôn nullable). KHÔNG BAO GIỜ xoá/đổi kiểu cột hay bảng (theo định hướng: không xoá cột có dữ
    liệu khi chưa xác nhận). Trả danh sách thay đổi đã làm (để hiện/ghi log)."""
    engine = engine_for(db_name)
    changes: list[str] = []
    try:
        inspector = sa.inspect(engine)
        existing = set(inspector.get_table_names())
        for coll in spec["collections"]:
            table = _table(spec, coll["name"])
            if table.name not in existing:
                table.create(engine)
                changes.append(f"tạo bảng {table.name}")
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                preparer = engine.dialect.identifier_preparer
                ddl = f"ALTER TABLE {preparer.quote(table.name)} ADD COLUMN {preparer.quote(column.name)} {column.type.compile(dialect=engine.dialect)} NULL"
                with engine.begin() as conn:
                    conn.execute(sa.text(ddl))
                changes.append(f"thêm cột {table.name}.{column.name}")
    except DBAPIError as exc:
        raise TenantDbError(f"Không dựng được schema trong database của app: {exc.orig}") from exc
    return changes


# ---------------------------------------------------------------------------------------------------------------------- dung lượng
_stats_expiry_supported: bool | None = None


def usage_bytes(db_name: str) -> int:
    """Dung lượng thực (dữ liệu + chỉ mục) của database app. MySQL 8 cache thống kê information_schema tới 24 giờ (information_schema_stats_expiry) nên
    đặt = 0 cho phiên đo để số liệu tức thời; MariaDB không có biến này (lỗi 1193) — nhận biết 1 lần rồi nhớ lại, không nuốt lỗi khác."""
    global _stats_expiry_supported
    query = sa.text("SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.TABLES WHERE table_schema = :schema")
    with engine_for(db_name).connect() as conn:
        if _stats_expiry_supported is not False:
            try:
                conn.execute(sa.text("SET SESSION information_schema_stats_expiry = 0"))
                _stats_expiry_supported = True
            except DBAPIError as exc:
                if getattr(exc.orig, "args", [None])[0] != _MYSQL_UNKNOWN_SYSTEM_VARIABLE:
                    raise
                _stats_expiry_supported = False
                conn.rollback()
        return int(conn.execute(query, {"schema": db_name}).scalar() or 0)


def check_quota(db_name: str, quota_mb: int) -> None:
    if usage_bytes(db_name) >= quota_mb * 1024 * 1024:
        raise QuotaExceeded(f"Ứng dụng đã dùng hết {quota_mb}MB dung lượng. Hãy xoá bớt dữ liệu hoặc nâng gói để tiếp tục thêm/sửa.")


# ------------------------------------------------------------------------------------------------------------------------ giá trị
def _coerce(field: dict, value, conn, spec: dict):
    """Giá trị JSON -> giá trị cột; ném ValueError(thông báo) nếu sai kiểu. None/"" -> None (kiểm bắt buộc ở caller)."""
    if value is None or (isinstance(value, str) and not value.strip() and field["type"] != "text"):
        return None
    kind = field["type"]
    if kind == "string":
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError("phải là chuỗi")
        text = str(value).strip()
        if len(text) > 255:
            raise ValueError("tối đa 255 ký tự")
        return text
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError("phải là chuỗi")
        if len(value) > MAX_TEXT:
            raise ValueError(f"tối đa {MAX_TEXT} ký tự")
        return value or None
    if kind in ("integer", "ref"):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError("phải là số nguyên")
        try:
            number = int(str(value).strip())
        except ValueError as exc:
            raise ValueError("phải là số nguyên") from exc
        if kind == "ref":
            target = sa.Table(field["ref"], sa.MetaData(), sa.Column("id", sa.BigInteger, primary_key=True))
            if conn.execute(sa.select(target.c.id).where(target.c.id == number)).first() is None:
                raise ValueError(f'không tìm thấy bản ghi #{number} trong "{field["ref"]}"')
        return number
    if kind == "decimal":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("phải là số")
        try:
            number = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValueError("phải là số") from exc
        if not number.is_finite() or abs(number) >= Decimal(10) ** 14:
            raise ValueError("số ngoài phạm vi cho phép")
        return number.quantize(Decimal("0.0001"))
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).strip().lower() in ("true", "1"):
            return True
        if str(value).strip().lower() in ("false", "0"):
            return False
        raise ValueError("phải là true/false")
    if kind == "date":
        try:
            return date.fromisoformat(str(value).strip()[:10])
        except ValueError as exc:
            raise ValueError("phải là ngày dạng YYYY-MM-DD") from exc
    if kind == "datetime":
        try:
            return datetime.fromisoformat(str(value).strip().replace("Z", ""))
        except ValueError as exc:
            raise ValueError("phải là ngày giờ dạng YYYY-MM-DDTHH:MM") from exc
    raise ValueError("kiểu không được hỗ trợ")


def _fields(spec: dict, collection: str) -> dict[str, dict]:
    coll = next((c for c in spec["collections"] if c["name"] == collection), None)
    if coll is None:
        raise RecordError(f'Collection "{collection}" không tồn tại', status=404)
    return {f["name"]: f for f in coll["fields"]}


def _validated_values(spec: dict, collection: str, payload, conn, *, partial: bool) -> dict:
    if not isinstance(payload, dict):
        raise RecordError("Dữ liệu gửi lên phải là đối tượng JSON")
    fields = _fields(spec, collection)
    errors: dict[str, str] = {}
    for key in payload:
        if key not in fields and key not in SYSTEM_COLUMNS:
            errors[key] = "field không tồn tại"
    values: dict = {}
    for name, field in fields.items():
        if name not in payload:
            if not partial and field["required"]:
                errors[name] = "bắt buộc nhập"
            continue
        try:
            value = _coerce(field, payload[name], conn, spec)
        except ValueError as exc:
            errors[name] = str(exc)
            continue
        if value is None and field["required"]:
            errors[name] = "bắt buộc nhập"
            continue
        values[name] = value
    if errors:
        raise RecordError("Dữ liệu không hợp lệ", errors)
    return values


def serialize(row, table: sa.Table) -> dict:
    out = {}
    for column in table.columns:
        value = row._mapping[column.name]
        if isinstance(value, Decimal):
            value = format(value.normalize(), "f")  # chuỗi: JSON number làm mất độ chính xác tiền tệ
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        out[column.name] = value
    return out


# ------------------------------------------------------------------------------------------------------------------------- CRUD
def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def list_records(db_name: str, spec: dict, collection: str, *, page: int, per_page: int, q: str = "", sort: str = "id", order: str = "desc",
                 filters: dict | None = None) -> dict:
    table = _table(spec, collection)
    fields = _fields(spec, collection)
    per_page = min(max(per_page, 1), Config.BUILDER_API_MAX_PER_PAGE)
    page = max(page, 1)
    if sort not in fields and sort not in SYSTEM_COLUMNS:
        raise RecordError(f'Không sắp xếp được theo "{sort}"')
    where = []
    for key, raw in (filters or {}).items():
        if key not in fields and key != "id":
            raise RecordError(f'Không lọc được theo "{key}"')
        if key == "id":
            where.append(table.c.id == int(raw) if str(raw).isdigit() else sa.false())
            continue
        with engine_for(db_name).connect() as conn:
            try:
                where.append(table.c[key] == _coerce(fields[key], raw, conn, spec))
            except ValueError as exc:
                raise RecordError(f'Bộ lọc "{key}": {exc}') from exc
    if q.strip():
        searchable = [table.c[n] for n, f in fields.items() if f["type"] in ("string", "text")]
        if searchable:
            where.append(sa.or_(*[c.like(_like(q.strip()[:100]), escape="\\") for c in searchable]))
    sort_col = table.c[sort]
    ordering = [sort_col.asc() if order == "asc" else sort_col.desc(), table.c.id.desc()]
    with engine_for(db_name).connect() as conn:
        total = conn.execute(sa.select(sa.func.count()).select_from(table).where(*where)).scalar() or 0
        rows = conn.execute(sa.select(table).where(*where).order_by(*ordering).limit(per_page).offset((page - 1) * per_page)).all()
    return {"items": [serialize(r, table) for r in rows], "total": int(total), "page": page, "per_page": per_page}


def get_record(db_name: str, spec: dict, collection: str, record_id: int) -> dict:
    table = _table(spec, collection)
    with engine_for(db_name).connect() as conn:
        row = conn.execute(sa.select(table).where(table.c.id == record_id)).first()
    if row is None:
        raise RecordError("Không tìm thấy bản ghi", status=404)
    return serialize(row, table)


def create_record(db_name: str, spec: dict, collection: str, payload, quota_mb: int) -> dict:
    table = _table(spec, collection)
    check_quota(db_name, quota_mb)
    try:
        with engine_for(db_name).begin() as conn:
            values = _validated_values(spec, collection, payload, conn, partial=False)
            new_id = conn.execute(sa.insert(table).values(**values)).inserted_primary_key[0]
            row = conn.execute(sa.select(table).where(table.c.id == new_id)).one()
    except IntegrityError as exc:
        raise RecordError(f"Vi phạm ràng buộc dữ liệu: {exc.orig}") from exc
    return serialize(row, table)


def update_record(db_name: str, spec: dict, collection: str, record_id: int, payload, quota_mb: int) -> dict:
    table = _table(spec, collection)
    check_quota(db_name, quota_mb)
    try:
        with engine_for(db_name).begin() as conn:
            values = _validated_values(spec, collection, payload, conn, partial=True)
            if values and conn.execute(sa.update(table).where(table.c.id == record_id).values(**values)).rowcount == 0:
                if conn.execute(sa.select(table.c.id).where(table.c.id == record_id)).first() is None:  # rowcount 0 cũng xảy ra khi giá trị không đổi
                    raise RecordError("Không tìm thấy bản ghi", status=404)
            row = conn.execute(sa.select(table).where(table.c.id == record_id)).first()
            if row is None:
                raise RecordError("Không tìm thấy bản ghi", status=404)
    except IntegrityError as exc:
        raise RecordError(f"Vi phạm ràng buộc dữ liệu: {exc.orig}") from exc
    return serialize(row, table)


def delete_record(db_name: str, spec: dict, collection: str, record_id: int) -> None:
    table = _table(spec, collection)
    try:
        with engine_for(db_name).begin() as conn:
            if conn.execute(sa.delete(table).where(table.c.id == record_id)).rowcount == 0:
                raise RecordError("Không tìm thấy bản ghi", status=404)
    except IntegrityError as exc:  # khoá ngoại: không có FK thật (ref chỉ là cột chỉ mục) nên hiếm; giữ để báo lỗi rõ nếu sau này thêm FK
        raise RecordError(f"Không xoá được vì còn dữ liệu liên quan: {exc.orig}") from exc


def aggregate(db_name: str, spec: dict, collection: str, agg: str, field: str | None, filters: dict | None = None) -> dict:
    from app.builder.spec import AGGREGATES, NUMERIC_TYPES

    table = _table(spec, collection)
    fields = _fields(spec, collection)
    if agg not in AGGREGATES:
        raise RecordError(f"agg phải thuộc {', '.join(AGGREGATES)}")
    if agg == "count":
        expr = sa.func.count()
    else:
        if field not in fields or fields[field]["type"] not in NUMERIC_TYPES:
            raise RecordError(f'"{agg}" cần field số có thật trong "{collection}"')
        expr = {"sum": sa.func.sum, "avg": sa.func.avg, "min": sa.func.min, "max": sa.func.max}[agg](table.c[field])
    where = []
    with engine_for(db_name).connect() as conn:
        for key, raw in (filters or {}).items():
            if key not in fields:
                raise RecordError(f'Không lọc được theo "{key}"')
            try:
                where.append(table.c[key] == _coerce(fields[key], raw, conn, spec))
            except ValueError as exc:
                raise RecordError(f'Bộ lọc "{key}": {exc}') from exc
        value = conn.execute(sa.select(expr).select_from(table).where(*where)).scalar()
    if isinstance(value, Decimal):
        value = format(value.normalize(), "f")
    return {"value": 0 if value is None else value}


def record_counts(db_name: str, spec: dict) -> dict[str, int]:
    """Số bản ghi mỗi collection (cho trang quản trị app)."""
    counts = {}
    with engine_for(db_name).connect() as conn:
        for coll in spec["collections"]:
            counts[coll["name"]] = int(conn.execute(sa.select(sa.func.count()).select_from(_table(spec, coll["name"]))).scalar() or 0)
    return counts
