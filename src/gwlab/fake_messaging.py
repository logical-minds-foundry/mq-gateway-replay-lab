"""In-memory ``Messaging`` for offline runs and tests (no queue manager needed).

Committed state lives in ``_committed``. Puts and destructive gets within a unit
of work are staged and only applied on ``commit``; ``backout`` discards them —
so the two-put-in-one-UOW and get-and-copy semantics the design depends on can
be exercised without a live box.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gwlab.messaging import Message


class FakeMessaging:
    """A single-connection, syncpoint-aware, in-memory queue set."""

    def __init__(self) -> None:
        self._committed: dict[str, list[Message]] = {}
        self._staged_puts: list[tuple[str, Message]] = []
        self._staged_gets: list[tuple[str, Message]] = []

    def put(self, queue: str, message: Message) -> None:
        self._staged_puts.append((queue, message))

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
