"""Tests for the common per-instruction end-state record (#122 cross-validation)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.endstate import EndState, end_state
from gwlab.swift.lifecycle import Hop, InstructionState
from gwlab.swift.reference import State

_BASE = datetime(2026, 7, 29, 9, 0)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def test_settled_carries_the_terminal_hop_time() -> None:
    state = InstructionState(
        "S1",
        State.SETTLED,
        hops=[
            Hop(State.INSTRUCTED, _at(0)),
            Hop(State.ACKNOWLEDGED, _at(1)),
            Hop(State.MATCHED, _at(2)),
            Hop(State.SETTLED, _at(3)),
        ],
    )
    assert end_state(state) == EndState("S1", State.SETTLED, _at(3))


def test_rejected_carries_the_reject_hop_time() -> None:
    state = InstructionState(
        "S2",
        State.REJECTED,
        hops=[Hop(State.INSTRUCTED, _at(0)), Hop(State.REJECTED, _at(1))],
    )
    assert end_state(state) == EndState("S2", State.REJECTED, _at(1))


def test_failed_carries_the_fail_hop_time() -> None:
    state = InstructionState(
        "S3",
        State.FAILED,
        hops=[
            Hop(State.INSTRUCTED, _at(0)),
            Hop(State.ACKNOWLEDGED, _at(1)),
            Hop(State.MATCHED, _at(2)),
            Hop(State.FAILED, _at(3)),
        ],
    )
    assert end_state(state) == EndState("S3", State.FAILED, _at(3))


def test_in_flight_instructed_has_no_terminal_timestamp() -> None:
    state = InstructionState("S4", State.INSTRUCTED, hops=[Hop(State.INSTRUCTED, _at(0))])
    assert end_state(state) == EndState("S4", State.INSTRUCTED, None)


def test_in_flight_after_advancing_has_no_terminal_timestamp() -> None:
    # An acknowledged-but-unsettled instruction is still in flight: terminal_at None.
    state = InstructionState(
        "S5",
        State.ACKNOWLEDGED,
        hops=[Hop(State.INSTRUCTED, _at(0)), Hop(State.ACKNOWLEDGED, _at(1))],
    )
    result = end_state(state)
    assert result.state is State.ACKNOWLEDGED
    assert result.terminal_at is None
