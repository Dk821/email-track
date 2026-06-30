from urllib.parse import unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from starlette.responses import RedirectResponse, Response

from app.config import settings
from app.database import get_db
from app.models import Email
from app.tracking_service import TRANSPARENT_GIF, TrackingService

router = APIRouter()
tracking_service = TrackingService(settings.app_url)


@router.get("/track/open/{tracking_id}")
async def track_open(tracking_id: str, request: Request, db: Session = Depends(get_db)):
    await tracking_service.record_open(db, tracking_id, request)
    return Response(
        content=TRANSPARENT_GIF,
        media_type="image/gif",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@router.get("/track/click/{tracking_id}")
async def track_click(
    tracking_id: str,
    request: Request,
    redirect: str = Query(...),
    db: Session = Depends(get_db),
):
    redirect_url = redirect
    if not _is_safe_redirect(redirect_url):
        raise HTTPException(status_code=400, detail="redirect must be an absolute http(s) URL")

    await tracking_service.record_click(db, tracking_id, redirect_url, request)
    return RedirectResponse(
        url=redirect_url,
        status_code=302,
        headers={"Referrer-Policy": "no-referrer"},
    )


@router.get("/track/{email_id}")
async def legacy_track_email(email_id: str, request: Request, db: Session = Depends(get_db)):
    email = db.query(Email).filter(Email.id == email_id).first()
    tracking_id = email.tracking_id if email else email_id
    return await track_open(tracking_id, request, db)


@router.get("/click/{email_id}")
async def legacy_track_click(
    email_id: str,
    request: Request,
    url: str = Query(...),
    db: Session = Depends(get_db),
):
    email = db.query(Email).filter(Email.id == email_id).first()
    tracking_id = email.tracking_id if email else email_id
    return await track_click(tracking_id, request, redirect=url, db=db)


@router.post("/api/webhook/open")
async def open_webhook(payload: dict, request: Request, db: Session = Depends(get_db)):
    tracking_id = payload.get("tracking_id") or payload.get("email_id")
    if not tracking_id:
        raise HTTPException(status_code=400, detail="tracking_id is required")

    email = db.query(Email).filter(Email.id == tracking_id).first()
    if email:
        tracking_id = email.tracking_id

    email = await tracking_service.record_open(db, tracking_id, request)
    if not email:
        raise HTTPException(status_code=404, detail="Email not found")

    return {
        "status": "ok",
        "id": email.id,
        "tracking_id": email.tracking_id,
        "open_count": email.open_count,
    }


def _is_safe_redirect(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
