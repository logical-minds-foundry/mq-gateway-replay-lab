"""Tests for the persistent-cursor ``browse_new`` on the ``Messaging`` port.

Exercised through the in-memory :class:`FakeMessaging`: the first call returns
every message from the first, each later call returns only messages that arrived
since the previous call, and a browsed message is never returned twice — the
continuous-cursor semantics the streaming collector's sweep depends on.
"""

from __future__ import annotations

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message


def _arrive(fake: FakeMessaging, queue: str, *bodies: bytes) -> None:
    """Commit ``bodies`` onto ``queue`` — the audit copies a sweep would browse."""
    for body in bodies:
        fake.put(queue, Message(body=body))
    fake.commit()


def test_browse_new_returns_only_new_since_last_call() -> None:
    fake = FakeMessaging()
    _arrive(fake, "Q", b"a", b"b")

    first = fake.browse_new("Q")
    assert [m.body for m in first] == [b"a", b"b"]

    # Nothing has arrived since — the cursor stays put.
    assert fake.browse_new("Q") == []

    # A new arrival is returned exactly once, with no re-read of a/b.
    _arrive(fake, "Q", b"c")
    assert [m.body for m in fake.browse_new("Q")] == [b"c"]
    assert fake.browse_new("Q") == []


def test_browse_new_first_call_on_empty_queue_returns_empty() -> None:
    fake = FakeMessaging()

    # A never-seen queue starts with an implicit cursor at the first message.
    assert fake.browse_new("NOPE") == []

    _arrive(fake, "NOPE", b"x")
    assert [m.body for m in fake.browse_new("NOPE")] == [b"x"]


def test_browse_new_cursor_is_per_queue() -> None:
    fake = FakeMessaging()
    _arrive(fake, "A", b"a1")
    _arrive(fake, "B", b"b1")

    assert [m.body for m in fake.browse_new("A")] == [b"a1"]
    # B's cursor is independent — advancing A did not move it.
    assert [m.body for m in fake.browse_new("B")] == [b"b1"]
    assert fake.browse_new("A") == []
