"""Cấu hình giao diện chatbox của Web Widget — nguồn DUY NHẤT cho: các lựa chọn hợp lệ, giá trị mặc định,
validate form, và payload gửi cho widget. Trang Xuất bản (form + khung xem trước), API /config và
embed.js đều đi qua đây, nên thêm 1 tuỳ chọn mới chỉ cần: thêm vào bảng dưới, thêm cột trong
BotSettings, và xử lý ở embed.js (apply) — khung xem trước tự đổi theo vì chạy đúng embed.js thật.
"""
from app.widget.icons import CUSTOM_ICON_KEY, DEFAULT_ICON, WIDGET_ICONS

DEFAULT_COLOR = "#1D4ED8"
COLOR_PRESETS = ["#1D4ED8", "#0EA5E9", "#16A34A", "#F59E0B", "#EC4899", "#7C3AED", "#DC2626", "#0F172A"]

SIZE_MIN = 40
SIZE_MAX = 96
DEFAULT_SIZE = 56

SHAPES = {"round": "Tròn", "rounded": "Bo góc"}
DEFAULT_SHAPE = "round"

POSITIONS = {"left": "Bên trái", "right": "Bên phải"}
DEFAULT_POSITION = "right"

# key -> (nhãn, rộng px, cao px) của khung chat khi mở
WINDOWS = {"sm": ("Nhỏ", 320, 460), "md": ("Vừa", 360, 540), "lg": ("Lớn", 420, 640)}
DEFAULT_WINDOW = "md"


def is_valid_color(value: str) -> bool:
    """Chỉ nhận #RRGGBB — giá trị này đi vào CSS nên không cho chuỗi tự do."""
    return len(value) == 7 and value[0] == "#" and all(c in "0123456789abcdefABCDEF" for c in value[1:])


def _choice(value, allowed, default):
    return value if value in allowed else default


def _current_icon(settings) -> str:
    """"custom" chỉ hợp lệ khi thực sự có ảnh đã tải lên — icon bị xoá/orphan (vd. tải icon rồi sau đó
    ảnh bị dọn ở tầng lưu trữ ngoài ý muốn) thì tự về mặc định thay vì trỏ tới ảnh không còn tồn tại."""
    icon = settings.widget_icon
    if icon == CUSTOM_ICON_KEY:
        return icon if settings.widget_icon_path else DEFAULT_ICON
    return _choice(icon, WIDGET_ICONS, DEFAULT_ICON)


def current(settings) -> dict:
    """Cấu hình đã lưu, đã chuẩn hoá (dữ liệu cũ/sai tự về mặc định)."""
    color = settings.widget_color or ""
    return {
        "icon": _current_icon(settings),
        "color": color.upper() if is_valid_color(color) else DEFAULT_COLOR,
        "size": min(max(settings.widget_size or DEFAULT_SIZE, SIZE_MIN), SIZE_MAX),
        "shape": _choice(settings.widget_shape, SHAPES, DEFAULT_SHAPE),
        "position": _choice(settings.widget_position, POSITIONS, DEFAULT_POSITION),
        "window": _choice(settings.widget_window, WINDOWS, DEFAULT_WINDOW),
    }


def to_widget(values: dict, icon_url: str | None = None) -> dict:
    """Từ cấu hình (key) sang payload widget dùng trực tiếp: icon (SVG cố định hoặc ảnh tự tải lên),
    kích thước px của khung chat. icon_url: URL công khai của ảnh, chỉ cần khi icon="custom" — do
    caller tự dựng (cần bot_id để tạo URL, appearance.py không biết về routing)."""
    _, width, height = WINDOWS[values["window"]]
    if values["icon"] == CUSTOM_ICON_KEY and icon_url:
        icon = {"kind": "image", "value": icon_url}
    else:
        icon = {"kind": "svg", "value": WIDGET_ICONS.get(values["icon"], WIDGET_ICONS[DEFAULT_ICON])}
    return {
        "icon": icon,
        "color": values["color"],
        "size": values["size"],
        "shape": values["shape"],
        "position": values["position"],
        "window": {"w": width, "h": height},
    }


def parse_form(form) -> tuple[dict | None, str | None]:
    """Validate form Xuất bản -> (giá trị hợp lệ, None) hoặc (None, thông báo lỗi)."""
    icon = form.get("widget_icon", "")
    if icon not in WIDGET_ICONS and icon != CUSTOM_ICON_KEY:
        return None, "Icon không hợp lệ."
    color = form.get("widget_color", "")
    if not is_valid_color(color):
        return None, "Màu không hợp lệ (định dạng #RRGGBB)."
    try:
        size = int(form.get("widget_size", ""))
    except (TypeError, ValueError):
        return None, "Kích thước phải là số nguyên."
    if not SIZE_MIN <= size <= SIZE_MAX:
        return None, f"Kích thước nút chat phải từ {SIZE_MIN} đến {SIZE_MAX} px."
    shape = form.get("widget_shape", "")
    if shape not in SHAPES:
        return None, "Kiểu nút không hợp lệ."
    position = form.get("widget_position", "")
    if position not in POSITIONS:
        return None, "Vị trí không hợp lệ."
    window = form.get("widget_window", "")
    if window not in WINDOWS:
        return None, "Kích thước khung chat không hợp lệ."
    return {"icon": icon, "color": color.upper(), "size": size, "shape": shape, "position": position, "window": window}, None


def apply(settings, values: dict) -> None:
    settings.widget_icon = values["icon"]
    settings.widget_color = values["color"]
    settings.widget_size = values["size"]
    settings.widget_shape = values["shape"]
    settings.widget_position = values["position"]
    settings.widget_window = values["window"]
