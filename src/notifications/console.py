"""Console notification provider — always available, no credentials."""

from __future__ import annotations

from ..utils.logging import get_logger
from .base import Notification, NotificationProvider, Priority

log = get_logger("notify.console")


class ConsoleProvider(NotificationProvider):
    name = "console"

    def send(self, notification: Notification) -> bool:
        marker = {
            Priority.HOT: "🔥🔥🔥",
            Priority.HIGH: "🔥",
            Priority.NORMAL: "•",
            Priority.LOW: "·",
        }.get(notification.priority, "•")
        log.info("%s [%s] %s\n%s", marker, notification.priority.value.upper(),
                 notification.title, notification.body)
        return True
