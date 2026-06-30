import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi import Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.analytics_service import analytics_service
from app.config import settings
from app.database import Base, engine, get_db, sync_email_schema
from app.dashboard import router as dashboard_router
from app.email_service import email_service
from app.events import broadcaster
from app.models import Email, TrackingEvent  # noqa: F401 - ensure models are registered
from app.schemas import (
    AnalyticsSummary,
    EmailResponse,
    PaginatedEmails,
    SendEmailRequest,
    TrackingDetailResponse,
)
from app.tracking import router as tracking_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_email_schema()
    Base.metadata.create_all(bind=engine)
    sync_email_schema()
    yield


app = FastAPI(title="dinesh kumar", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(dashboard_router)
app.include_router(tracking_router)


@app.get("/api/events")
async def email_events():
    queue = await broadcaster.subscribe()

    async def event_stream():
        try:
            yield ": connected\n\n"
            while True:
                payload = await queue.get()
                yield payload
        finally:
            await broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/send-email")
async def send_email(payload: SendEmailRequest, db: Session = Depends(get_db)):
    email_id = str(uuid.uuid4())
    tracking_id = str(uuid.uuid4())

    email = Email(
        id=email_id,
        tracking_id=tracking_id,
        recipient=payload.recipient,
        subject=payload.subject,
        body=payload.body,
        status="pending",
        sent_at=None,
        open_count=0,
        click_count=0,
    )
    db.add(email)
    db.commit()

    success, error, bounce_reason = await email_service.send_email(
        recipient=payload.recipient,
        subject=payload.subject,
        html_body=payload.body,
        tracking_id=tracking_id,
        attachments=payload.attachments,
    )

    if not success:
        if bounce_reason:
            email.status = "rejected"
            email.bounce_reason = bounce_reason
            email.bounced_at = datetime.now(timezone.utc)
        else:
            email.status = "failed"
        db.commit()
        return {
            "id": email_id,
            "tracking_id": tracking_id,
            "status": email.status,
            "message": f"{email.status}: {error}",
            "bounce_reason": bounce_reason,
        }

    email.status = "sent"
    email.sent_at = datetime.now(timezone.utc)
    db.commit()
    return {
        "id": email_id,
        "tracking_id": tracking_id,
        "status": "sent",
        "message": "Email sent successfully",
    }


@app.get("/api/emails", response_model=PaginatedEmails)
def list_emails(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    query = db.query(Email)

    if search:
        query = query.filter(
            Email.recipient.ilike(f"%{search}%") | Email.subject.ilike(f"%{search}%")
        )
    if status:
        query = query.filter(Email.status == status)

    total = query.count()
    items = (
        query.order_by(Email.sent_at.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return {
        "total": total,
        "page": page,
        "per_page": per_page,
        "items": [analytics_service.tracking_status(email) for email in items],
    }


@app.get("/api/email/{email_id}", response_model=EmailResponse)
def get_email(email_id: str, db: Session = Depends(get_db)):
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return analytics_service.tracking_status(email)


@app.get("/api/email/{email_id}/tracking", response_model=TrackingDetailResponse)
def get_email_tracking(email_id: str, db: Session = Depends(get_db)):
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")
    return analytics_service.tracking_detail(db, email)


@app.get("/api/tracking/{tracking_id}", response_model=TrackingDetailResponse)
def get_tracking_status(tracking_id: str, db: Session = Depends(get_db)):
    email = db.query(Email).filter(Email.tracking_id == tracking_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Tracking id not found")
    return analytics_service.tracking_detail(db, email)


@app.get("/api/analytics", response_model=AnalyticsSummary)
def get_analytics(db: Session = Depends(get_db)):
    return analytics_service.summary(db)


@app.post("/api/bounce-webhook")
async def bounce_webhook(
    email_id: str,
    reason: str = "",
    db: Session = Depends(get_db),
):
    email = db.query(Email).filter(Email.id == email_id).first()
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    email.status = "bounced"
    email.bounce_reason = reason or "Bounced"
    email.bounced_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("Email %s bounced: %s", email_id, reason)
    return {"status": "bounced", "email_id": email_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
