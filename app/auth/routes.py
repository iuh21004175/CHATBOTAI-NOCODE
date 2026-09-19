"""Route layer cho blueprint auth: render trang Đăng nhập (theo thiết kế demo), xử lý form
đăng nhập/đăng xuất, và endpoint JSON /me cho client gọi lại thông tin session hiện tại.
"""
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import limiter, oauth

from . import service

bp = Blueprint("auth", __name__, url_prefix="/auth")


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    mode = request.form.get("mode") or request.args.get("mode") or "password"
    mode = mode if mode in ("password", "magic") else "password"

    if request.method == "GET":
        return render_template(
            "auth/login.html", csrf_token=service.ensure_csrf_token(), error=None, info=None, email="", mode=mode
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
            ), 400

        magic_url = url_for(
            "auth.magic_callback", token=service.generate_magic_link_token(email), _external=True
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
            ), 502

        return render_template(
            "auth/login.html",
            csrf_token=service.ensure_csrf_token(),
            error=None,
            info=f"Đã gửi link đăng nhập tới {email}. Kiểm tra hộp thư đến (và mục Spam).",
            email=email,
            mode=mode,
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
        ), 401

    service.login(user, remember=remember)
    next_url = request.args.get("next")
    return redirect(next_url or url_for("dashboard.index"))


@bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per minute", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    if request.method == "GET":
        return render_template(
            "auth/register.html",
            csrf_token=service.ensure_csrf_token(),
            error=None,
            full_name="",
            email="",
            team_name="",
        )

    full_name = request.form.get("full_name", "")
    email = request.form.get("email", "")
    team_name = request.form.get("team_name", "")
    password = request.form.get("password", "")
    confirm_password = request.form.get("confirm_password", "")

    form_state = dict(full_name=full_name, email=email, team_name=team_name)

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
    return redirect(url_for("dashboard.index"))


@bp.route("/google/login")
@limiter.limit("20 per minute")
def google_login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if not current_app.config["GOOGLE_CLIENT_ID"]:
        flash("Đăng nhập Google chưa được cấu hình (thiếu GOOGLE_CLIENT_ID/SECRET trong .env).", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@bp.route("/google/callback")
@limiter.limit("20 per minute")
def google_callback():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

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
    return redirect(url_for("dashboard.index"))


@bp.route("/facebook/login")
@limiter.limit("20 per minute")
def facebook_login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    if not current_app.config["FACEBOOK_CLIENT_ID"]:
        flash("Đăng nhập Facebook chưa được cấu hình (thiếu FACEBOOK_CLIENT_ID/SECRET trong .env).", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.facebook_callback", _external=True)
    return oauth.facebook.authorize_redirect(redirect_uri)


@bp.route("/facebook/callback")
@limiter.limit("20 per minute")
def facebook_callback():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

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
    return redirect(url_for("dashboard.index"))


@bp.route("/magic/callback")
@limiter.limit("20 per minute")
def magic_callback():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))

    email, error = service.verify_magic_link_token(request.args.get("token", ""))
    if error:
        flash(error, "error")
        return redirect(url_for("auth.login", mode="magic"))

    user = service.find_or_create_user(email, "")
    service.login(user)
    return redirect(url_for("dashboard.index"))


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    if service.verify_csrf_token(request.form.get("csrf_token", "")):
        service.logout()
        flash("Đã đăng xuất.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/me", methods=["GET"])
@login_required
def me():
    return jsonify(service.serialize_current_user())
