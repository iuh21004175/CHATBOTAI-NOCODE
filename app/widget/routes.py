"""Route layer (API) cho blueprint widget: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.

API công khai (không đăng nhập) nên bảo vệ bằng: domain được phép nhúng (kiểm tra header Origin),
CORS chỉ mở cho đúng origin đó, giới hạn tần suất theo IP, giới hạn độ dài tin nhắn.
"""
from flask import Blueprint, Response, current_app, jsonify, request

from extensions import limiter

from . import service

bp = Blueprint("widget", __name__, url_prefix="/widget")


def _with_cors(response: Response, origin: str) -> Response:
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Max-Age"] = "600"
    response.headers["Vary"] = "Origin"
    return response


def _authorize(bot_id: int):
    """Trả về (bot, origin, None) nếu request hợp lệ, ngược lại (None, None, response lỗi)."""
    bot = service.get_bot(bot_id)
    if bot is None:
        return None, None, (jsonify(error="Không tìm thấy trợ lý."), 404)
    # Request cross-origin luôn có Origin; GET cùng origin thì trình duyệt bỏ Origin nên dùng Referer thay thế
    origin = request.headers.get("Origin") or service.origin_of(request.headers.get("Referer", ""))
    if not service.origin_allowed(origin, service.widget_domain(bot)):
        return None, None, (jsonify(error="Domain này chưa được phép nhúng widget."), 403)
    return bot, origin, None


@bp.route("/embed.js", methods=["GET"])
def embed_script():
    """Script nhúng tĩnh (embed.js), không cần auth. Tự dựng khung chat trong Shadow DOM."""
    return Response(
        service.embed_script_source(),
        mimetype="application/javascript",
        headers={"Cache-Control": "public, max-age=300"},
    )


@bp.route("/api/<int:bot_id>/config", methods=["GET", "OPTIONS"])
def widget_config(bot_id):
    bot, origin, error = _authorize(bot_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    return _with_cors(jsonify(service.get_config(bot)), origin)


@bp.route("/api/<int:bot_id>/messages", methods=["POST", "OPTIONS"])
@limiter.limit("20 per minute", methods=["POST"])
def receive_message(bot_id):
    """Endpoint public nhận tin nhắn khách, gọi rag_engine trả lời (đồng bộ trong request)."""
    bot, origin, error = _authorize(bot_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)

    payload = request.get_json(silent=True) or {}
    message = payload.get("message")
    if not isinstance(message, str):
        return _with_cors(jsonify(error="Thiếu nội dung tin nhắn."), origin), 400

    try:
        result = service.receive_message(bot, message, str(payload.get("visitor_id") or ""), payload.get("conversation_id"))
    except ValueError:
        return _with_cors(jsonify(error="Tin nhắn trống hoặc quá dài."), origin), 400
    except Exception:
        current_app.logger.exception("widget: không trả lời được (bot_id=%s)", bot.id)
        return _with_cors(jsonify(error=service.get_config(bot)["error"]), origin), 502
    return _with_cors(jsonify(result), origin)
