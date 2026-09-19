"""Job nền gửi FollowUp theo lịch đã đặt — quét bảng followups theo status=pending và
schedule_at <= now.
"""
from datetime import datetime

from app import create_app
from extensions import db
from app.models import Followup


def send_due_followups():
    app = create_app()
    with app.app_context():
        due = Followup.query.filter(
            Followup.status == "pending", Followup.schedule_at <= datetime.utcnow()
        ).all()
        for followup in due:
            # TODO: gửi tin nhắn FollowUp tới các conversation/customer liên quan
            followup.status = "sent"
        db.session.commit()


if __name__ == "__main__":
    send_due_followups()
