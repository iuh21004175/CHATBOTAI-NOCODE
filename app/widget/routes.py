"""Route layer (API) cho blueprint widget: nhận request, validate, gọi service, trả response.
Không chứa logic nghiệp vụ — logic đặt ở service.py.

API công khai (không đăng nhập) nên bảo vệ bằng: domain được phép nhúng (kiểm tra header Origin),
CORS chỉ mở cho đúng origin đó, bot chỉ định danh bằng public_id ngẫu nhiên (không dùng id tuần tự), giới hạn tần suất theo IP, giới hạn độ dài tin nhắn.
"""
from flask import Blueprint, Response, abort, current_app, jsonify, request

from app.attachments import service as attachments_service
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


def _authorize(public_id: str):
    """Trả về (bot, origin, None) nếu request hợp lệ, ngược lại (None, None, response lỗi)."""
    bot = service.get_bot(public_id)
    if bot is None:
        return None, None, (jsonify(error="Không tìm thấy trợ lý."), 404)
    # Request cross-origin luôn có Origin; GET cùng origin thì trình duyệt bỏ Origin nên dùng Referer thay thế
    origin = request.headers.get("Origin") or service.origin_of(request.headers.get("Referer", ""))
    if not service.origin_allowed(origin, service.allowed_domains(bot)):
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


@bp.route("/api/<string:public_id>/icon", methods=["GET"])
def widget_icon(public_id):
    """Ảnh icon tự tải lên (Bước 3), public như embed.js — không gác bằng domain: <img> tải cross-site
    không gửi Origin đáng tin cậy (Referrer-Policy nhiều trình duyệt/site đã tắt Referer), gác nhầm sẽ
    làm icon không hiển thị ngay trên chính domain đã cấu hình. Ảnh không nhạy cảm nên không cần gác."""
    bot = service.get_bot(public_id)
    if bot is None:
        abort(404)
    result = service.icon_bytes(bot)
    if result is None:
        abort(404)
    raw, content_type = result
    return Response(raw, mimetype=content_type, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@bp.route("/api/<string:public_id>/config", methods=["GET", "OPTIONS"])
def widget_config(public_id):
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    return _with_cors(jsonify(service.get_config(bot)), origin)


@bp.route("/api/<string:public_id>/messages", methods=["POST", "OPTIONS"])
@limiter.limit("20 per minute", methods=["POST"])
def receive_message(public_id):
    """Endpoint public nhận tin nhắn khách, gọi rag_engine trả lời (đồng bộ trong request)."""
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)

    payload = request.get_json(silent=True) or {}
    message = payload.get("message")
    attachment_ids = payload.get("attachment_ids")  # id các tệp đã tải lên (module "Đọc tài liệu"); tin chỉ có tệp thì message có thể rỗng
    if message is None and attachment_ids:
        message = ""
    if not isinstance(message, str):
        return _with_cors(jsonify(error="Thiếu nội dung tin nhắn."), origin), 400

    # async: true = widget mới (embed.js): lưu tin rồi trả NGAY mã lượt, tiến trình + câu trả lời lấy qua /turns/<turn_id>. Không có cờ = đường đồng bộ cũ
    # (chờ tới khi có câu trả lời) cho mã nhúng/khách API cũ.
    accept = service.start_message if payload.get("async") is True else service.receive_message
    try:
        result = accept(bot, message, str(payload.get("visitor_id") or ""), payload.get("conversation_id"), attachment_ids)
    except ValueError:
        return _with_cors(jsonify(error="Tin nhắn trống hoặc quá dài."), origin), 400
    except attachments_service.AttachmentError as exc:
        return _with_cors(jsonify(error=str(exc)), origin), exc.status
    except Exception:
        current_app.logger.exception("widget: không trả lời được (bot_id=%s)", bot.id)
        return _with_cors(jsonify(error=service.get_config(bot)["error"]), origin), 502
    return _with_cors(jsonify(result), origin), 202 if result.get("status") == "processing" else 200


@bp.route("/api/<string:public_id>/attachments", methods=["POST", "OPTIONS"])
@limiter.limit("10 per minute", methods=["POST"])
def upload_attachment(public_id):
    """Khách tải 1 tệp lên (module "Đọc tài liệu"), multipart: file, visitor_id, conversation_id (tuỳ chọn). Trả ngay 202 {id, status: processing}; việc đọc chạy nền,
    widget hỏi tiếp qua GET .../attachments/<id> tới khi xong rồi mới gửi tin kèm attachment_ids."""
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    try:
        body = attachments_service.upload(
            current_app._get_current_object(), bot, request.form.get("visitor_id"), request.form.get("conversation_id", type=int), request.files.get("file"),
        )
    except attachments_service.AttachmentError as exc:
        return _with_cors(jsonify(error=str(exc)), origin), exc.status
    return _with_cors(jsonify(body), origin), 202


@bp.route("/api/<string:public_id>/attachments/<int:attachment_id>", methods=["GET", "OPTIONS"])
@limiter.limit("300 per minute", methods=["GET"])
def attachment_status(public_id, attachment_id):
    """Trạng thái đọc 1 tệp (đúng chủ = bot + visitor_id mới xem được; sai thì 404 như không tồn tại)."""
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    body = attachments_service.status_of(bot, attachment_id, request.args.get("visitor_id", ""))
    if body is None:
        return _with_cors(jsonify(error="Không tìm thấy tệp."), origin), 404
    return _with_cors(jsonify(body), origin)


@bp.route("/api/<string:public_id>/staff-messages", methods=["GET", "OPTIONS"])
@limiter.limit("60 per minute", methods=["GET"])
def staff_messages(public_id):
    """Widget hỏi định kỳ: tin nhân viên trả lời từ Inbox (đúng bot + đúng visitor mới đọc được)."""
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    result = service.staff_messages_since(
        bot,
        request.args.get("conversation_id", type=int),
        request.args.get("visitor_id", ""),
        request.args.get("after_id", 0, type=int),
        include_bot=request.args.get("include_bot") == "1",
    )
    return _with_cors(jsonify(result), origin)


@bp.route("/api/<string:public_id>/turns/<string:turn_id>", methods=["GET", "OPTIONS"])
@limiter.limit("300 per minute", methods=["GET"])
def turn_status(public_id, turn_id):
    """Widget hỏi tiến trình + câu trả lời của lượt bất đồng bộ (sự kiện từ vị trí `after`). Lượt không tồn tại/hết hạn/không thuộc visitor này -> 404."""
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    result = service.turn_status(bot, turn_id, request.args.get("visitor_id", ""), request.args.get("after", 0, type=int))
    if result is None:
        return _with_cors(jsonify(error="Không tìm thấy lượt trả lời."), origin), 404
    return _with_cors(jsonify(result), origin)


@bp.route("/api/<string:public_id>/actions/<int:pending_action_id>/result", methods=["POST", "OPTIONS"])
@limiter.limit("60 per minute", methods=["POST"])
def action_result(public_id, pending_action_id):
    """Widget báo kết quả thực thi 1 hành động (Phase M3). Cùng lớp bảo vệ như các route widget khác: public_id + Origin thuộc domain đã khai báo của bot;
    ngoài ra phải đúng visitor_id của hội thoại và token riêng của lệnh (chỉ gửi qua Socket.IO tới đúng khách). HTTP POST thường để không phụ thuộc
    socket còn sống lúc gửi kết quả."""
    bot, origin, error = _authorize(public_id)
    if error:
        return error
    if request.method == "OPTIONS":
        return _with_cors(Response(status=204), origin)
    payload = request.get_json(silent=True) or {}
    body, status = service.submit_action_result(
        bot, pending_action_id, payload.get("visitor_id"), payload.get("token"), payload.get("status"), payload.get("reason"), payload.get("data"),
    )
    return _with_cors(jsonify(body), origin), status
