"""Outgoing e-mail over SMTP (password reset links). Sent in a background thread so a slow mail
server never delays the request — nor reveals, by timing, whether an account exists."""

import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def _send(to: str, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(text)
    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
            if settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
        with server:
            if settings.smtp_user:
                server.login(settings.smtp_user, settings.smtp_password or "")
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.error("e-mail to %s failed: %s", to, exc)


def send(to: str, subject: str, text: str) -> None:
    threading.Thread(target=_send, args=(to, subject, text), daemon=True, name="mailer").start()
