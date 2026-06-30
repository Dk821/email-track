import base64
import html
import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import quote, urlparse

from fastapi import Request
from sqlalchemy import case, func, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.events import broadcaster
from app.models import Email, TrackingEvent

logger = logging.getLogger(__name__)

TRANSPARENT_GIF = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


class _LinkTrackingParser(HTMLParser):
    def __init__(self, service: "TrackingService", tracking_id: str) -> None:
        super().__init__(convert_charrefs=False)
        self.service = service
        self.tracking_id = tracking_id
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.parts.append(self._render_tag(tag, attrs, closed=False))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self.parts.append(self._render_tag(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        self.parts.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<![{data}]>")

    def _render_tag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
        closed: bool,
    ) -> str:
        rendered_attrs = []
        for name, value in attrs:
            if (
                tag.lower() == "a"
                and name.lower() == "href"
                and value
                and self.service._should_track_url(value.strip())
            ):
                value = self.service.tracking_url(self.tracking_id, value.strip())

            if value is None:
                rendered_attrs.append(name)
            else:
                rendered_attrs.append(f'{name}="{html.escape(value, quote=True)}"')

        suffix = " /" if closed else ""
        attributes = f" {' '.join(rendered_attrs)}" if rendered_attrs else ""
        return f"<{tag}{attributes}{suffix}>"


class TrackingService:
    def __init__(self, app_url: str) -> None:
        self.app_url = app_url.rstrip("/")

    def append_tracking_pixel(self, html: str, tracking_id: str) -> str:
        pixel_url = f"{self.app_url}/track/open/{tracking_id}"
        if pixel_url in html:
            return html

        pixel = (
            f'<img src="{pixel_url}" '
            'width="1" height="1" '
            'style="width:1px;height:1px;border:0;opacity:0;overflow:hidden;" '
            'alt="" aria-hidden="true">'
        )
        closing_body = re.search(r"</body\s*>", html, flags=re.IGNORECASE)
        if closing_body:
            return (
                html[: closing_body.start()]
                + pixel
                + "\n"
                + html[closing_body.start() :]
            )
        return html + pixel

    def rewrite_links_for_tracking(self, html: str, tracking_id: str) -> str:
        parser = _LinkTrackingParser(self, tracking_id)
        parser.feed(html)
        parser.close()
        return "".join(parser.parts)

    def prepare_html(self, html: str, tracking_id: str) -> str:
        html = self.rewrite_links_for_tracking(html, tracking_id)
        return self.append_tracking_pixel(html, tracking_id)

    def tracking_url(self, tracking_id: str, original_url: str) -> str:
        return (
            f"{self.app_url}/track/click/{tracking_id}"
            f"?redirect={quote(original_url, safe='')}"
        )

    async def record_open(self, db: Session, tracking_id: str, request: Request) -> Optional[Email]:
        now = datetime.now(timezone.utc)
        ip_address = self._client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        first_open = (
            db.query(Email.opened_at)
            .filter(Email.tracking_id == tracking_id)
            .scalar()
            is None
        )

        try:
            result = db.execute(
                update(Email)
                .where(Email.tracking_id == tracking_id)
                .values(
                    open_count=func.coalesce(Email.open_count, 0) + 1,
                    opened_at=case(
                        (Email.opened_at.is_(None), now), else_=Email.opened_at
                    ),
                    first_open_ip=case(
                        (Email.opened_at.is_(None), ip_address),
                        else_=Email.first_open_ip,
                    ),
                    first_open_user_agent=case(
                        (Email.opened_at.is_(None), user_agent),
                        else_=Email.first_open_user_agent,
                    ),
                    last_opened_at=now,
                    last_open_ip=ip_address,
                    last_open_user_agent=user_agent,
                    status=case(
                        (
                            Email.opened_at.is_(None)
                            & Email.status.notin_(
                                {"clicked", "bounced", "rejected", "failed"}
                            ),
                            "opened",
                        ),
                        else_=Email.status,
                    ),
                )
            )
            if not result.rowcount:
                db.rollback()
                logger.warning("Open event for unknown tracking_id: %s", tracking_id)
                return None

            db.add(
                TrackingEvent(
                    tracking_id=tracking_id,
                    event_type="open",
                    event_at=now,
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Could not record open for tracking_id=%s", tracking_id)
            return None

        email = self._get_email(db, tracking_id)
        if not email:
            return None

        await broadcaster.publish("email_opened", self._event_payload(email, "open", first_open))
        return email

    async def record_click(
        self, db: Session, tracking_id: str, redirect_url: str, request: Request
    ) -> Optional[Email]:
        now = datetime.now(timezone.utc)
        ip_address = self._client_ip(request)
        user_agent = request.headers.get("user-agent", "")
        first_click = (
            db.query(Email.first_clicked_at)
            .filter(Email.tracking_id == tracking_id)
            .scalar()
            is None
        )

        try:
            result = db.execute(
                update(Email)
                .where(Email.tracking_id == tracking_id)
                .values(
                    click_count=func.coalesce(Email.click_count, 0) + 1,
                    first_clicked_at=case(
                        (Email.first_clicked_at.is_(None), now),
                        else_=Email.first_clicked_at,
                    ),
                    first_click_ip=case(
                        (Email.first_clicked_at.is_(None), ip_address),
                        else_=Email.first_click_ip,
                    ),
                    first_click_user_agent=case(
                        (Email.first_clicked_at.is_(None), user_agent),
                        else_=Email.first_click_user_agent,
                    ),
                    last_clicked_at=now,
                    last_click_ip=ip_address,
                    last_click_user_agent=user_agent,
                    status="clicked",
                )
            )
            if not result.rowcount:
                db.rollback()
                logger.warning("Click event for unknown tracking_id: %s", tracking_id)
                return None

            db.add(
                TrackingEvent(
                    tracking_id=tracking_id,
                    event_type="click",
                    event_at=now,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    redirect_url=redirect_url,
                )
            )
            db.commit()
        except SQLAlchemyError:
            db.rollback()
            logger.exception("Could not record click for tracking_id=%s", tracking_id)
            return None

        email = self._get_email(db, tracking_id)
        if not email:
            return None

        await broadcaster.publish("email_clicked", self._event_payload(email, "click", first_click))
        return email

    def _get_email(self, db: Session, tracking_id: str) -> Optional[Email]:
        return db.query(Email).filter(Email.tracking_id == tracking_id).first()

    def _should_track_url(self, url: str) -> bool:
        lowered = url.lower()
        if lowered.startswith(("mailto:", "tel:", "#", "javascript:")):
            return False
        if lowered.startswith(f"{self.app_url.lower()}/track/"):
            return False
        parsed = urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    @staticmethod
    def _client_ip(request: Request) -> str:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()
        if request.client:
            return request.client.host
        return ""

    @staticmethod
    def _event_payload(email: Email, source: str, first_event: bool) -> dict:
        return {
            "id": email.id,
            "tracking_id": email.tracking_id,
            "recipient": email.recipient,
            "subject": email.subject,
            "status": email.status,
            "open_count": email.open_count or 0,
            "click_count": email.click_count or 0,
            "opened_at": email.opened_at.isoformat() if email.opened_at else None,
            "first_clicked_at": email.first_clicked_at.isoformat() if email.first_clicked_at else None,
            "first_event": first_event,
            "source": source,
        }
