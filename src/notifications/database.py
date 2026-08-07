"""Database notification provider — persists every alert so the dashboard has a
durable feed with no external service required."""

from __future__ import annotations

from ..database.base import session_scope
from ..models.feedback import Notification as NotificationRow
from ..utils.logging import get_logger
from .base import Notification, NotificationProvider

log = get_logger("notify.db")


class DatabaseProvider(NotificationProvider):
    name = "database"

    def send(self, notification: Notification) -> bool:
        try:
            with session_scope() as s:
                s.add(NotificationRow(
                    priority=notification.priority.value,
                    title=notification.title[:300],
                    body=notification.body,
                    listing_id=notification.listing_id,
                    payload=notification.payload or None,
                    delivered_channels=["database"],
                ))
            return True
        except Exception as exc:  # pragma: no cover
            log.warning("db notification failed: %s", exc)
            return False
