import logging
import smtplib
from email.message import EmailMessage

from app.config import Settings

log = logging.getLogger(__name__)


def send_email(s: Settings, to: str, subject: str, body: str) -> tuple[bool, bool, str | None]:
    """Retourne (ok, simulated, error). Sans SMTP_HOST, l'envoi est simulé (démonstration)."""
    if not s.smtp_host:
        log.info("[E-MAIL SIMULÉ] -> %s : %s", to, subject)
        return True, True, None
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = s.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(msg)
        return True, False, None
    except Exception as exc:
        return False, False, str(exc)[:200]
