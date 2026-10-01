"""Route layer cho blueprint auth: render trang Đăng nhập (theo thiết kế demo), xử lý form
đăng nhập/đăng xuất, và endpoint JSON /me cho client gọi lại thông tin session hiện tại.
"""
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from extensions import limiter, oauth

from . import service

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    # "next": trang cần quay lại sau đăng nhập (vd khách bấm "Nạp Credit" từ trang chủ khi chưa đăng nhập -> Flask-Login tự thêm
    # ?next=<url gốc> khi chặn truy cập /profile). Trên GET lấy từ query string; POST/redirect nội bộ đọc lại qua ô ẩn "next" của
    # chính form này (server tự render lại form khi có lỗi) để không phụ thuộc query string của action URL. Luôn lọc qua
    # safe_next_url TRƯỚC KHI đưa vào bất kỳ đâu (kể cả hiện lại trong ô ẩn) — chặn open redirect ngay từ đầu.
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    mode = request.form.get("mode") or request.args.get("mode") or "password"
    mode = mode if mode in ("password", "magic") else "password"
    next_url = service.safe_next_url(request.form.get("next") or request.args.get("next"), "")

    if request.method == "GET":
        return render_template(
            "auth/login.html", csrf_token=service.ensure_csrf_token(), error=None, info=None, email="", mode=mode, next=next_url
        )

    email = request.form.get("email", "")

    if not service.verify_csrf_token(request.form.get("csrf_token", "")):
        return render_template(
            "auth/login.html",
            csrf_token=service.ensure_csrf_token(),
            error="Phiên làm việc đã hết hạn, vui lòng thử lại.",
            info=None,
            email=email,
            mode=mode,
            next=next_url,
        ), 400

    if mode == "magic":
        if not service.EMAIL_RE.match((email or "").strip().lower()):
            return render_template(
                "auth/login.html",
                csrf_token=service.ensure_csrf_token(),
                error="Email không hợp lệ.",
                info=None,
                email=email,
                mode=mode,
                next=next_url,
            ), 400

        # next đi kèm token trong chính link email (không phải session): magic link thường được mở ở trình duyệt/thiết bị KHÁC nơi bấm gửi.
        magic_url = url_for(
            "auth.magic_callback", token=service.generate_magic_link_token(email),
            next=next_url or None, _external=True,
        )
        try:
            service.send_magic_link_email(email, magic_url)
        except Exception:
            current_app.logger.exception("Không gửi được magic link email")
            return render_template(
                "auth/login.html",
                csrf_token=service.ensure_csrf_token(),
                error="Không gửi được email lúc này, vui lòng thử lại sau hoặc dùng cách đăng nhập khác.",
                info=None,
                email=email,
                mode=mode,
                next=next_url,
            ), 502

        return render_template(
            "auth/login.html",
            csrf_token=service.ensure_csrf_token(),
            error=None,
            info=f"Đã gửi link đăng nhập tới {email}. Kiểm tra hộp thư đến (và mục Spam).",
            email=email,
            mode=mode,
            next=next_url,
        )

    password = request.form.get("password", "")
    remember = bool(request.form.get("remember"))

    user = service.authenticate(email, password)
    if user is None:
        return render_template(
            "auth/login.html",
            csrf_token=service.ensure_csrf_token(),
            error="Email hoặc mật khẩu không đúng.",
            email=email,
            mode=mode,
            next=next_url,
        ), 401

    service.login(user, remember=remember)
    return redirect(next_url or url_for("dashboard.index"))


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    next_url = service.safe_next_url(request.form.get("next") or request.args.get("next"), "")

    if request.method == "GET":
        return render_template(
            "auth/register.html",
            csrf_token=service.ensure_csrf_token(),
            error=None,
            full_name="",
            email="",
            team_name="",
            next=next_url,
        )

    full_name = request.form.get("full_name", "")
    email = request.form.get("email", "")
    team_name = request.form.get("team_name", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    form_state = dict(full_name=full_name, email=email, team_name=team_name, next=next_url)

    if not service.verify_csrf_token(request.form.get("csrf_token", "")):
        return render_template(
            "auth/register.html",
            csrf_token=service.ensure_csrf_token(),
            error="Phiên làm việc đã hết hạn, vui lòng thử lại.",
            **form_state,
        ), 400

    error = service.validate_registration(full_name, email, password, confirm_password)
    if error:
        return render_template(
            "auth/register.html", csrf_token=service.ensure_csrf_token(), error=error, **form_state
        ), 400

    user = service.register(full_name, email, password, team_name)
    service.login(user)
    flash("Tạo tài khoản thành công!", "success")
    return redirect(next_url or url_for("dashboard.index"))


@bp.route("/google/login")
@limiter.limit("20 per minute")
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if not current_app.config["GOOGLE_CLIENT_ID"]:
        flash("Đăng nhập Google chưa được cấu hình (thiếu GOOGLE_CLIENT_ID/SECRET trong .env).", "error")
        return redirect(url_for("auth.login"))
    # Google không trả lại tham số của ta (chỉ redirect_uri + state riêng của authlib) -> giữ "next" qua session, đọc lại ở callback.
    # Cùng trình duyệt/session trong suốt vòng round-trip nên không mất, dù server có nhiều tab/luồng khác nhau đang xử lý.
    session["auth_next"] = service.safe_next_url(request.args.get("next"), "")
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route("/google/callback")
@limiter.limit("20 per minute")
def google_callback():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    next_url = service.safe_next_url(session.pop("auth_next", None), "")

    try:
        token = oauth.google.authorize_access_token()
    except Exception:
        flash("Đăng nhập Google thất bại hoặc đã bị hủy.", "error")
        return redirect(url_for("auth.login"))

    userinfo = token.get("userinfo") or oauth.google.userinfo(token=token)
    if not userinfo or not userinfo.get("email_verified", True):
        flash("Không thể xác thực email Google.", "error")
        return redirect(url_for("auth.login"))

    user = service.find_or_create_user(userinfo["email"], userinfo.get("name", ""))
    service.login(user)
    return redirect(next_url or url_for("dashboard.index"))


@bp.route("/facebook/login")
@limiter.limit("20 per minute")
def facebook_login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if not current_app.config["FACEBOOK_CLIENT_ID"]:
        flash("Đăng nhập Facebook chưa được cấu hình (thiếu FACEBOOK_CLIENT_ID/SECRET trong .env).", "error")
        return redirect(url_for("auth.login"))
    session["auth_next"] = service.safe_next_url(request.args.get("next"), "")  # xem chú thích ở google_login
    redirect_uri = url_for("auth.facebook_callback", _external=True)
    return oauth.facebook.authorize_redirect(redirect_uri)


@bp.route("/facebook/callback")
@limiter.limit("20 per minute")
def facebook_callback():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    next_url = service.safe_next_url(session.pop("auth_next", None), "")

    try:
        token = oauth.facebook.authorize_access_token()
    except Exception:
        flash("Đăng nhập Facebook thất bại hoặc đã bị hủy.", "error")
        return redirect(url_for("auth.login"))

    profile = oauth.facebook.get("me", params={"fields": "id,name,email"}, token=token).json()
    email = profile.get("email")
    if not email:
        flash(
            "Tài khoản Facebook này không cung cấp email (có thể chưa xác minh email với Facebook) "
            "— vui lòng đăng nhập bằng mật khẩu hoặc Google.",
            "error",
        )
        return redirect(url_for("auth.login"))

    user = service.find_or_create_user(email, profile.get("name", ""))
    service.login(user)
    return redirect(next_url or url_for("dashboard.index"))


@bp.route("/magic/callback")
@limiter.limit("20 per minute")
def magic_callback():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    # "next" đi trong chính link email (xem login(): mode=="magic"), không qua session — magic link thường mở ở trình duyệt/thiết bị KHÁC nơi
    # bấm gửi. Lọc lại qua safe_next_url dù link do chính server phát: phòng khi ai đó sửa tay tham số trên URL trước khi bấm.
    next_url = service.safe_next_url(request.args.get("next"), "")

    email, error = service.verify_magic_link_token(request.args.get("token", ""))
    if error:
        flash(error, "error")
        return redirect(url_for("auth.login", mode="magic"))

    user = service.find_or_create_user(email, "")
    service.login(user)
    return redirect(next_url or url_for("dashboard.index"))


@bp.app_context_processor
def shell_csrf():
    """Token CSRF cho form đăng xuất nằm trong sidebar của MỌI trang có shell (hàm, chỉ sinh token khi template gọi)."""
    return {"shell_csrf_token": service.ensure_csrf_token}


@bp.route("/logout", methods=["POST"])
def logout():
    # Không dùng @login_required: đăng xuất phải idempotent (bấm 2 lần, hoặc tab khác đã đăng xuất). Với @login_required, lượt thứ 2 bị đẩy về
    # /auth/login?next=/auth/logout kèm câu "Vui lòng đăng nhập", và sau khi đăng nhập lại sẽ bị chuyển tới route chỉ nhận POST (405).
    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))
    if not service.verify_csrf_token(request.form.get("csrf_token", "")):
        # KHÔNG bỏ qua im lặng: trước đây token sai vẫn redirect về /auth/login, nơi người đã đăng nhập bị đẩy ngược về /dashboard nên trông như
        # "đăng xuất không làm gì". Người dùng vẫn đang đăng nhập -> báo rõ và ở lại dashboard.
        flash("Phiên làm việc đã hết hạn, vui lòng thử đăng xuất lại.", "error")
        return redirect(url_for("dashboard.index"))
    from app.dashboard import events  # import cục bộ: tránh nạp toàn bộ dashboard (và vòng import) lúc đăng ký blueprint

    socket_key = session.get("socket_key")
    service.logout()
    events.disconnect_session_sockets(socket_key)
    flash("Đã đăng xuất.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify(service.serialize_current_user())
