"""Telegram notification provider (optional, via env vars)."""

from __future__ import annotations

import httpx

from ..config.settings import get_settings
from ..utils.logging import get_logger
from .base import Notification, NotificationProvider

log = get_logger("notify.telegram")


class TelegramProvider(NotificationProvider):
    name = "telegram"

    def __init__(self, settings=None):
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_bot_token and self.settings.telegram_chat_id)

    def send(self, notification: Notification) -> bool:
        if not self.enabled:
            return False
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        text = f"*{notification.title}*\n```\n{notification.body}\n```"
        try:
            r = httpx.post(url, json={
                "chat_id": self.settings.telegram_chat_id,
                "text": text[:4000],
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            }, timeout=15)
            return r.status_code == 200
        except httpx.HTTPError as exc:
            log.warning("telegram send failed: %s", exc)
            return False
