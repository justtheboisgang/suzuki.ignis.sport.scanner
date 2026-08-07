"""Notification providers. Console + database work with zero credentials;
Telegram / Discord / email activate via env vars."""

from .base import Notification, Priority, NotificationProvider
from .manager import NotificationManager, get_manager

__all__ = [
    "Notification",
    "Priority",
    "NotificationProvider",
    "NotificationManager",
    "get_manager",
]
