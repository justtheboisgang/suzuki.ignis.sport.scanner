"""SMTP email notification provider (optional, via env vars)."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

from ..config.settings import get_settings
from ..utils.logging import get_logger
from .base import Notification, NotificationProvider

log = get_logger("notify.email")


class EmailProvider(NotificationProvider):
    name = "email"

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.smtp_host and s.smtp_from and s.smtp_to)

    def send(self, notification: Notification) -> bool:
        if not self.enabled:
            return False
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = notification.title[:200]
        msg["From"] = s.smtp_from
        msg["To"] = s.smtp_to
        msg.set_content(notification.body)
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as server:
                server.starttls()
                if s.smtp_user and s.smtp_password:
                    server.login(s.smtp_user, s.smtp_password)
                server.send_message(msg)
            return True
        except Exception as exc:  # pragma: no cover - network/SMTP variance
            log.warning("email send failed: %s", exc)
            return False
