"""Prometheus text exposition of the reconciliation metrics.

This is the feed behind the observability panels (the extension to the
messaging-flow board), scoped to the **current trading day** (prior-day messages
are ignored — see ``reconcile``). Three shapes are exposed:

- **Aggregate gauges**, labelled by counterparty, for the KPI tiles and the
  backlog/rate charts: sent, confirmed, unconfirmed, the aged-unconfirmed count,
  and mean confirm latency.
- **Scan-cost gauges**: how many headers the reconciliation pass examined
  (``gwlab_reconcile_scanned_headers``) and how long it took
  (``gwlab_reconcile_scan_seconds``). The audit queue grows monotonically
  through the day, so these let us *chart the cost of reconciliation as it
  scales* and reason about the full-audit-queue worst case instead of guessing.
- A **per-message info-metric** (``gwlab_unconfirmed_message``) — one series per
  unconfirmed message, its value the age in seconds and its labels identifying
  *which* trade (MsgId), when it was sent, and its state. This powers the
  *which-trades, not just how many* table; the unconfirmed set is small
  (hundreds), so one series each is fine.

Serving this over HTTP and registering it with the lab's metrics tier is live
wiring done on the box; the exposition itself is rendered and tested here.
"""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from gwlab.reconcile import report

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging

_GAUGE_HELP = {
    "gwlab_sent": "Messages sent so far today (current-day outbound audit count).",
    "gwlab_confirmed": "Messages confirmed so far today.",
    "gwlab_unconfirmed": "Messages sent today but not yet confirmed (the backlog).",
    "gwlab_unconfirmed_aged": "Unconfirmed messages older than the aged threshold.",
    "gwlab_confirm_latency_seconds": "Mean time from send to confirmation, today, in seconds.",
    "gwlab_reconcile_scanned_headers": "Message headers examined by the last reconciliation scan.",
    "gwlab_reconcile_scan_seconds": "Wall-clock duration of the last reconciliation scan.",
}

_MESSAGE_METRIC = "gwlab_unconfirmed_message"
_MESSAGE_HELP = (
    "Age in seconds of one unconfirmed message; labels identify which trade "
    "(msgid), when it was sent, and its state."
)


def render(
    messaging: Messaging,
    queues: QueueSet,
    counterparty: str,
    now: datetime,
    timer: Callable[[], float] = perf_counter,
) -> str:
    """Render the current-day reconciliation metrics as Prometheus text exposition.

    ``timer`` is the monotonic clock used to measure the scan duration; it is
    injectable so the exposition is deterministic under test.
    """
    start = timer()
    daily_report = report(messaging, queues, now)
    scan_seconds = timer() - start

    daily = daily_report.metrics
    gauges = (
        ("gwlab_sent", daily.sent),
        ("gwlab_confirmed", daily.confirmed),
        ("gwlab_unconfirmed", daily.unconfirmed),
        ("gwlab_unconfirmed_aged", daily.aged),
        ("gwlab_confirm_latency_seconds", daily.mean_latency_seconds),
        ("gwlab_reconcile_scanned_headers", daily.scanned_headers),
        ("gwlab_reconcile_scan_seconds", scan_seconds),
    )

    lines: list[str] = []
    for name, value in gauges:
        lines.append(f"# HELP {name} {_GAUGE_HELP[name]}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f'{name}{{counterparty="{counterparty}"}} {value}')

    lines.append(f"# HELP {_MESSAGE_METRIC} {_MESSAGE_HELP}")
    lines.append(f"# TYPE {_MESSAGE_METRIC} gauge")
    for item in daily_report.unconfirmed:
        sent = item.sent_at.strftime("%H:%M:%S")
        msgid = item.msg_id.hex()
        age_seconds = f"{item.age.total_seconds():.0f}"
        labels = f'counterparty="{counterparty}",msgid="{msgid}",sent="{sent}",state="{item.state}"'
        lines.append(f"{_MESSAGE_METRIC}{{{labels}}} {age_seconds}")

    return "\n".join(lines) + "\n"
