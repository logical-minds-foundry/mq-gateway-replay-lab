"""A single min-heap event scheduler — the responder's scaling core (§6).

Thread-per-request (a thread that sleeps between an instruction's stages) does not
scale to 100k. Instead the responder computes each instruction's whole lifecycle
plan up front (:mod:`gwlab.swift.responder_app`) as a small ordered list of timed
:class:`Event`\\ s and pushes them all onto **one** shared min-heap keyed by
``emit_at``. A single loop then pops the due events and PUTs each response. So it
is *one scheduler + N events* — a heap push/pop per event, not N threads — which
scales to millions of events.

The heap orders on ``(emit_at, counter)``: a monotonically-increasing tiebreaker
``counter`` makes every heap key unique, so two events with the **same** ``emit_at``
break the tie on insertion order and the :class:`Event` payloads are **never**
compared (they carry no ordering). Time is injected — :meth:`Scheduler.pop_due`
takes an explicit ``now`` — so the scheduler is fully deterministic and testable
without touching the wall clock.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class Event:
    """One response to emit at a scheduled time: ``body`` PUT to ``queue`` at ``emit_at``."""

    emit_at: datetime
    queue: str
    body: str


class Scheduler:
    """A min-heap of pending :class:`Event`\\ s, ordered by ``emit_at``.

    :meth:`push` adds an event; :meth:`pop_due` removes and returns every event
    whose ``emit_at`` is at or before an injected ``now``, in time order (ties
    broken by insertion order). Deterministic: the same pushes and the same
    ``now`` always yield the same result.
    """

    def __init__(self) -> None:
        self._heap: list[tuple[datetime, int, Event]] = []
        self._counter = 0

    def push(self, event: Event) -> None:
        """Schedule ``event``, tie-breaking equal ``emit_at`` by insertion order."""
        heapq.heappush(self._heap, (event.emit_at, self._counter, event))
        self._counter += 1

    def pop_due(self, now: datetime) -> list[Event]:
        """Pop and return every event with ``emit_at <= now``, earliest first.

        Not-yet-due events stay on the heap for a later, larger ``now``.
        """
        due: list[Event] = []
        while self._heap and self._heap[0][0] <= now:
            due.append(heapq.heappop(self._heap)[2])
        return due
