"""Route layer cho các trang sau đăng nhập: Bảng điều khiển, tạo trợ lý mới, và trang tạm
"đang xây dựng" cho các mục nav/thao tác chưa có màn hình thật.
"""
from flask import Blueprint, Response, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from werkzeug.exceptions import RequestEntityTooLarge

from app import permissions
from app.csrf import ensure_csrf_token, verify_csrf_token
from app.customers import service as customers_service
from app.widget import appearance, icons, turns
from app.widget import service as widget_service
from extensions import limiter

from . import assistant_templates, service
from .assistant_templates import MAX_INSTRUCTIONS_CHARS

bp = Blueprint("dashboard", __name__)
bp.add_app_template_filter(service.filesize, "filesize")  # {{ 1536|filesize }} -> "1.5 KB"
bp.add_app_template_filter(service.format_message, "format_message")  # in đậm/mã an toàn cho tin nhắn hiển thị
bp.add_app_template_filter(service.message_segments, "message_segments")  # tin bot có danh sách sản phẩm -> nhiều tin riêng
bp.add_app_template_filter(service.explain_decision, "explain_decision")  # decision_trace -> nhãn tiếng Việt cạnh tin bot


@bp.errorhandler(RequestEntityTooLarge)
def upload_too_large(_error):
    """Request vượt MAX_CONTENT_LENGTH (tổng dung lượng 1 lần tải lên quá lớn) -> báo lỗi thân thiện.
    Quay lại đúng trang có form vừa gửi (không phải luôn về Cơ sở tri thức — nhiều route có upload)."""
    flash(f"Tổng dung lượng tải lên quá lớn (tối đa {service.filesize(current_app.config['MAX_CONTENT_LENGTH'])} mỗi lần).", "error")
    bot_id = (request.view_args or {}).get("bot_id")
    if not bot_id:
        return "Payload Too Large", 413
    back_to = "dashboard.bot_publish" if request.endpoint == "dashboard.bot_publish_icon_upload" else "dashboard.bot_knowledge"
    return redirect(url_for(back_to, bot_id=bot_id))


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
@permissions.requires("manage_bots")
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


# ---- Tin nhắn (Inbox) ----

@bp.route("/inbox")
@login_required
def inbox():
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    return render_template(
        "inbox/index.html",
        team=service.get_team(team_id),
        csrf_token=ensure_csrf_token(),
        initial_conversation_id=request.args.get("conversation_id", type=int),
    )


# ---- Khách hàng ----

def _customer_filters() -> dict:
    return {
        "search": request.args.get("q", "").strip(),
        "channel": request.args.get("channel", ""),
        "stage": request.args.get("stage", ""),
        "status": request.args.get("status", ""),
    }


@bp.route("/customers")
@login_required
def customers():
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    filters = _customer_filters()
    result = customers_service.list_customers(team_id, page=request.args.get("page", 1, type=int), **filters)
    return render_template(
        "customers/index.html",
        team=service.get_team(team_id),
        filters=filters,
        channel_options=customers_service.channel_options(team_id),
        stages=customers_service.STAGES,
        statuses=customers_service.STATUS_LABELS,
        csrf_token=ensure_csrf_token(),
        **result,
    )


@bp.route("/customers/export.csv")
@login_required
def customers_export():
    team_id = session.get("team_id")
    if not team_id:
        abort(404)
    content = customers_service.export_csv(team_id, **_customer_filters())
    return Response(
        content,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="khach-hang.csv"'},
    )


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
        elif len(service.clean_instructions(request.form.get("instructions"))) > MAX_INSTRUCTIONS_CHARS:
            flash(f"Chỉ dẫn tối đa {MAX_INSTRUCTIONS_CHARS} ký tự.", "error")
        else:
            error = service.update_bot_setup(bot, settings, request.form)
            if error:
                flash(error, "error")
            else:
                flash("Đã lưu thay đổi.", "success")
                return redirect(url_for("dashboard.bot_setup", bot_id=bot.id))

    return render_template(
        "bots/setup.html",
        bot=bot,
        settings=settings,
        engine=service.engine_form_context(settings),
        template_list=assistant_templates.TEMPLATES,
        template_data=assistant_templates.client_data(),
        instructions_max=MAX_INSTRUCTIONS_CHARS,
        speed_text_max=service.MAX_SPEED_TEXT_CHARS,
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

    if payload.get("async") is True:  # khung xem trước (embed.js): trả ngay mã lượt, tiến trình + câu trả lời lấy qua bot_preview_turn
        turn_id = service.start_preview_turn(bot, message, payload.get("history"), current_user.id)
        return jsonify(status="processing", turn_id=turn_id), 202

    try:
        result = service.preview_reply(bot, message, payload.get("history"))
    except Exception:
        current_app.logger.exception("preview-chat lỗi (bot_id=%s)", bot.id)
        return jsonify(error=service.PREVIEW_ERROR_TEXT), 502
    return jsonify(result)


@bp.route("/bots/<int:bot_id>/preview-chat/<string:turn_id>", methods=["GET"])
@login_required
@limiter.limit("300 per minute")
def bot_preview_turn(bot_id, turn_id):
    """Tiến trình + câu trả lời của lượt xem trước bất đồng bộ (sự kiện từ vị trí `after`); chỉ người đã tạo lượt đọc được."""
    bot = _require_bot(bot_id)
    result = turns.read(turn_id, service.preview_turn_owner(bot.id, current_user.id), request.args.get("after", 0, type=int))
    if result is None:
        return jsonify(error="Không tìm thấy lượt trả lời."), 404
    return jsonify(result)


@bp.route("/bots/<int:bot_id>/setup/optimize-instructions", methods=["POST"])
@login_required
@limiter.limit("10 per minute")
def bot_optimize_instructions(bot_id):
    """Nút "Tối ưu" ở Bước 1: nhờ LLM viết lại chỉ dẫn đang soạn (chưa lưu) cho rõ ràng, có cấu trúc."""
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error="Phiên làm việc đã hết hạn, vui lòng tải lại trang."), 400
    text = (request.get_json(silent=True) or {}).get("instructions")
    if not isinstance(text, str) or not service.clean_instructions(text):
        return jsonify(error="Chưa có chỉ dẫn để tối ưu."), 400
    text = service.clean_instructions(text)
    if len(text) > MAX_INSTRUCTIONS_CHARS:
        return jsonify(error=f"Chỉ dẫn tối đa {MAX_INSTRUCTIONS_CHARS} ký tự."), 400
    try:
        return jsonify(instructions=service.optimize_instructions(text))
    except Exception:
        current_app.logger.exception("optimize-instructions lỗi (bot_id=%s)", bot.id)
        return jsonify(error="Không tối ưu được chỉ dẫn lúc này. Kiểm tra DEEPSEEK_API_KEY rồi thử lại."), 502


@bp.route("/bots/<int:bot_id>/setup/cost-estimate", methods=["POST"])
@login_required
@limiter.limit("60 per minute")
def bot_cost_estimate(bot_id):
    """Chi phí ước tính (thấp nhất-cao nhất) của 1 câu hỏi theo cấu hình đang chọn ở Bước 1 — chưa lưu, không ghi gì."""
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.headers.get("X-CSRF-Token", "")):
        return jsonify(error="Phiên làm việc đã hết hạn, vui lòng tải lại trang."), 400
    if len(service.clean_instructions(request.form.get("instructions"))) > MAX_INSTRUCTIONS_CHARS:
        return jsonify(error=f"Chỉ dẫn tối đa {MAX_INSTRUCTIONS_CHARS} ký tự."), 422
    result, error = service.estimate_setup_cost(bot, service.get_or_create_settings(bot), request.form)
    if error:
        return jsonify(error=error), 422
    return jsonify(result)


# ---- Bước 3: Xuất bản ----

@bp.route("/bots/<int:bot_id>/publish", methods=["GET"])
@login_required
def bot_publish(bot_id):
    bot = _require_bot(bot_id)
    settings = service.get_or_create_settings(bot)

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
        custom_icon_key=icons.CUSTOM_ICON_KEY,
        custom_icon_url=widget_service.icon_url(bot, settings),  # None nếu chưa từng tải icon
        max_icon_mb=service.MAX_ICON_MB,
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


@bp.route("/bots/<int:bot_id>/publish/domains", methods=["POST"])
@login_required
@permissions.requires("publish")
def bot_publish_domain_add(bot_id):
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.add_widget_domain(bot, request.form.get("widget_domain", ""))
        flash(error or "Đã thêm domain được phép nhúng widget.", "error" if error else "success")
    return redirect(url_for("dashboard.bot_publish", bot_id=bot.id))


@bp.route("/bots/<int:bot_id>/publish/domains/<int:domain_id>/delete", methods=["POST"])
@login_required
@permissions.requires("publish")
def bot_publish_domain_delete(bot_id, domain_id):
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    elif service.remove_widget_domain(bot, domain_id):
        flash("Đã xóa domain.", "success")
    else:
        flash("Không tìm thấy domain.", "error")
    return redirect(url_for("dashboard.bot_publish", bot_id=bot.id))


@bp.route("/bots/<int:bot_id>/publish/appearance", methods=["POST"])
@login_required
@permissions.requires("publish")
def bot_publish_appearance(bot_id):
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.update_widget_appearance(service.get_or_create_settings(bot), request.form)
        flash(error or "Đã lưu giao diện widget. Website đã nhúng sẽ cập nhật sau tối đa vài phút.", "error" if error else "success")
    return redirect(url_for("dashboard.bot_publish", bot_id=bot.id))


@bp.route("/bots/<int:bot_id>/publish/icon", methods=["POST"])
@login_required
@permissions.requires("publish")
def bot_publish_icon_upload(bot_id):
    bot = _require_bot(bot_id)
    if not verify_csrf_token(request.form.get("csrf_token", "")):
        flash("Phiên làm việc đã hết hạn, vui lòng thử lại.", "error")
    else:
        error = service.update_widget_icon(bot, service.get_or_create_settings(bot), request.files.get("icon_file"))
        flash(error or "Đã cập nhật icon.", "error" if error else "success")
    return redirect(url_for("dashboard.bot_publish", bot_id=bot.id))


# ---- Lịch sử chat (mục theo dõi riêng, ngoài luồng 3 bước tạo bot) ----

@bp.route("/bots/<int:bot_id>/history")
@login_required
def bot_history(bot_id):
    bot = _require_bot(bot_id)
    search = request.args.get("q", "")
    channel = request.args.get("channel", "")
    decision = request.args.get("decision", "")
    if decision not in service.DECISION_LABELS:
        decision = ""
    conversations = service.list_conversations(bot_id, search=search, channel=channel, decision=decision)

    conv_id = request.args.get("conversation_id", type=int)
    selected = None
    messages = []
    if conversations:
        selected = next((c for c in conversations if c.id == conv_id), None) or conversations[0]
        messages = service.get_conversation_messages(selected.id)

    return render_template(
        "bots/history.html",
        bot=bot,
        step=0,  # không thuộc 3 bước Thiết lập → Cơ sở tri thức → Xuất bản: không bước nào active/done
        step_name="Lịch sử chat",
        conversations=conversations,
        selected=selected,
        messages=messages,
        search=search,
        channel=channel,
        decision=decision,
        decision_labels=service.DECISION_LABELS,
        decision_stats=service.decision_stats(bot_id),
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
