import asyncio
import html
import unittest
from email import policy
from email.parser import BytesParser
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.analytics_service import AnalyticsService
from app.database import Base
from app.email_service import EmailService
from app.models import Email, TrackingEvent
from app.tracking import track_click, track_open
from app.tracking_service import TrackingService


def make_request(ip: str, user_agent: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "query_string": b"",
            "headers": [
                (b"x-forwarded-for", ip.encode("ascii")),
                (b"user-agent", user_agent.encode("ascii")),
            ],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


class HybridTrackingTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        self.db = self.Session()
        self.service = TrackingService("https://track.example.com")
        self.email = Email(
            id="email-1",
            tracking_id="tracking-1",
            recipient="person@example.com",
            subject="Tracked message",
            body="<p>Hello</p>",
            status="sent",
            open_count=0,
            click_count=0,
        )
        self.db.add(self.email)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_html_links_and_pixel_are_instrumented_once(self) -> None:
        original_url = "https://example.com/a%2Fb?x=1&y=two"
        source = (
            '<html><body><a class="cta" href="'
            + html.escape(original_url, quote=True)
            + '">Open</a><a href="mailto:team@example.com">Mail</a></body></html>'
        )

        tracked = self.service.prepare_html(source, "tracking-1")
        tracked_twice = self.service.prepare_html(tracked, "tracking-1")

        self.assertIn("/track/click/tracking-1?redirect=", tracked)
        self.assertIn('href="mailto:team@example.com"', tracked)
        self.assertEqual(tracked_twice.count("/track/open/tracking-1"), 1)
        encoded_target = tracked.split("?redirect=", 1)[1].split('"', 1)[0]
        self.assertEqual(unquote(html.unescape(encoded_target)), original_url)
        self.assertLess(tracked.index("/track/open/tracking-1"), tracked.index("</body>"))

    def test_open_and_click_events_keep_first_and_last_metadata(self) -> None:
        asyncio.run(
            self.service.record_open(
                self.db, "tracking-1", make_request("203.0.113.10", "FirstClient")
            )
        )
        first_opened_at = self.db.get(Email, "email-1").opened_at
        asyncio.run(
            self.service.record_open(
                self.db, "tracking-1", make_request("203.0.113.20", "SecondClient")
            )
        )
        asyncio.run(
            self.service.record_click(
                self.db,
                "tracking-1",
                "https://example.com/a%2Fb?x=1%2B2",
                make_request("203.0.113.30", "ClickClient"),
            )
        )

        self.db.expire_all()
        email_record = self.db.get(Email, "email-1")
        events = self.db.query(TrackingEvent).order_by(TrackingEvent.id).all()

        self.assertEqual(email_record.open_count, 2)
        self.assertEqual(email_record.opened_at, first_opened_at)
        self.assertEqual(email_record.first_open_ip, "203.0.113.10")
        self.assertEqual(email_record.last_open_ip, "203.0.113.20")
        self.assertEqual(email_record.click_count, 1)
        self.assertEqual(email_record.first_click_ip, "203.0.113.30")
        self.assertEqual(email_record.status, "clicked")
        self.assertEqual([event.event_type for event in events], ["open", "open", "click"])
        self.assertEqual(events[-1].redirect_url, "https://example.com/a%2Fb?x=1%2B2")

    def test_display_status_uses_engagement_priority(self) -> None:
        analytics = AnalyticsService()
        self.assertEqual(analytics.display_status(self.email), "Sent")
        self.email.open_count = 1
        self.assertEqual(analytics.display_status(self.email), "Opened (Estimated)")
        self.email.click_count = 1
        self.assertEqual(analytics.display_status(self.email), "Clicked (Engaged)")

    def test_tracking_routes_return_gif_and_preserve_redirect(self) -> None:
        target = "https://example.com/a%2Fb?x=1%2B2"
        request = make_request("203.0.113.40", "RouteClient")
        pixel = asyncio.run(track_open("tracking-1", request, self.db))
        click = asyncio.run(
            track_click("tracking-1", request, redirect=target, db=self.db)
        )

        self.assertEqual(pixel.status_code, 200)
        self.assertEqual(pixel.media_type, "image/gif")
        self.assertEqual(pixel.headers["cache-control"], "no-store, no-cache, must-revalidate, max-age=0")
        self.assertEqual(click.status_code, 302)
        self.assertEqual(click.headers["location"], target)
        self.assertEqual(click.headers["referrer-policy"], "no-referrer")

    def test_email_service_tracks_html_but_not_plaintext_urls(self) -> None:
        service = EmailService()
        service.username = "sender@example.com"
        service.password = "test-password"
        service.app_url = "https://track.example.com"
        service.tracking_service = TrackingService(service.app_url)
        target = "https://example.com/path?x=1&y=2"

        with patch.object(
            service,
            "_send_with_retry",
            new=AsyncMock(return_value=(True, "", None)),
        ) as sender:
            result = asyncio.run(
                service.send_email(
                    recipient="person@example.com",
                    subject="Hello",
                    html_body=f'<p><a href="{html.escape(target)}">Visit</a></p>',
                    tracking_id="tracking-1",
                )
            )

        self.assertTrue(result[0])
        message_bytes = sender.await_args.args[0]
        message = BytesParser(policy=policy.default).parsebytes(message_bytes)
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html_body = message.get_body(preferencelist=("html",)).get_content()

        self.assertIn(target, plain)
        self.assertNotIn("/track/click/", plain)
        self.assertIn("/track/click/tracking-1", html_body)
        self.assertIn("/track/open/tracking-1", html_body)


if __name__ == "__main__":
    unittest.main()
