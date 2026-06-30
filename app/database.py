from sqlalchemy import create_engine, inspect, text
from sqlalchemy import event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


if "sqlite" in settings.database_url:
    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def sync_email_schema():
    inspector = inspect(engine)
    if not inspector.has_table("emails"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("emails")}
    missing_columns = {
        "tracking_id": "VARCHAR",
        "first_open_ip": "VARCHAR",
        "first_open_user_agent": "TEXT",
        "last_opened_at": "DATETIME",
        "last_open_ip": "VARCHAR",
        "last_open_user_agent": "TEXT",
        "click_count": "INTEGER DEFAULT 0",
        "first_clicked_at": "DATETIME",
        "first_click_ip": "VARCHAR",
        "first_click_user_agent": "TEXT",
        "last_clicked_at": "DATETIME",
        "last_click_ip": "VARCHAR",
        "last_click_user_agent": "TEXT",
        "bounce_reason": "TEXT",
        "bounced_at": "DATETIME",
    }.items()

    with engine.begin() as connection:
        for column_name, column_type in missing_columns:
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE emails ADD COLUMN {column_name} {column_type}")
                )

        connection.execute(
            text(
                """
                UPDATE emails
                SET tracking_id = lower(hex(randomblob(4))) || '-' ||
                                  lower(hex(randomblob(2))) || '-' ||
                                  lower(hex(randomblob(2))) || '-' ||
                                  lower(hex(randomblob(2))) || '-' ||
                                  lower(hex(randomblob(6)))
                WHERE tracking_id IS NULL OR tracking_id = ''
                """
            )
        )
        connection.execute(text("UPDATE emails SET open_count = 0 WHERE open_count IS NULL"))
        connection.execute(text("UPDATE emails SET click_count = 0 WHERE click_count IS NULL"))
        connection.execute(text("UPDATE emails SET status = 'sent' WHERE status IS NULL OR status = ''"))
        connection.execute(
            text("CREATE UNIQUE INDEX IF NOT EXISTS ix_emails_tracking_id ON emails (tracking_id)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_emails_status ON emails (status)")
        )
        connection.execute(
            text("CREATE INDEX IF NOT EXISTS ix_emails_sent_at ON emails (sent_at)")
        )

        if inspector.has_table("tracking_events"):
            event_columns = {
                column["name"] for column in inspector.get_columns("tracking_events")
            }
            if "redirect_url" not in event_columns:
                connection.execute(
                    text("ALTER TABLE tracking_events ADD COLUMN redirect_url TEXT")
                )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tracking_events_tracking_id "
                    "ON tracking_events (tracking_id)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tracking_events_event_type "
                    "ON tracking_events (event_type)"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_tracking_events_tracking_time "
                    "ON tracking_events (tracking_id, event_at)"
                )
            )
