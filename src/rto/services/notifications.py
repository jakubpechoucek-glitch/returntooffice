"""Notification port.

The prototype only logs notifications. For production, implement ``Notifier`` with e-mail
(Microsoft Graph ``sendMail`` / SMTP relay) or Microsoft Teams (Graph chat / incoming webhook) and
register it with :func:`set_notifier`. Delivery should go through a queue so that a slow mail server
never blocks an approval.
"""

from __future__ import annotations

import logging
from typing import Protocol

log = logging.getLogger("rto.notifications")


class Notifier(Protocol):
    def send(self, recipient: str, subject: str, body: str = "") -> None: ...


class LogNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, recipient: str, subject: str, body: str = "") -> None:
        self.sent.append((recipient, subject))
        log.info("notification to=%s subject=%s", recipient, subject)


_notifier: Notifier = LogNotifier()


def set_notifier(n: Notifier) -> None:
    global _notifier
    _notifier = n


def get_notifier() -> Notifier:
    return _notifier


def notify(recipient: str | None, subject: str, body: str = "") -> None:
    if not recipient:
        return
    try:
        _notifier.send(recipient, subject, body)
    except Exception:  # notifications must never break a business transaction
        log.exception("notification failed")
