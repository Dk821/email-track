from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class EmailAttachment(BaseModel):
    filename: str
    content_base64: str
    content_type: str = "application/octet-stream"


class SendEmailRequest(BaseModel):
    recipient: EmailStr
    subject: str
    body: str
    attachments: Optional[list[EmailAttachment]] = None


class EmailResponse(BaseModel):
    id: str
    tracking_id: str
    recipient: str
    subject: str
    status: str
    display_status: str = "Sent"
    sent_at: Optional[datetime] = None
    opened_at: Optional[datetime] = None
    open_count: int = 0
    first_open_ip: Optional[str] = None
    first_open_user_agent: Optional[str] = None
    last_opened_at: Optional[datetime] = None
    last_open_ip: Optional[str] = None
    last_open_user_agent: Optional[str] = None
    click_count: int = 0
    first_clicked_at: Optional[datetime] = None
    first_click_ip: Optional[str] = None
    first_click_user_agent: Optional[str] = None
    last_clicked_at: Optional[datetime] = None
    last_click_ip: Optional[str] = None
    last_click_user_agent: Optional[str] = None
    bounce_reason: Optional[str] = None
    bounced_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrackingEventResponse(BaseModel):
    id: int
    type: str
    timestamp: datetime
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    redirect_url: Optional[str] = None


class TrackingDetailResponse(EmailResponse):
    events: list[TrackingEventResponse] = Field(default_factory=list)


class AnalyticsSummary(BaseModel):
    total: int
    sent: int
    opened: int
    clicked: int
    bounced: int
    failed: int
    open_rate: float
    click_rate: float


class PaginatedEmails(BaseModel):
    total: int
    page: int
    per_page: int
    items: list[EmailResponse]

