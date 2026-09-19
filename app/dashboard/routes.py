"""Route layer cho các trang sau đăng nhập: Bảng điều khiển, tạo trợ lý mới, và trang tạm
"đang xây dựng" cho các mục nav/thao tác chưa có màn hình thật.
"""
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import login_required
from werkzeug.exceptions import RequestEntityTooLarge

from app.csrf import ensure_csrf_token, verify_csrf_token
from app.widget import appearance, icons
from app.widget import service as widget_service
from extensions import limiter

from . import service

bp = Blueprint("dashboard", __name__)
bp.add_app_template_filter(service.filesize, "filesize")  # {{ 1536|filesize }} -> "1.5 KB"


@bp.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    """Request vượt MAX_CONTENT_LENGTH (tổng dung lượng 1 lần tải lên quá lớn) -> báo lỗi thân thiện."""
    flash(f"Tổng dung lượng tải lên quá lớn (tối đa {service.filesize(current_app.config['MAX_CONTENT_LENGTH'])} mỗi lần).", "error")
    bot_id = (request.view_args or {}).get("bot_id")
    return redirect(url_for("dashboard.bot_knowledge", bot_id=bot_id)) if bot_id else ("Payload Too Large", 413)


def _require_bot(bot_id: int):
    """Lấy bot theo id, chặn 404 nếu không thuộc team đang đăng nhập (multi-tenant)."""
    team_id = session.get("team_id")
    bot = service.get_bot_for_team(bot_id, team_id) if team_id else None
    if bot is None:
        abort(404)
    return bot


@bp.route("/dashboard")
@login_required
def index():
    team_id = session.get("team_id")
    team = service.get_team(team_id) if team_id else None
    bots = service.get_team_bots(team_id) if team_id else []

    if not bots:
        return render_template("dashboard/empty.html", team=team)

    bot_id = request.args.get("bot_id", type=int)
    selected_bot = next((b for b in bots if b.id == bot_id), None) or bots[0]
    detail = service.get_bot_detail(selected_bot)
    owner_name = service.get_team_owner_name(team_id)

    return render_template(
        "dashboard/detail.html",
        team=team,
        bots=bots,
        selected_bot=selected_bot,
        owner_name=owner_name,
        **detail,
    )


@bp.route("/bots/new", methods=["GET", "POST"])
@login_required
def new_bot():
    team_id = session.get("team_id")

    if request.method == "GET":
        return render_template("dashboard/new_bot.html", csrf_token=ensure_csrf_token(), error=None, name="")

    name = request.form.get("name", "").strip()

    if not verify_csrf_token(request.form.get("csrf_token", "")):
        return render_template(
            "dashboard/new_bot.html",
            csrf_token=ensure_csrf_token(),
            error="Phiên làm việc đã hết hạn, vui lòng thử lại.",
            name=name,
        ), 400

    if not name:
        return render_template(
            "dashboard/new_bot.html", csrf_token=ensure_csrf_token(), error="Vui lòng nhập tên trợ lý.", name=name
        ), 400

    bot = service.create_bot(team_id, name)
    flash(f'Đã tạo trợ lý "{bot.name}".', "success")
    return redirect(url_for("dashboard.index", bot_id=bot.id))


@bp.route("/placeholder")
@login_required
def placeholder():
    title = request.args.get("title", "Tính năng này")
    return render_template("placeholder.html", title=title)


# ---- Bước 1: Thiết lập ----

@bp.route("/bots/<int:bot_id>/setup", methods=["GET", "POST"])
@login_required
def bot_setup(bot_id):
    bot = _require_bot(bot_id)
    settings = service.get_or_create_settings(bot)

    if request.method == "POST":
        if not verify_csrf_token(request.form.get("csrf_token", "")):
            flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        elif not request.form.get("name", "").strip():
            flash("Vui lòng nhập tên trợ lý.", "error")
        else:
            service.update_bot_setup(bot, settings, request.form)
            flash("Đã lưu thay đổi.", "success")
            return redirect(url_for("dashboard.bot_setup", bot_id=bot.id))

    return render_template(
        "bots/setup.html",
        bot=bot,
        settings=settings,
        step=1,
        step_name="Thiết lập",
        csrf_token=ensure_csrf_token(),
    )


@bp.route("/bots/<int:bot_id>/preview-chat", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def bot_preview_chat(bot_id):
    """Chat thử trong khung xem trước ở Bước 1: gọi đúng luồng RAG + cấu hình đã lưu như widget thật,
    nhưng không lưu hội thoại (không lẫn vào Lịch sử chat)."""
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error="Phiên làm việc đã hết hạn, vui lòng tải lại trang."), 400

    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    if not message:
        return jsonify(error="Vui lòng nhập câu hỏi."), 400
    if len(message) > service.MAX_MESSAGE_CHARS:
        return jsonify(error=f"Câu hỏi tối đa {service.MAX_MESSAGE_CHARS} ký tự."), 400

    try:
        reply = service.generate_reply(bot, message, service.clean_history(payload.get("history")))
    except Exception:
        current_app.logger.exception("preview-chat lỗi (bot_id=%s)", bot.id)
        return jsonify(error="Không lấy được câu trả lời. Kiểm tra DEEPSEEK_API_KEY và các dịch vụ ChromaDB."), 502
    return jsonify(reply=reply)


# ---- Bước 3: Xuất bản ----

@bp.route("/bots/<int:bot_id>/publish", methods=["GET", "POST"])
@login_required
def bot_publish(bot_id):
    bot = _require_bot(bot_id)
    settings = service.get_or_create_settings(bot)

    if request.method == "POST":
        if not verify_csrf_token(request.form.get("csrf_token", "")):
            flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        else:
            service.update_widget_domain(settings, request.form.get("widget_domain", ""))
            flash("Đã lưu cấu hình Web Widget.", "success")
        return redirect(url_for("dashboard.bot_publish", bot_id=bot.id))

    embed_src = url_for("widget.embed_script", _external=True)
    return render_template(
        "bots/publish.html",
        bot=bot,
        settings=settings,
        appearance=appearance.current(settings),
        widget_config=widget_service.get_config(bot),  # đúng payload widget thật nhận từ /config
        embed_version=widget_service.embed_version(),
        widget_icons=icons.WIDGET_ICONS,
        widget_icon_labels=icons.WIDGET_ICON_LABELS,
        color_presets=appearance.COLOR_PRESETS,
        size_min=appearance.SIZE_MIN,
        size_max=appearance.SIZE_MAX,
        shapes=appearance.SHAPES,
        positions=appearance.POSITIONS,
        windows=appearance.WINDOWS,
        step=3,
        step_name="Xuất bản",
        csrf_token=ensure_csrf_token(),
        embed_src=embed_src,
    )


@bp.route("/bots/<int:bot_id>/publish/appearance", methods=["POST"])
@login_required
def bot_publish_appearance(bot_id):
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.update_widget_appearance(service.get_or_create_settings(bot), request.form)
        flash(error or "Đã lưu giao diện widget. Website đã nhúng sẽ cập nhật sau tối đa vài phút.", "error" if error else "success")
    return redirect(url_for("dashboard.bot_publish", bot_id=bot.id))


# ---- Bước 4: Lịch sử chat ----

@bp.route("/bots/<int:bot_id>/history")
@login_required
def bot_history(bot_id):
    bot = _require_bot(bot_id)
    search = request.args.get("q", "")
    channel = request.args.get("channel", "")
    conversations = service.list_conversations(bot_id, search=search, channel=channel)

    conv_id = request.args.get("conversation_id", type=int)
    selected = None
    messages = []
    if conversations:
        selected = next((c for c in conversations if c.id == conv_id), None) or conversations[0]
        messages = service.get_conversation_messages(selected.id)

    return render_template(
        "bots/history.html",
        bot=bot,
        step=4,
        step_name="Lịch sử chat",
        conversations=conversations,
        selected=selected,
        messages=messages,
        search=search,
        channel=channel,
        display_name=service.conversation_display_name,
    )


# ---- Bước 2: Cơ sở tri thức ----

@bp.route("/bots/<int:bot_id>/knowledge")
@login_required
def bot_knowledge(bot_id):
    bot = _require_bot(bot_id)
    search = request.args.get("q", "")
    ext = request.args.get("ext", "")
    documents = service.list_documents(bot.id, search=search, ext=ext)
    storage = service.storage_summary(bot.id)

    return render_template(
        "bots/knowledge.html",
        bot=bot,
        step=2,
        step_name="Cơ sở tri thức",
        documents=documents,
        search=search,
        ext=ext,
        storage=storage,
        max_file_mb=service.Config.KNOWLEDGE_MAX_FILE_MB,
        csrf_token=ensure_csrf_token(),
    )


@bp.route("/bots/<int:bot_id>/knowledge/upload", methods=["POST"])
@login_required
def bot_knowledge_upload(bot_id):
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("dashboard.bot_knowledge", bot_id=bot.id))

    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Chưa chọn tệp nào.", "error")
    uploaded = []
    for f in files:
        document, error = service.upload_document(bot, f)
        if error:
            flash(f"{f.filename}: {error}", "error")
        else:
            uploaded.append(document)
    if len(uploaded) == 1:  # 1 tệp: đi thẳng tới bước cấu hình chunk; nhiều tệp: cấu hình lần lượt từ danh sách
        return redirect(url_for("dashboard.bot_document_chunks", bot_id=bot.id, document_id=uploaded[0].id))
    if uploaded:
        flash(f"Đã tải lên {len(uploaded)} tệp — hãy cấu hình chunk cho từng tệp rồi bấm huấn luyện.", "success")
    return redirect(url_for("dashboard.bot_knowledge", bot_id=bot.id))


@bp.route("/bots/<int:bot_id>/knowledge/<int:document_id>/delete", methods=["POST"])
@login_required
def bot_knowledge_delete(bot_id, document_id):
    bot = _require_bot(bot_id)
    document = service.get_document_for_bot(document_id, bot.id) or abort(404)
    if verify_csrf_token(request.form.get("csrf_token", "")):
        service.delete_document(bot, document)
        flash("Đã xóa tài liệu.", "success")
    return redirect(url_for("dashboard.bot_knowledge", bot_id=bot.id))


def _configurable_document(bot, document_id):
    document = service.get_document_for_bot(document_id, bot.id) or abort(404)
    if document.status not in service.CONFIGURABLE_STATUSES:
        abort(409)  # đang chờ/đang xử lý: worker đang giữ tài liệu này
    return document


@bp.route("/bots/<int:bot_id>/knowledge/<int:document_id>/chunks")
@login_required
def bot_document_chunks(bot_id, document_id):
    from core import rag_engine

    bot = _require_bot(bot_id)
    document = _configurable_document(bot, document_id)
    chunk_size, chunk_overlap = service.chunk_params_for(bot, document)
    return render_template(
        "bots/chunk_config.html",
        bot=bot,
        step=2,
        step_name="Cơ sở tri thức",
        document=document,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_size_min=rag_engine.CHUNK_SIZE_MIN,
        chunk_size_max=rag_engine.CHUNK_SIZE_MAX,
        overlap_max_percent=rag_engine.OVERLAP_MAX_PERCENT,
        csrf_token=ensure_csrf_token(),
    )


@bp.route("/bots/<int:bot_id>/knowledge/<int:document_id>/chunks/preview", methods=["POST"])
@login_required
def bot_document_chunks_preview(bot_id, document_id):
    bot = _require_bot(bot_id)
    document = _configurable_document(bot, document_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify({"error": "Phiên làm việc đã hết hạn, hãy tải lại trang."}), 400
    params, error = service.parse_chunk_params(request.get_json(silent=True) or {})
    if error:
        return jsonify({"error": error}), 400
    try:
        return jsonify(service.preview_chunks(document, *params))
    except Exception as e:
        current_app.logger.exception("Không xem trước được chunk của tài liệu %s", document.id)
        return jsonify({"error": service.failure_message(e)}), 502


@bp.route("/bots/<int:bot_id>/knowledge/<int:document_id>/train", methods=["POST"])
@login_required
def bot_document_train(bot_id, document_id):
    bot = _require_bot(bot_id)
    document = _configurable_document(bot, document_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
        return redirect(url_for("dashboard.bot_document_chunks", bot_id=bot.id, document_id=document.id))
    params, error = service.parse_chunk_params(request.form)
    if error:
        flash(error, "error")
        return redirect(url_for("dashboard.bot_document_chunks", bot_id=bot.id, document_id=document.id))
    service.queue_training(document, *params)
    flash(f"Đã lưu cấu hình chunk — {document.filename} đang xếp hàng huấn luyện nền.", "success")
    return redirect(url_for("dashboard.bot_knowledge", bot_id=bot.id))


@bp.route("/bots/<int:bot_id>/knowledge/status")
@login_required
def bot_knowledge_status(bot_id):
    bot = _require_bot(bot_id)
    return jsonify(service.status_snapshot(bot.id))
