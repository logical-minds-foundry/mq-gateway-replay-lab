"""In-memory ``Messaging`` for offline runs and tests (no queue manager needed).

Committed state lives in ``_committed``. Puts and destructive gets within a unit
of work are staged and only applied on ``commit``; ``backout`` discards them —
so the two-put-in-one-UOW and get-and-copy semantics the design depends on can
be exercised without a live box.
"""

from __future__ import annotations

from collections import namedtuple
from typing import TYPE_CHECKING

from gwlab.messaging import Message

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from gwlab.messaging import ReportOpts

#: A record of one ``put`` call, kept so tests can assert what the gateway logic
#: requested — the target ``queue``, the ``message``, and any ``report_opts``
#: (the COA/COD request bits) attached to it.
PutRecord = namedtuple("PutRecord", "queue message report_opts")


class FakeMessaging:
    """A single-connection, syncpoint-aware, in-memory queue set.

    ``clock`` models the queue manager stamping a put-time (MQMD PutDate/PutTime):
    when set, a put stamps any message that has none, so offline reconciliation and
    the day simulation see dated messages exactly as the live path does.
    """

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._committed: dict[str, list[Message]] = {}
        self._staged_puts: list[tuple[str, Message]] = []
        self._staged_gets: list[tuple[str, Message]] = []
        self._streams: dict[str, str] = {}
        self._clock = clock
        self._next_msgid = 0
        self._browse_cursor: dict[str, int] = {}
        self.puts: list[PutRecord] = []

    def configure_stream(self, source: str, target: str) -> None:
        """Model ``STREAMQ(target) STRMQOS(MUSTDUP)`` on ``source``.

        Every put to ``source`` also stages a copy to ``target`` in the **same**
        unit of work (both commit or neither), preserving the MsgId — the
        queue-manager-native audit copy, produced with no application logic.
        """
        self._streams[source] = target

    def put(self, queue: str, message: Message, *, report_opts: ReportOpts | None = None) -> None:
        if not message.msg_id:
            # Mirror the queue manager minting a globally-unique MsgId (the live
            # path uses MQPMO_NEW_MSG_ID); a synthetic-but-unique id, never zero.
            self._next_msgid += 1
            message.msg_id = b"FAKE" + self._next_msgid.to_bytes(20, "big")
        if self._clock is not None and message.put_time is None:
            message.put_time = self._clock()
        # Record the request (with any COA/COD report options) for test inspection;
        # the offline fake has no queue manager to emit the reports, so the options
        # are captured rather than acted on (spec §10 drives reports via
        # ``enqueue_report`` instead).
        self.puts.append(PutRecord(queue, message, report_opts))
        self._staged_puts.append((queue, message))
        target = self._streams.get(queue)
        if target is not None:
            self._staged_puts.append((target, message))

    def get(self, queue: str) -> Message | None:
        taken = [m for q, m in self._staged_gets if q == queue]
        available = [m for m in self._committed.get(queue, []) if m not in taken]
        if not available:
            return None
        message = available[0]
        self._staged_gets.append((queue, message))
        return message

    def browse(self, queue: str) -> list[Message]:
        return list(self._committed.get(queue, []))

    def browse_new(self, queue: str, limit: int = 0) -> list[Message]:
        """Persistent-cursor browse (see the ``Messaging`` protocol).

        A per-queue high-water index models the live adapter's parked browse
        cursor: the first call returns every committed message, each later call
        only those committed since — never re-reading a message. Uncommitted
        staged puts are invisible (they are not yet on the queue), exactly as a
        live browse would not see an in-flight, uncommitted put. ``limit > 0``
        caps the call at that many new messages and advances the cursor only past
        those returned, so the next call continues from there (the bounded-batch
        drain of issue #152); ``limit == 0`` returns the whole new delta.
        """
        committed = self._committed.get(queue, [])
        start = self._browse_cursor.get(queue, 0)
        end = len(committed)
        if limit > 0:
            end = min(end, start + limit)
        self._browse_cursor[queue] = end
        return committed[start:end]

    def commit(self) -> None:
        for queue, message in self._staged_gets:
            self._committed[queue].remove(message)
        for queue, message in self._staged_puts:
            self._committed.setdefault(queue, []).append(message)
        self._staged_puts.clear()
        self._staged_gets.clear()

    def backout(self) -> None:
        self._staged_puts.clear()
        self._staged_gets.clear()

    def clear(self, queue: str) -> int:
        removed = len(self._committed.get(queue, []))
        self._committed[queue] = []
        return removed

    def enqueue_report(
        self, queue: str, correl_id: bytes, feedback: int, put_time: datetime
    ) -> Message:
        """Land a body-less COA/COD report on ``queue``, served over ``browse_new``.

        Models the counterparty queue manager returning a report (spec §10): a
        message with an empty body, ``correl_id`` set to the instruction's MsgId
        (``MQRO_COPY_MSG_ID_TO_CORREL_ID``), the ``feedback`` code that names the
        milestone (``MQFB_COA``/``MQFB_COD``), and a controllable ``put_time``. It
        is placed directly on the committed queue — a report arrives already on the
        queue, not through the app's unit of work — so the next ``browse_new`` (or
        ``browse``) sees it. Returns the enqueued message for convenience.
        """
        message = Message(body=b"", correl_id=correl_id, feedback=feedback, put_time=put_time)
        self._committed.setdefault(queue, []).append(message)
        return message
