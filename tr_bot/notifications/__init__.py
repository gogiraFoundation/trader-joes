"""Pluggable notifications (console, webhook) with throttling."""

from tr_bot.notifications import events
from tr_bot.notifications.facade import notify

__all__ = ["notify", "events"]
