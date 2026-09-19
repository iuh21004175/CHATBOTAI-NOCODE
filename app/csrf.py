"""CSRF token thủ công dùng chung cho mọi form trong app (login/register và các form khác) —
app chưa dùng Flask-WTF nên tự viết 1 chỗ duy nhất, tránh lặp lại ở từng blueprint.
"""
import secrets

from flask import session


def ensure_csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def verify_csrf_token(submitted: str) -> bool:
    expected = session.get("csrf_token")
    return bool(expected) and secrets.compare_digest(expected, submitted or "")
