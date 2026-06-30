import asyncio
import base64
import html as html_module
import logging
import re
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional

from aiosmtplib import SMTP
from aiosmtplib.errors import (
    SMTPAuthenticationError,
    SMTPConnectError,
    SMTPRecipientsRefused,
    SMTPResponseException,
)
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings
from app.tracking_service import TrackingService

logger = logging.getLogger(__name__)

_EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MAX_RETRIES = 3
_RETRYABLE_SMTP_CODES = {421, 450, 451, 452, 455, 501, 503, 550, 551, 552}


class EmailService:
    def __init__(self) -> None:
        self.host = settings.smtp_host
        self.port = settings.smtp_port
        self.username = settings.smtp_email
        self.password = settings.smtp_password
        self.from_name = settings.smtp_from_name
        self.use_tls = settings.smtp_use_tls
        self.start_tls = settings.smtp_start_tls and not settings.smtp_use_tls
        self.app_url = settings.app_url.rstrip("/")
        self.company_name = settings.company_name
        self.company_logo_url = settings.company_logo_url
        self.max_body_size = settings.max_body_size
        self.max_subject_length = settings.max_subject_length
        self.tracking_service = TrackingService(self.app_url)

        self._template_env = Environment(
            loader=FileSystemLoader("templates"),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self._template = self._template_env.get_template("email_template.html")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_email(
        self,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str = "",
        email_id: Optional[str] = None,
        tracking_id: Optional[str] = None,
        attachments: Optional[list] = None,
    ) -> tuple[bool, str, Optional[str]]:
        if not self.username or not self.password or "your" in self.username.lower():
            logger.warning("SMTP credentials not configured; skipping real send")
            return False, "SMTP not configured", None

        validation_error = self._validate_inputs(recipient, subject, html_body)
        if validation_error:
            return False, validation_error, validation_error

        attachment_error = self._validate_attachments(attachments)
        if attachment_error:
            return False, attachment_error, attachment_error

        html = self._build_html(subject, html_body)
        text = self._build_plain_text(html_body, text_body)
        tracking_id = tracking_id or email_id
        if tracking_id:
            html = self.tracking_service.prepare_html(html, tracking_id)
        message_bytes = self._create_message(recipient, subject, html, text, attachments)

        return await self._send_with_retry(message_bytes, recipient, subject)

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    def _validate_inputs(
        self, recipient: str, subject: str, html_body: str
    ) -> Optional[str]:
        if not recipient or not recipient.strip():
            return "Recipient address is empty"

        if not _EMAIL_REGEX.match(recipient.strip()):
            return f"Invalid email format: {recipient}"

        if not subject or not subject.strip():
            return "Subject is empty"

        if len(subject) > self.max_subject_length:
            return f"Subject exceeds max length of {self.max_subject_length} characters"

        if not html_body or not html_body.strip():
            return "Body is empty"

        if len(html_body.encode("utf-8")) > self.max_body_size:
            return f"Body exceeds max size of {self.max_body_size} bytes"

        return None

    def _validate_attachments(self, attachments: Optional[list]) -> Optional[str]:
        if not attachments:
            return None
        total_size = 0
        for att in attachments:
            try:
                raw = base64.b64decode(att.content_base64, validate=True)
            except Exception:
                return f"Attachment '{att.filename}' is not valid base64 data"
            total_size += len(raw)
        # Leave headroom for base64 and MIME overhead under common relay limits.
        if total_size > 20 * 1024 * 1024:
            return "Attachments exceed the 20MB total size limit"
        return None

    # ------------------------------------------------------------------
    # HTML rendering & tracking pixel
    # ------------------------------------------------------------------

    def _build_html(self, subject: str, body: str) -> str:
        return self._template.render(
            subject=subject,
            body=body,
            company_name=self.company_name,
            logo_url=self.company_logo_url,
            year=time.strftime("%Y"),
        )

    def _build_plain_text(self, html_body: str, text_body: str = "") -> str:
        if text_body and text_body.strip():
            return text_body.strip()

        text = html_body

        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>", "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</p>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
        text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
        text = re.sub(
            r"<a[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            r"\2 (\1)",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        text = re.sub(r"<[^>]+>", "", text)
        text = html_module.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n ", "\n", text)
        text = re.sub(r" \n", "\n", text)

        return text.strip()

    # ------------------------------------------------------------------
    # MIME message construction
    # ------------------------------------------------------------------

    def _create_message(
        self,
        recipient: str,
        subject: str,
        html: str,
        text: str,
        attachments: Optional[list] = None,
    ) -> bytes:
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text, "plain", "utf-8"))
        alt.attach(MIMEText(html, "html", "utf-8"))

        if attachments:
            msg = MIMEMultipart("mixed")
            msg.attach(alt)
            for att in attachments:
                raw = base64.b64decode(att.content_base64)
                part = MIMEApplication(raw, Name=att.filename)
                part["Content-Disposition"] = f'attachment; filename="{att.filename}"'
                if att.content_type:
                    part.set_type(att.content_type)
                msg.attach(part)
        else:
            msg = alt

        domain = self.username.split("@")[-1] if "@" in self.username else "localhost"

        msg["From"] = formataddr((self.from_name, self.username))
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=domain)
        msg["Date"] = formatdate(localtime=True)
        msg["Reply-To"] = self.username
        msg["MIME-Version"] = "1.0"

        return msg.as_bytes()

    # ------------------------------------------------------------------
    # SMTP send with retry
    # ------------------------------------------------------------------

    async def _send_with_retry(
        self, message_bytes: bytes, recipient: str, subject: str
    ) -> tuple[bool, str, Optional[str]]:
        last_error = ""
        bounce_reason: Optional[str] = None

        for attempt in range(_MAX_RETRIES):
            start = time.monotonic()
            try:
                async with SMTP(
                    hostname=self.host,
                    port=self.port,
                    timeout=30,
                    use_tls=self.use_tls,
                    start_tls=self.start_tls,
                ) as smtp:
                    await smtp.login(self.username, self.password)

                    errors, _ = await smtp.sendmail(
                        self.username, [recipient], message_bytes
                    )

                duration = time.monotonic() - start

                if errors:
                    recipient_err = errors.get(recipient)
                    if recipient_err:
                        code = recipient_err.code
                        message = recipient_err.message
                        bounce_reason = f"SMTP {code}: {message}"
                        logger.warning(
                            "Recipient rejected  recipient=%s  subject=%s  "
                            "code=%s  reason=%s  attempt=%d/%d  duration=%.2fs",
                            recipient, subject, code, message,
                            attempt + 1, _MAX_RETRIES, duration,
                        )
                        return False, bounce_reason, bounce_reason

                self._log_success(recipient, subject, attempt, duration)
                return True, "", None

            except SMTPAuthenticationError as e:
                duration = time.monotonic() - start
                last_error = f"Authentication failed: {e}"
                logger.error(
                    "SMTP auth error  recipient=%s  subject=%s  "
                    "error=%s  attempt=%d/%d  duration=%.2fs",
                    recipient, subject, last_error,
                    attempt + 1, _MAX_RETRIES, duration,
                )
                return False, last_error, last_error

            except SMTPRecipientsRefused as e:
                duration = time.monotonic() - start
                bounce_reason = (
                    "; ".join(
                        f"SMTP {err.code}: {err.message}"
                        for err in e.args[0].values()
                    )
                    if e.args
                    else str(e)
                )
                last_error = bounce_reason
                logger.error(
                    "Recipient refused  recipient=%s  subject=%s  "
                    "reason=%s  attempt=%d/%d  duration=%.2fs",
                    recipient, subject, bounce_reason,
                    attempt + 1, _MAX_RETRIES, duration,
                )
                return False, bounce_reason, bounce_reason

            except (SMTPConnectError, SMTPResponseException, TimeoutError,
                    ConnectionError, OSError) as e:
                duration = time.monotonic() - start

                if isinstance(e, SMTPResponseException):
                    last_error = f"SMTP {e.code}: {e.message}"
                    is_retryable = e.code in _RETRYABLE_SMTP_CODES
                else:
                    last_error = str(e)
                    is_retryable = True

                logger.error(
                    "SMTP error  recipient=%s  subject=%s  "
                    "error=%s  retry=%s  attempt=%d/%d  duration=%.2fs",
                    recipient, subject, last_error,
                    "yes" if is_retryable else "no",
                    attempt + 1, _MAX_RETRIES, duration,
                )

                if is_retryable and attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue

                return False, last_error, None

            except Exception as e:
                duration = time.monotonic() - start
                last_error = str(e)
                logger.error(
                    "Unexpected error  recipient=%s  subject=%s  "
                    "error=%s  attempt=%d/%d  duration=%.2fs",
                    recipient, subject, last_error,
                    attempt + 1, _MAX_RETRIES, duration,
                )
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(2 ** attempt)
                    continue
                return False, last_error, None

        return False, last_error, bounce_reason

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log_success(
        recipient: str, subject: str, attempt: int, duration: float
    ) -> None:
        logger.info(
            "Email sent  recipient=%s  subject=%s  "
            "attempt=%d/%d  duration=%.2fs",
            recipient, subject, attempt + 1, _MAX_RETRIES, duration,
        )


email_service = EmailService()

