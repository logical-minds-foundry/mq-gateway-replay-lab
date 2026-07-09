"""Prometheus text exposition of the reconciliation metrics.

This is the feed behind the observability panels (the extension to the
messaging-flow board). All three metrics are **gauges** — intraday counts that
grow through the day and reset at start-of-day — so the dashboard plots them raw
as time series (the backlog-over-the-day, sent-vs-confirmed). They are labelled
by counterparty so one board can serve many streams (one page per counterparty).

Serving this over HTTP and registering it with the lab's metrics tier is live
wiring done on the box; the exposition itself is rendered and tested here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.reconcile import metrics

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging

_METRICS = (
    ("gwlab_sent", "Messages sent so far today (outbound archive count).", "sent"),
    ("gwlab_confirmed", "Messages confirmed so far today.", "confirmed"),
    ("gwlab_unconfirmed", "Messages sent but not yet confirmed (the backlog).", "unconfirmed"),
)


def render(messaging: Messaging, queues: QueueSet, counterparty: str) -> str:
    """Render the reconciliation metrics as Prometheus text exposition."""
    current = metrics(messaging, queues)
    values = {
        "sent": current.sent,
        "confirmed": current.confirmed,
        "unconfirmed": current.unconfirmed,
    }
    lines: list[str] = []
    for name, help_text, field in _METRICS:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f'{name}{{counterparty="{counterparty}"}} {values[field]}')
    return "\n".join(lines) + "\n"
