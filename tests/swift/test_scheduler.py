"""Tests for the responder's min-heap event scheduler (deterministic, injected now)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.scheduler import Event, Scheduler

_BASE = datetime(2026, 7, 29, 9, 0)


def _event(offset_seconds: int, body: str) -> Event:
    return Event(emit_at=_BASE + timedelta(seconds=offset_seconds), queue="GW.SEC.REPLY", body=body)


def test_pop_due_returns_events_in_time_order() -> None:
    scheduler = Scheduler()
    # Pushed out of order; must come back earliest-first.
    scheduler.push(_event(30, "third"))
    scheduler.push(_event(10, "first"))
    scheduler.push(_event(20, "second"))

    due = scheduler.pop_due(_BASE + timedelta(seconds=60))

    assert [e.body for e in due] == ["first", "second", "third"]


def test_pop_due_leaves_not_yet_due_events() -> None:
    scheduler = Scheduler()
    scheduler.push(_event(10, "now-ish"))
    scheduler.push(_event(50, "later"))

    first_pass = scheduler.pop_due(_BASE + timedelta(seconds=20))
    assert [e.body for e in first_pass] == ["now-ish"]

    # The not-yet-due event stays until a later now.
    assert scheduler.pop_due(_BASE + timedelta(seconds=20)) == []
    second_pass = scheduler.pop_due(_BASE + timedelta(seconds=50))
    assert [e.body for e in second_pass] == ["later"]


def test_pop_due_is_inclusive_of_now() -> None:
    scheduler = Scheduler()
    scheduler.push(_event(10, "exactly-now"))

    assert [e.body for e in scheduler.pop_due(_BASE + timedelta(seconds=10))] == ["exactly-now"]


def test_equal_emit_at_breaks_the_tie_on_insertion_order() -> None:
    # Same emit_at for every event: the monotonic tiebreaker must order them by
    # insertion (and the Event payloads — which carry no ordering — never compared).
    scheduler = Scheduler()
    for index in range(5):
        scheduler.push(_event(10, f"e{index}"))

    due = scheduler.pop_due(_BASE + timedelta(seconds=10))

    assert [e.body for e in due] == ["e0", "e1", "e2", "e3", "e4"]


def test_pop_due_on_empty_scheduler_is_empty() -> None:
    assert Scheduler().pop_due(_BASE) == []
