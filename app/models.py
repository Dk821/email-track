import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.database import Base


class Email(Base):
    __tablename__ = "emails"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tracking_id = Column(String, unique=True, nullable=False, index=True, default=lambda: str(uuid.uuid4()))
    recipient = Column(String, nullable=False, index=True)
    subject = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    status = Column(String, default="sent")
    sent_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    open_count = Column(Integer, default=0)
    first_open_ip = Column(String, nullable=True)
    first_open_user_agent = Column(Text, nullable=True)
    last_opened_at = Column(DateTime, nullable=True)
    last_open_ip = Column(String, nullable=True)
    last_open_user_agent = Column(Text, nullable=True)
    click_count = Column(Integer, default=0)
    first_clicked_at = Column(DateTime, nullable=True)
    first_click_ip = Column(String, nullable=True)
    first_click_user_agent = Column(Text, nullable=True)
    last_clicked_at = Column(DateTime, nullable=True)
    last_click_ip = Column(String, nullable=True)
    last_click_user_agent = Column(Text, nullable=True)
    bounce_reason = Column(Text, nullable=True)
    bounced_at = Column(DateTime, nullable=True)


class TrackingEvent(Base):
    __tablename__ = "tracking_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tracking_id = Column(String, ForeignKey("emails.tracking_id"), nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    event_at = Column(DateTime, nullable=False)
    ip_address = Column(String, nullable=True)
    user_agent = Column(Text, nullable=True)
    redirect_url = Column(Text, nullable=True)
