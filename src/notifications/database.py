"""Database notification provider — persists every alert so the dashboard has a
durable feed with no external service required."""

from __future__ import annotations

from ..database.writer import run_write
from ..models.feedback import Notification as NotificationRow
from ..utils.logging import get_logger
from .base import Notification, NotificationProvider

log = get_logger("notify.db")


class DatabaseProvider(NotificationProvider):
    name = "database"

    def send(self, notification: Notification) -> bool:
        # Serialised, retrying write; swallows on failure so a locked DB can
        # never crash the scan that produced the alert.
        run_write(
            lambda s: s.add(NotificationRow(
                priority=notification.priority.value,
                title=notification.title[:300], body=notification.body,
                listing_id=notification.listing_id,
                payload=notification.payload or None,
                delivered_channels=["database"])),
            swallow=True, label="notification")
        return True
