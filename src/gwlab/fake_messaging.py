"""In-memory ``Messaging`` for offline runs and tests (no queue manager needed).

Committed state lives in ``_committed``. Puts and destructive gets within a unit
of work are staged and only applied on ``commit``; ``backout`` discards them —
so the two-put-in-one-UOW and get-and-copy semantics the design depends on can
be exercised without a live box.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from gwlab.messaging import Message


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

    def configure_stream(self, source: str, target: str) -> None:
        """Model ``STREAMQ(target) STRMQOS(MUSTDUP)`` on ``source``.

        Every put to ``source`` also stages a copy to ``target`` in the **same**
        unit of work (both commit or neither), preserving the MsgId — the
        queue-manager-native audit copy, produced with no application logic.
        """
        self._streams[source] = target

    def put(self, queue: str, message: Message) -> None:
        if not message.msg_id:
            # Mirror the queue manager minting a globally-unique MsgId (the live
            # path uses MQPMO_NEW_MSG_ID); a synthetic-but-unique id, never zero.
            self._next_msgid += 1
            message.msg_id = b"FAKE" + self._next_msgid.to_bytes(20, "big")
        if self._clock is not None and message.put_time is None:
            message.put_time = self._clock()
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

    def browse_new(self, queue: str) -> list[Message]:
        """Persistent-cursor browse (see the ``Messaging`` protocol).

        A per-queue high-water index models the live adapter's parked browse
        cursor: the first call returns every committed message, each later call
        only those committed since — never re-reading a message. Uncommitted
        staged puts are invisible (they are not yet on the queue), exactly as a
        live browse would not see an in-flight, uncommitted put.
        """
        committed = self._committed.get(queue, [])
        start = self._browse_cursor.get(queue, 0)
        self._browse_cursor[queue] = len(committed)
        return committed[start:]

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
