"""Service layer cho blueprint auth: xác thực mật khẩu, quản lý session (team_id đang chọn),
CSRF token thủ công cho form đăng nhập (app chưa dùng Flask-WTF).
"""
import hashlib
import re

from flask import current_app, session
from flask_login import current_user, login_user, logout_user
from flask_mail import Message
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash

from app.csrf import ensure_csrf_token, verify_csrf_token  # noqa: F401 — re-export cho routes.py
from app.models import Team, TeamMember, User
from extensions import db, mail, redis_client

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

MAGIC_LINK_SALT = "magic-link"
MAGIC_LINK_MAX_AGE = 15 * 60  # 15 phút


def authenticate(email: str, password: str) -> User | None:
    """Kiểm tra email/mật khẩu, trả None nếu sai — không phân biệt lỗi "không có email" hay
    "sai mật khẩu" để tránh lộ thông tin tài khoản nào tồn tại."""
    email = (email or "").strip().lower()
    if not email or not password:
        return None
    user = User.query.filter_by(email=email).first()
    if user is None or user.password_hash is None:
        return None  # tài khoản chỉ đăng nhập qua Google, không có mật khẩu nội bộ
    if not check_password_hash(user.password_hash, password):
        return None
    return user


def validate_registration(full_name: str, email: str, password: str, confirm_password: str) -> str | None:
    """Trả về thông báo lỗi đầu tiên gặp phải, hoặc None nếu dữ liệu hợp lệ."""
    if not full_name or not full_name.strip():
        return "Vui lòng nhập họ tên."
    email = (email or "").strip().lower()
    if not email or not EMAIL_RE.match(email):
        return "Email không hợp lệ."
    if len(password or "") < 8:
        return "Mật khẩu cần tối thiểu 8 ký tự."
    if password != confirm_password:
        return "Mật khẩu nhập lại không khớp."
    if User.query.filter_by(email=email).first() is not None:
        return "Email này đã được đăng ký."
    return None


def register(full_name: str, email: str, password: str, team_name: str = "") -> User:
    """Tạo team mới + user Owner đầu tiên của team đó."""
    email = email.strip().lower()
    full_name = full_name.strip()
    team_name = team_name.strip() or f"Team của {full_name}"

    team = Team(name=team_name, plan="free")
    db.session.add(team)
    db.session.flush()

    user = User(email=email, password_hash=generate_password_hash(password), full_name=full_name)
    db.session.add(user)
    db.session.flush()

    db.session.add(TeamMember(team_id=team.id, user_id=user.id, role="Owner"))
    db.session.commit()

    return user


def find_or_create_user(email: str, full_name: str) -> User:
    """Đăng nhập không cần mật khẩu (OAuth Google/Facebook, hoặc Magic Link qua email): nếu email
    đã có tài khoản thì dùng lại (kể cả tài khoản tạo bằng mật khẩu thường — email đã được xác
    thực qua kênh khác nên an toàn để gộp), ngược lại tự tạo team + user mới (role Owner), giống
    luồng /auth/register nhưng không có mật khẩu."""
    email = email.strip().lower()
    user = User.query.filter_by(email=email).first()
    if user is not None:
        return user

    full_name = (full_name or email.split("@")[0]).strip()
    team = Team(name=f"Team của {full_name}", plan="free")
    db.session.add(team)
    db.session.flush()

    user = User(email=email, password_hash=None, full_name=full_name)
    db.session.add(user)
    db.session.flush()

    db.session.add(TeamMember(team_id=team.id, user_id=user.id, role="Owner"))
    db.session.commit()

    return user


def _magic_link_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"])


def generate_magic_link_token(email: str) -> str:
    return _magic_link_serializer().dumps(email.strip().lower(), salt=MAGIC_LINK_SALT)


def verify_magic_link_token(token: str) -> tuple[str | None, str | None]:
    """Trả (email, None) nếu hợp lệ, hoặc (None, thông_báo_lỗi). Token chỉ dùng được 1 lần —
    đánh dấu đã dùng trong Redis (TTL = thời gian còn lại của token) để chặn replay."""
    try:
        email = _magic_link_serializer().loads(token, salt=MAGIC_LINK_SALT, max_age=MAGIC_LINK_MAX_AGE)
    except SignatureExpired:
        return None, "Link đăng nhập đã hết hạn (chỉ có hiệu lực 15 phút) — vui lòng gửi lại."
    except BadSignature:
        return None, "Link đăng nhập không hợp lệ."

    used_key = "magic_link_used:" + hashlib.sha256(token.encode()).hexdigest()
    if not redis_client.set(used_key, "1", nx=True, ex=MAGIC_LINK_MAX_AGE):
        return None, "Link đăng nhập này đã được sử dụng — vui lòng gửi lại."
    return email, None


def send_magic_link_email(email: str, magic_url: str) -> None:
    msg = Message(
        subject="Link đăng nhập Chatbot AI",
        recipients=[email],
        body=(
            "Bấm vào link sau để đăng nhập (hết hạn sau 15 phút, chỉ dùng được 1 lần):\n\n"
            f"{magic_url}\n\nNếu bạn không yêu cầu email này, vui lòng bỏ qua."
        ),
        html=f"""
        <p>Bấm vào nút bên dưới để đăng nhập (link có hiệu lực trong 15 phút, chỉ dùng được 1 lần):</p>
        <p><a href="{magic_url}" style="display:inline-block;padding:12px 20px;background:#1D4ED8;
           color:#fff;border-radius:8px;text-decoration:none;font-weight:600;">Đăng nhập</a></p>
        <p>Hoặc copy link: {magic_url}</p>
        <p style="color:#5B6B82;font-size:13px;">Nếu bạn không yêu cầu email này, vui lòng bỏ qua.</p>
        """,
    )
    mail.send(msg)


def login(user: User, remember: bool = False) -> None:
    login_user(user, remember=remember)
    session.pop("csrf_token", None)  # xoay token sau khi đăng nhập để chống replay

    membership = TeamMember.query.filter_by(user_id=user.id).first()
    session["team_id"] = membership.team_id if membership else None


def logout() -> None:
    logout_user()
    session.pop("team_id", None)
    session.pop("csrf_token", None)


def serialize_current_user() -> dict:
    membership = TeamMember.query.filter_by(user_id=current_user.id).first()
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "team_id": membership.team_id if membership else None,
        "role": membership.role if membership else None,
    }
