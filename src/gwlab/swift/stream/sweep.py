"""One round-robin ingest pass over the audit queues (design §4).

:func:`sweep` is the streaming collector's MQ boundary. It drains each audit
queue **in order** — instructions first, then responses (and, with COA/COD, the
report queue) — behind a persistent browse cursor
(:meth:`gwlab.messaging.Messaging.browse_new`), folding each browsed message into
the maintained :class:`~gwlab.swift.stream.state.SettlementState` as it is seen.
There is no distinct cold-load phase: the first lap rebuilds from empty and every
later lap returns only new arrivals, because the cursor never re-reads a message
(design §4/§5). The caller loops :func:`sweep`; the fold happens here, once per
message.

Two constraints make this correct and are load-bearing, not stylistic:

- **Sweep order** — instructions before responses — so a response's instruction
  is already in state when the response is folded (the correlation invariant, §5).
- **Fold-on-first-sight** — a browsed message is never re-browsed, so each is
  decoded and ingested the moment it appears.

Decoding reuses :func:`gwlab.swift.collect._decode_and_parse`, so the streaming
path carries the *same* four-kind unified anomaly channel as the batch collector:
a malformed body becomes a counted ``malformed`` anomaly handed to
:meth:`~gwlab.swift.stream.state.SettlementState.record_anomalies` — it never
aborts the sweep (no silent failures). After every queue is drained, the picture
is aged against the horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift import window
from gwlab.swift.collect import _decode_and_parse

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.messaging import Messaging
    from gwlab.swift.stream.state import SettlementState
    from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

#: The audit queues to drain, in fold order. ``report_audit`` (COA/COD) joins the
#: tuple when that queue lands; the names are :class:`SwiftQueueSet` attributes.
_QUEUE_ORDER: tuple[str, ...] = ("instruct_audit", "status_audit")


@dataclass(frozen=True)
class SweepResult:
    """The outcome of one sweep: how many messages this pass browsed and folded.

    ``ingested`` counts every message drawn from the cursors this pass — including
    malformed ones, which are counted anomalies, not skipped reads — so it is the
    per-cycle backlog the self-metrics gauge (design §7).
    """

    ingested: int


def sweep(
    messaging: Messaging,
    state: SettlementState,
    queues: SwiftQueueSet,
    now: datetime,
    horizon: Horizon,
) -> SweepResult:
    """Drain each audit queue once, fold new messages into ``state``, then age it.

    Round-robins the queues in :data:`_QUEUE_ORDER` via
    :meth:`~gwlab.messaging.Messaging.browse_new`. Each browsed message is decoded
    with :func:`gwlab.swift.collect._decode_and_parse`; its anomalies are recorded
    (a malformed body never aborts the pass), and an in-window parse is ingested.
    """
    ingested = 0
    for attr in _QUEUE_ORDER:
        queue = getattr(queues, attr)
        for message in messaging.browse_new(queue):
            ingested += 1
            parsed, anomalies = _decode_and_parse(message, queue)
            state.record_anomalies(anomalies)
            if parsed is not None and window.in_window(parsed.put_time, now, horizon):
                state.ingest(parsed)
    state.age(now, horizon)
    return SweepResult(ingested=ingested)
