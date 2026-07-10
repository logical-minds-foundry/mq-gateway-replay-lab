"""A full trading-day simulation → the end-of-day dashboard story.

Drives a realistic day of trades through the gateway and stalls the
counterparty's confirmations for a window, so the observability board shows a
*troubled* day: the unconfirmed backlog climbing during the outage, the aged set,
the shifted confirm latency, and exactly which trades are outstanding.

The orchestration (``run_day``) is shared by two callers:

- **offline** — a ``FakeMessaging`` with an advancing ``SimClock`` makes it a
  deterministic unit test (no real time, no queue manager);
- **live** — ``demo/full_day_sim.py`` runs the same orchestration against a real
  queue manager with real sleeps, producing the snapshot for the proof-of-concept
  documentation while Prometheus scrapes the board.

During a *confirm* phase the counterparty replies to every request. During the
*outage* phase it **consumes but does not confirm** — the trade is archived on the
outbound side with no matching inbound confirmation, which is precisely the
unconfirmed condition the reconciliation surfaces. The outage backlog is left
outstanding at end of day: that is the story the snapshot tells.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab import inbound, outbound

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta

    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging
    from gwlab.responder import Responder


@dataclass(frozen=True)
class Phase:
    """One stretch of the day: ``trades`` requests, confirmed or not.

    ``confirm=False`` is the confirmation outage — the counterparty receives the
    requests but its confirmations never come back.
    """

    label: str
    trades: int
    confirm: bool


# A steady open, a busier midday, a confirmation outage, then recovery. Volumes
# are shape-only (the live run scales them); the outage is what the board shows.
TROUBLED_DAY: tuple[Phase, ...] = (
    Phase("open", 12, confirm=True),
    Phase("midday", 24, confirm=True),
    Phase("outage", 18, confirm=False),
    Phase("recovery", 16, confirm=True),
)


class SimClock:
    """A movable clock for the offline run — call it for ``now``, advance it by hand."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def _msgid(seq: int) -> bytes:
    """A 24-byte MsgId (MQ's width) derived from the trade sequence."""
    return seq.to_bytes(24, "big")


def _drain_confirmed(messaging: Messaging, queues: QueueSet, responder: Responder) -> None:
    """The counterparty confirms everything outstanding, and the gateway consumes it."""
    while responder.process_one() is not None:
        pass
    while inbound.receive(messaging, queues) is not None:
        pass


def _drain_outage(messaging: Messaging, queues: QueueSet) -> None:
    """The counterparty receives the requests but sends no confirmation (the outage)."""
    while messaging.get(queues.send) is not None:
        messaging.commit()


def run_day(
    messaging: Messaging,
    queues: QueueSet,
    responder: Responder,
    phases: tuple[Phase, ...],
    *,
    after_send: Callable[[Phase], None] | None = None,
    after_phase: Callable[[Phase], None] | None = None,
) -> int:
    """Run the scenario, returning the number of trades sent.

    ``after_send`` / ``after_phase`` are timing hooks: offline they advance the
    ``SimClock`` (so trades and confirmations get spread-out put-times); live they
    sleep real time. Both default to no-ops.
    """
    seq = 0
    for phase in phases:
        for _ in range(phase.trades):
            outbound.send(messaging, queues, f"trade-{seq:06d}".encode(), _msgid(seq))
            seq += 1
            if after_send is not None:
                after_send(phase)
        if phase.confirm:
            _drain_confirmed(messaging, queues, responder)
        else:
            _drain_outage(messaging, queues)
        if after_phase is not None:
            after_phase(phase)
    return seq
