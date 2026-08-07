"""Discord webhook notification provider (optional, via env var)."""

from __future__ import annotations

import httpx

from ..config.settings import get_settings
from ..utils.logging import get_logger
from .base import Notification, NotificationProvider

log = get_logger("notify.discord")


class DiscordProvider(NotificationProvider):
    name = "discord"

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.discord_webhook_url)

    def send(self, notification: Notification) -> bool:
        if not self.enabled:
            return False
        content = f"**{notification.title}**\n```\n{notification.body[:1800]}\n```"
        try:
            r = httpx.post(self.settings.discord_webhook_url,
                           json={"content": content}, timeout=15)
            return r.status_code in (200, 204)
        except httpx.HTTPError as exc:
            log.warning("discord send failed: %s", exc)
            return False
