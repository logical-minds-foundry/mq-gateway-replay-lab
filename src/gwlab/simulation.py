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
*outage* phase it **consumes but does not confirm** — the trade is audited on the
outbound side with no matching inbound confirmation, which is precisely the
unconfirmed condition the reconciliation surfaces. The outage backlog is left
outstanding at end of day: that is the story the snapshot tells.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab import inbound, outbound
from gwlab.messaging import Message

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

# The last hour of a trading day: steady, a slowdown, a ~10-minute block whose
# backlog recovers (unlike TROUBLED_DAY's outage, which is left stranded), then a
# small end-of-day unconfirmed tail. See ``run_last_hour`` (offline) and the live
# two-process pair (``demo/gateway_driver.py`` + ``demo/svc_responder.py``).
LAST_HOUR: tuple[Phase, ...] = (
    Phase("steady", 20, confirm=True),
    Phase("slowdown", 16, confirm=True),
    Phase("block", 10, confirm=False),
    Phase("recovery", 12, confirm=True),
)

# Wall-clock seconds each ``LAST_HOUR`` phase occupies in the *live* run. The block
# is the ~10-minute stoppage whose backlog climbs then drains, so the board shows
# both halves of the curve. The gateway mimic and the counterparty responder are
# separate processes (one queue-manager connection each, as in production); they
# share this schedule and a common start time, so no inter-process messaging is
# needed — each derives the current phase from the elapsed wall-clock.
LAST_HOUR_SECONDS: dict[str, int] = {
    "steady": 300,
    "slowdown": 420,
    "block": 600,
    "recovery": 360,
}


def phase_at(elapsed: float) -> Phase | None:
    """The ``LAST_HOUR`` phase active ``elapsed`` seconds after the shared start, or
    ``None`` once the scheduled phases are over (the run's tail/idle window).

    This is the single source of truth both live processes consult to stay in step
    without talking to each other: the responder withholds confirmations exactly
    when ``phase_at`` reports the non-confirming ``block``, and the gateway paces its
    sends by the same boundaries.
    """
    boundary = 0.0
    for phase in LAST_HOUR:
        boundary += LAST_HOUR_SECONDS[phase.label]
        if elapsed < boundary:
            return phase
    return None


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
    while messaging.get(queues.request) is not None:
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


def _inject_orphan_reply(messaging: Messaging, queues: QueueSet) -> None:
    """A confirmation whose CorrelId matches no sent MsgId — the should-never
    -happen anomaly (Algorithm 3). Put on the reply queue; the stream audits it
    on arrival, same as any other reply.
    """
    orphan = Message(body=b"orphan", msg_id=b"R:orphan", correl_id=b"\xffNOSENT")
    messaging.put(queues.reply, orphan)
    messaging.commit()


def run_last_hour(
    messaging: Messaging,
    queues: QueueSet,
    responder: Responder,
    *,
    after_send: Callable[[Phase], None] | None = None,
    after_phase: Callable[[Phase], None] | None = None,
    leave_unconfirmed: int = 3,
    inject_anomaly: bool = True,
) -> int:
    """The last hour of a trading day: steady, slowdown, a block, recovery, a
    small end-of-day unconfirmed tail, and one injected anomaly.

    Unlike ``run_day``'s ``outage`` phase (whose backlog is permanently
    stranded by ``_drain_outage``), this scenario's ``block`` backlog must
    *survive* into ``recovery`` so it gets confirmed there — the backlog climbs
    then drains, rather than being left outstanding. That means ``run_day``
    cannot be reused for the ``block`` phase, so ``LAST_HOUR`` is iterated
    locally here: during ``block`` requests are sent and audited but not
    drained at all; ``recovery``'s ``_drain_confirmed`` then processes both the
    block backlog and the recovery trades in one pass.

    The tail sends ``leave_unconfirmed`` trades that are audited but never
    confirmed — the end-of-day outstanding set — and, when ``inject_anomaly``
    is set, one orphan reply is injected as the should-never-happen anomaly.
    """
    seq = 0
    for phase in LAST_HOUR:
        for _ in range(phase.trades):
            outbound.send(messaging, queues, f"trade-{seq:06d}".encode(), _msgid(seq))
            seq += 1
            if after_send is not None:
                after_send(phase)
        if phase.confirm:
            _drain_confirmed(messaging, queues, responder)
        # else: the block phase — send only, no drain, so its backlog survives
        # into recovery instead of being stranded (contrast run_day's outage).
        if after_phase is not None:
            after_phase(phase)
    for _ in range(leave_unconfirmed):
        outbound.send(messaging, queues, f"trade-{seq:06d}".encode(), _msgid(seq))
        seq += 1
        if after_send is not None:
            after_send(LAST_HOUR[-1])
    if inject_anomaly:
        _inject_orphan_reply(messaging, queues)
    return seq
