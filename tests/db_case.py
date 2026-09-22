"""Nền cho test có DB. CHỈ chạy khi DATABASE_URL trỏ tới DB tên kết thúc `_test` (xem tests/helpers.db_test_enabled) —
setUp XÓA sạch dữ liệu các bảng nên tuyệt đối không được chạy trên DB thật."""
import unittest
from unittest import mock

from sqlalchemy import text

from tests.helpers import FakeLLM, db_test_enabled

_TABLES_IN_DELETE_ORDER = (
    "conversation_message_embeddings", "structured_memory", "conversation_state", "bot_intent_config", "messages",
    "conversations", "customers", "documents", "followups", "bot_settings", "bots", "api_tokens", "team_members", "users", "teams",
)


@unittest.skipUnless(db_test_enabled(), "cần DATABASE_URL trỏ tới DB *_test (xem tests/README.md)")
class DbCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app

        cls.app = create_app()

    def setUp(self):
        from app.dashboard import service
        from app.models import Team, TeamMember, User
        from extensions import db

        # App-context RIÊNG cho mỗi test: nếu dùng chung 1 context cho cả lớp thì `g` (nơi Flask-Login giữ user hiện tại) rò từ
        # request đã đăng nhập sang request ẩn danh của test khác
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.addCleanup(self.ctx.pop)
        self.addCleanup(db.session.remove)
        self.db = db
        self.service = service
        db.session.rollback()
        db.session.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in _TABLES_IN_DELETE_ORDER:
            db.session.execute(text(f"DELETE FROM {table}"))
        db.session.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        db.session.commit()

        self.team = self.make_team("Team A")
        self.bot = service.create_bot(self.team.id, "Bot A")

        # LLM + truy xuất giả (không mạng, không Chroma thật)
        self.llm = FakeLLM()
        from core import rag_engine
        from tests.test_engine import retrieval

        self.retrieval = retrieval()
        for patcher in (
            mock.patch("core.context_engine.engine.deepseek_call", lambda temperature, max_tokens: self.llm),
            mock.patch.object(rag_engine, "retrieve", lambda *a, **k: self.retrieval),
            mock.patch("app.inbox.service.emit_message", lambda *a, **k: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_team(self, name):
        from app.models import Team, TeamMember, User

        team = Team(name=name)
        self.db.session.add(team)
        self.db.session.flush()
        user = User(email=f"{name.replace(' ', '').lower()}@example.com", password_hash="x", full_name=name)
        self.db.session.add(user)
        self.db.session.flush()
        self.db.session.add(TeamMember(team_id=team.id, user_id=user.id, role="Owner"))
        self.db.session.commit()
        team.user = user
        return team

    def conversation(self, bot=None):
        from app.models import Conversation

        conversation = Conversation(bot_id=(bot or self.bot).id, channel="web_widget", visitor_id="visitor-1")
        self.db.session.add(conversation)
        self.db.session.commit()
        return conversation

    def add_message(self, conversation, sender, content):
        from app.models import Message

        message = Message(conversation_id=conversation.id, sender=sender, content=content)
        self.db.session.add(message)
        self.db.session.commit()
        return message

    def set_settings(self, **values):
        settings = self.service.get_or_create_settings(self.bot)
        for name, value in values.items():
            setattr(settings, name, value)
        self.db.session.commit()
        return settings
