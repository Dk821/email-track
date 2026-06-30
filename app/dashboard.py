from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_

from app.database import get_db
from app.models import Email

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db=Depends(get_db)):
    total = db.query(Email).count()
    opened = db.query(Email).filter(or_(Email.open_count > 0, Email.status == "opened")).count()
    clicked = db.query(Email).filter(or_(Email.click_count > 0, Email.status == "clicked")).count()
    unopened = total - opened
    rate = round((opened / total * 100) if total > 0 else 0, 1)

    emails = db.query(Email).order_by(Email.sent_at.desc()).all()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "total": total,
        "opened": opened,
        "clicked": clicked,
        "unopened": unopened,
        "rate": rate,
        "emails": emails,
    })
