"""Application factory — khởi tạo toàn bộ extension và đăng ký blueprint đúng 1 lần
khi app start (không để mỗi request/route tự mở kết nối riêng)."""
from flask import Flask, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from extensions import db, migrate, socketio, login_manager, limiter, oauth, mail


def create_app(config_object="config.Config"):
    app = Flask(__name__)
    app.config.from_object(config_object)
    # Sau reverse proxy/tunnel (ngrok, nginx...) trình duyệt vào bằng https nhưng proxy chuyển tiếp về Flask bằng http, kèm X-Forwarded-Proto.
    # Không tin header này thì url_for(_external=True) sinh "http://..." -> Google/Facebook OAuth lỗi redirect_uri_mismatch, link email
    # (magic link, mời thành viên) và URL embed widget sai scheme. Chỉ tin scheme (1 lớp proxy): Host đã do proxy giữ nguyên.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

    db.init_app(app)
    migrate.init_app(app, db)
    socketio.init_app(
        app,
        message_queue=app.config["REDIS_URL"],
        channel=app.config["SOCKETIO_CHANNEL"],
        cors_allowed_origins="*",
    )
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Vui lòng đăng nhập để tiếp tục."
    login_manager.login_message_category = "info"
    limiter.init_app(app)
    oauth.init_app(app)
    mail.init_app(app)

    from app.auth.routes import bp as auth_bp
    from app.dashboard.routes import bp as dashboard_bp
    from app.bots.routes import bp as bots_bp
    from app.knowledge.routes import bp as knowledge_bp
    from app.inbox.routes import bp as inbox_bp
    from app.followup.routes import bp as followup_bp
    from app.customers.routes import bp as customers_bp
    from app.reports.routes import bp as reports_bp
    from app.api_tokens.routes import bp as api_tokens_bp
    from app.profile.routes import bp as profile_bp
    from app.profile.routes import page_bp as profile_page_bp
    from app.payments.routes import bp as payments_bp
    from app.widget.routes import bp as widget_bp
    from app.team.routes import bp as team_bp
    from app.internal.routes import bp as internal_bp
    from app.modules.routes import bp as modules_bp
    from app.builder.routes import bp as builder_bp

    from app.dashboard import events  # noqa: F401 — đăng ký handler Socket.IO (trạng thái xử lý tài liệu)
    from app.inbox import events as inbox_events  # noqa: F401 — đăng ký handler Socket.IO (tin nhắn mới trong Inbox)
    from app.widget import events as widget_events  # noqa: F401 — đăng ký handler Socket.IO của widget khách (namespace /widget, xác thực bằng public_id + Origin)
    from app import models  # noqa: F401 — đăng ký model với SQLAlchemy metadata cho Flask-Migrate

    for bp in (
        auth_bp,
        dashboard_bp,
        bots_bp,
        knowledge_bp,
        inbox_bp,
        followup_bp,
        customers_bp,
        reports_bp,
        api_tokens_bp,
        profile_bp,
        profile_page_bp,
        payments_bp,
        widget_bp,
        team_bp,
        internal_bp,
        modules_bp,
        builder_bp,
    ):
        app.register_blueprint(bp)

    from app import permissions

    permissions.init_app(app)  # xác thực lại tư cách thành viên của session["team_id"] trước mỗi request + biến nav cho template

    @login_manager.user_loader
    def load_user(user_id):
        from app.models import User

        return db.session.get(User, int(user_id))

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/")
    def index():
        from flask_login import current_user

        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    return app
