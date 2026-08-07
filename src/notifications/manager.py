"""Notification manager — fans a Notification out to all configured channels.
Console + database are always on so alerts are never silently lost."""

from __future__ import annotations

from functools import lru_cache

from ..config.settings import get_settings
from ..utils.logging import get_logger
from .base import Notification, NotificationProvider, Priority
from .console import ConsoleProvider
from .database import DatabaseProvider
from .discord import DiscordProvider
from .email import EmailProvider
from .telegram import TelegramProvider

log = get_logger("notify.manager")

_REGISTRY = {
    "console": ConsoleProvider,
    "database": DatabaseProvider,
    "telegram": TelegramProvider,
    "discord": DiscordProvider,
    "email": EmailProvider,
}


class NotificationManager:
    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self.providers: list[NotificationProvider] = []
        wanted = set(self.settings.notify_channel_list) | {"console", "database"}
        for name in wanted:
            cls = _REGISTRY.get(name)
            if not cls:
                continue
            try:
                provider = cls() if name in ("console", "database") else cls(self.settings)
            except Exception as exc:  # pragma: no cover
                log.warning("provider %s init failed: %s", name, exc)
                continue
            if provider.enabled:
                self.providers.append(provider)
            elif name not in ("console", "database"):
                log.info("notification channel '%s' requested but not configured", name)

    def dispatch(self, notification: Notification) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for p in self.providers:
            try:
                results[p.name] = p.send(notification)
            except Exception as exc:  # pragma: no cover
                log.warning("provider %s send raised: %s", p.name, exc)
                results[p.name] = False
        return results


@lru_cache(maxsize=1)
def get_manager() -> NotificationManager:
    return NotificationManager()
