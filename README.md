# Hybrid Email Tracker

Self-hosted email delivery and engagement tracking built with FastAPI,
SQLAlchemy, SQLite, and Namecheap Private Email SMTP. No Mailgun or external
tracking API is used.

## Features

- SMTP delivery through `mail.privateemail.com`
- A unique UUID `tracking_id` for every email
- Hidden 1x1 GIF open tracking
- Automatic HTTP/HTTPS link rewriting for click tracking
- First/last metadata and event history for opens and clicks
- REST status and analytics APIs
- Dashboard statuses: Sent, Opened (Estimated), and Clicked (Engaged)
- Legacy tracking routes retained for existing emails

## Configuration

Copy `.env.example` to `.env` and set the mailbox credentials and public URL.
The tracking URL must be an internet-accessible HTTPS domain that routes to
this FastAPI application; `localhost` links cannot be reached by recipients.

Namecheap STARTTLS configuration:

```env
SMTP_HOST=mail.privateemail.com
SMTP_PORT=587
SMTP_USE_TLS=false
SMTP_START_TLS=true
```

For implicit TLS on port 465, use `SMTP_USE_TLS=true` and
`SMTP_START_TLS=false`.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` for the dashboard. In production, terminate TLS
at the application or reverse proxy and set `APP_URL` to that HTTPS origin.
The proxy should replace, rather than append untrusted values to,
`X-Forwarded-For` and `X-Real-IP`.

## API

| Method | Path | Description |
| --- | --- | --- |
| POST | `/api/send-email` | Send an SMTP email and return its tracking ID |
| GET | `/track/open/{tracking_id}` | Record an open and return a transparent GIF |
| GET | `/track/click/{tracking_id}?redirect=...` | Record a click and redirect with HTTP 302 |
| GET | `/api/emails` | Paginated email statuses |
| GET | `/api/email/{email_id}` | Status for an email |
| GET | `/api/email/{email_id}/tracking` | Status and event history |
| GET | `/api/tracking/{tracking_id}` | Status and events by tracking ID |
| GET | `/api/analytics` | Aggregate delivery and engagement analytics |

Example send request:

```bash
curl -X POST http://localhost:8000/api/send-email \
  -H "Content-Type: application/json" \
  -d '{"recipient":"user@example.com","subject":"Hello","body":"<p>Visit <a href=\"https://example.com\">our site</a>.</p>"}'
```

Open tracking is estimated because mail clients may block, cache, or prefetch
images. Security scanners and link previews can also generate opens or clicks.

## Test

```bash
python -m unittest discover -s tests -v
```
