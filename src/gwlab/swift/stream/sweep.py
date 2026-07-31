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

import time
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

#: Yield the GIL every this many folded messages. The browse (MQGET) already
#: releases the GIL, but the pure-Python decode/fold does not, so a very large
#: batch would starve the box-only HTTP thread and time out scrapes (issue #152).
#: A bare ``time.sleep(0)`` hands the interpreter to any waiting thread at
#: negligible throughput cost.
_YIELD_EVERY = 5000


@dataclass(frozen=True)
class SweepResult:
    """The outcome of one sweep: how many messages this pass browsed and folded.

    ``ingested`` counts every message drawn from the cursors this pass — including
    malformed ones, which are counted anomalies, not skipped reads — so it is the
    per-cycle backlog the self-metrics gauge (design §7).

    ``more`` is ``True`` when this was a bounded pass (``batch > 0``) and at least
    one queue returned a full ``batch``, i.e. its cursor likely has more to drain;
    the caller keeps sweeping without pausing until ``more`` is ``False`` (caught
    up). An unbounded pass (``batch == 0``) always drains fully, so ``more`` is
    ``False``.
    """

    ingested: int
    more: bool = False


def sweep(
    messaging: Messaging,
    state: SettlementState,
    queues: SwiftQueueSet,
    now: datetime,
    horizon: Horizon,
    batch: int = 0,
) -> SweepResult:
    """Drain each audit queue once, fold new messages into ``state``, then age it.

    Round-robins the queues in :data:`_QUEUE_ORDER` via
    :meth:`~gwlab.messaging.Messaging.browse_new`. Each browsed message is decoded
    with :func:`gwlab.swift.collect._decode_and_parse`; its anomalies are recorded
    (a malformed body never aborts the pass), and an in-window parse is ingested.

    ``batch > 0`` bounds the pass to at most ``batch`` messages **per queue**
    (passed as ``limit`` to :meth:`~gwlab.messaging.Messaging.browse_new`), so no
    single sweep folds a giant delta; the returned :class:`SweepResult` flags
    ``more`` when any queue filled its batch. The instruct-before-status order is
    preserved within the call, so the correlation invariant still holds:
    instructions are drained before the responses that reference them (design §5).
    Every :data:`_YIELD_EVERY` folded messages the loop yields the GIL so the
    box-only HTTP thread can serve mid-batch (issue #152).
    """
    ingested = 0
    more = False
    for attr in _QUEUE_ORDER:
        queue = getattr(queues, attr)
        drained = 0
        for message in messaging.browse_new(queue, limit=batch):
            ingested += 1
            drained += 1
            if ingested % _YIELD_EVERY == 0:
                time.sleep(0)  # cooperative GIL hand-off; see _YIELD_EVERY
            parsed, anomalies = _decode_and_parse(message, queue)
            state.record_anomalies(anomalies)
            if parsed is not None and window.in_window(parsed.put_time, now, horizon):
                state.ingest(parsed)
        if batch > 0 and drained == batch:
            more = True
    state.age(now, horizon)
    return SweepResult(ingested=ingested, more=more)
