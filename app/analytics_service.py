from sqlalchemy.orm import Session

from app.models import Email, TrackingEvent


class AnalyticsService:
    @staticmethod
    def display_status(email: Email) -> str:
        if email.status == "clicked" or (email.click_count or 0) > 0:
            return "Clicked (Engaged)"
        if email.status == "opened" or (email.open_count or 0) > 0:
            return "Opened (Estimated)"
        if email.status == "sent":
            return "Sent"
        return (email.status or "sent").title()

    def tracking_status(self, email: Email) -> dict:
        return {
            "id": email.id,
            "tracking_id": email.tracking_id,
            "recipient": email.recipient,
            "subject": email.subject,
            "status": email.status,
            "display_status": self.display_status(email),
            "sent_at": email.sent_at,
            "opened_at": email.opened_at,
            "open_count": email.open_count or 0,
            "first_open_ip": email.first_open_ip,
            "first_open_user_agent": email.first_open_user_agent,
            "last_opened_at": email.last_opened_at,
            "last_open_ip": email.last_open_ip,
            "last_open_user_agent": email.last_open_user_agent,
            "click_count": email.click_count or 0,
            "first_clicked_at": email.first_clicked_at,
            "first_click_ip": email.first_click_ip,
            "first_click_user_agent": email.first_click_user_agent,
            "last_clicked_at": email.last_clicked_at,
            "last_click_ip": email.last_click_ip,
            "last_click_user_agent": email.last_click_user_agent,
            "bounce_reason": email.bounce_reason,
            "bounced_at": email.bounced_at,
        }

    def tracking_detail(self, db: Session, email: Email) -> dict:
        events = (
            db.query(TrackingEvent)
            .filter(TrackingEvent.tracking_id == email.tracking_id)
            .order_by(TrackingEvent.event_at.desc())
            .all()
        )
        detail = self.tracking_status(email)
        detail["events"] = [
            {
                "id": event.id,
                "type": event.event_type,
                "timestamp": event.event_at,
                "ip": event.ip_address,
                "user_agent": event.user_agent,
                "redirect_url": event.redirect_url,
            }
            for event in events
        ]
        return detail

    def summary(self, db: Session) -> dict:
        total = db.query(Email).count()
        sent = db.query(Email).filter(Email.status == "sent").count()
        opened = db.query(Email).filter((Email.open_count > 0) | (Email.status == "opened")).count()
        clicked = db.query(Email).filter((Email.click_count > 0) | (Email.status == "clicked")).count()
        bounced = db.query(Email).filter(Email.status == "bounced").count()
        failed = db.query(Email).filter(Email.status.in_(["failed", "rejected"])).count()

        return {
            "total": total,
            "sent": sent,
            "opened": opened,
            "clicked": clicked,
            "bounced": bounced,
            "failed": failed,
            "open_rate": round((opened / total * 100) if total else 0, 1),
            "click_rate": round((clicked / total * 100) if total else 0, 1),
        }


analytics_service = AnalyticsService()
