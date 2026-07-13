"""Reconciliation: the unconfirmed set, from browsing the audit queues only.

This is the authoritative "what didn't come back" computation. It runs entirely
against the outbound and inbound **audit** queues — never the live transmit or
reply queues — so it never contends with the gateway's live throughput. An
outbound message is *unconfirmed* when its MsgId has no matching reply CorrelId
(the counterparty echoes the request MsgId back as the reply CorrelId).

Two layers sit here:

- The **raw** computation (``unconfirmed`` / ``metrics``) matches sent against
  confirmed with no notion of time — the binding-agnostic core.
- The **current-trading-day** layer (``report`` and its ``daily_metrics`` /
  ``unconfirmed_detail`` views) is what the observability tier consumes: it
  scopes to *today* (any message or reply stamped on a prior calendar day is
  ignored — this proof-of-concept shows the day's trading and recon; what to do
  with older messages is a follow-on), and it carries each unconfirmed message's
  sent-time, age, and state, plus how many headers the scan examined. The clock
  (``now``) is injected, never read internally, so the view is deterministic and
  testable.

``report`` scans each audit queue exactly once. ``scanned_headers`` — the number
of message headers examined — is deliberately first-class: it is the size of the
work, so pairing it with the wall-clock scan duration (measured by the exporter)
is how we quantify the cost of reconciliation as the audit queue grows through
the day, and reason about the full-audit-queue worst case rather than guess at
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime

    from gwlab.config import QueueSet
    from gwlab.messaging import Message, Messaging

# Age thresholds that bucket an unconfirmed message into a state. Chosen to match
# the observability mockup: a fresh message is within SLA, one waiting a while is
# awaiting reply, and one past the aged threshold is escalated.
_AWAITING_AFTER = timedelta(minutes=10)
_AGED_AFTER = timedelta(minutes=30)

STATE_WITHIN_SLA = "within-sla"
STATE_AWAITING = "awaiting-reply"
STATE_AGED = "aged"


@dataclass(frozen=True)
class Unconfirmed:
    """An outbound message with no matching inbound confirmation."""

    msg_id: bytes
    body: bytes


@dataclass(frozen=True)
class Metrics:
    """Point-in-time counts for the observability tier."""

    sent: int
    confirmed: int
    unconfirmed: int


@dataclass(frozen=True)
class UnconfirmedDetail:
    """An unconfirmed message with the fields the *which-trades* table shows."""

    msg_id: bytes
    sent_at: datetime
    age: timedelta
    state: str


@dataclass(frozen=True)
class DailyMetrics:
    """Current-trading-day aggregates for the KPI tiles and hero chart."""

    sent: int
    confirmed: int
    unconfirmed: int
    aged: int
    mean_latency_seconds: float
    scanned_headers: int


@dataclass(frozen=True)
class DailyReport:
    """One current-day reconciliation pass: aggregates plus the unconfirmed set."""

    metrics: DailyMetrics
    unconfirmed: list[UnconfirmedDetail]


def _split(messaging: Messaging, queues: QueueSet) -> tuple[list[Unconfirmed], int]:
    sent = messaging.browse(queues.send_audit)
    replies = messaging.browse(queues.reply_audit)
    confirmed_ids = {reply.correl_id for reply in replies}
    unmatched = [
        Unconfirmed(msg_id=message.msg_id, body=message.body)
        for message in sent
        if message.msg_id not in confirmed_ids
    ]
    return unmatched, len(sent)


def unconfirmed(messaging: Messaging, queues: QueueSet) -> list[Unconfirmed]:
    """Return the outbound messages with no matching confirmation."""
    unmatched, _ = _split(messaging, queues)
    return unmatched


def metrics(messaging: Messaging, queues: QueueSet) -> Metrics:
    """Return sent / confirmed / unconfirmed counts for the dashboard."""
    unmatched, total = _split(messaging, queues)
    return Metrics(sent=total, confirmed=total - len(unmatched), unconfirmed=len(unmatched))


def state_for(age: timedelta) -> str:
    """Bucket an unconfirmed message's age into a display state."""
    if age >= _AGED_AFTER:
        return STATE_AGED
    if age >= _AWAITING_AFTER:
        return STATE_AWAITING
    return STATE_WITHIN_SLA


def _on_day(messages: list[Message], day: date) -> list[tuple[Message, datetime]]:
    """Keep only messages stamped on ``day``, paired with their put-time.

    A message with no put-time cannot be dated; that is a data-integrity fault in
    the audit feed, so we fail loudly rather than silently drop it.
    """
    kept: list[tuple[Message, datetime]] = []
    for message in messages:
        put_time = message.put_time
        if put_time is None:
            error = f"audited message {message.msg_id!r} has no put_time; cannot date-scope it"
            raise ValueError(error)
        if put_time.date() == day:
            kept.append((message, put_time))
    return kept


def report(messaging: Messaging, queues: QueueSet, now: datetime) -> DailyReport:
    """Scan both audit queues once and return today's aggregates and unconfirmed set."""
    day = now.date()
    sent = _on_day(messaging.browse(queues.send_audit), day)
    replies = _on_day(messaging.browse(queues.reply_audit), day)
    reply_time_by_key = {message.correl_id: put_time for message, put_time in replies}

    detail = [
        UnconfirmedDetail(message.msg_id, put_time, now - put_time, state_for(now - put_time))
        for message, put_time in sent
        if message.msg_id not in reply_time_by_key
    ]
    latencies = [
        (reply_time_by_key[message.msg_id] - put_time).total_seconds()
        for message, put_time in sent
        if message.msg_id in reply_time_by_key
    ]
    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    aged = sum(1 for item in detail if item.state == STATE_AGED)

    daily = DailyMetrics(
        sent=len(sent),
        confirmed=len(sent) - len(detail),
        unconfirmed=len(detail),
        aged=aged,
        mean_latency_seconds=mean_latency,
        scanned_headers=len(sent) + len(replies),
    )
    return DailyReport(metrics=daily, unconfirmed=detail)


def unconfirmed_detail(
    messaging: Messaging, queues: QueueSet, now: datetime
) -> list[UnconfirmedDetail]:
    """Return today's unconfirmed messages, each with sent-time, age, and state."""
    return report(messaging, queues, now).unconfirmed


def daily_metrics(messaging: Messaging, queues: QueueSet, now: datetime) -> DailyMetrics:
    """Return today's KPI aggregates: counts, aged count, mean latency, scan size."""
    return report(messaging, queues, now).metrics
