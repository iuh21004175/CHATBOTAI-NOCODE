"""Tạo 1 team + 1 user Owner để test đăng nhập cục bộ.
Chạy: env/Scripts/python.exe scripts/seed_admin.py
"""
from werkzeug.security import generate_password_hash

from app import create_app
from app.models import Team, TeamMember, User
from extensions import db

EMAIL = "admin@example.com"
PASSWORD = "Admin@123"


def seed():
    app = create_app()
    with app.app_context():
        if User.query.filter_by(email=EMAIL).first():
            print(f"User {EMAIL} đã tồn tại, bỏ qua.")
            return

        team = Team(name="Demo Team", plan="free")
        db.session.add(team)
        db.session.flush()

        user = User(email=EMAIL, password_hash=generate_password_hash(PASSWORD), full_name="Admin Demo")
        db.session.add(user)
        db.session.flush()

        db.session.add(TeamMember(team_id=team.id, user_id=user.id, role="Owner"))
        db.session.commit()

        print(f"Đã tạo user demo: {EMAIL} / {PASSWORD} (team: {team.name})")


if __name__ == "__main__":
    seed()
