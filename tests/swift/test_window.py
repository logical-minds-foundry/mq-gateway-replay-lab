"""Tests for settlement-window scoping (in-scope / aged-out)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.lifecycle import Hop, InstructionState
from gwlab.swift.reference import State
from gwlab.swift.swiftconfig import Horizon
from gwlab.swift.window import in_window, is_aged_out

_NOW = datetime(2026, 7, 29, 17, 0)
_HORIZON = Horizon(days=3)
#: 3 sim-days at C=60 is 72 real minutes — a compressed run collapses the window.
_COMPRESSED = Horizon(days=3, compression=60.0)


def _state(current: State, instructed_at: datetime) -> InstructionState:
    return InstructionState("GWSEC0000000010", current, [Hop(State.INSTRUCTED, instructed_at)])


def test_recent_message_is_in_window() -> None:
    assert in_window(_NOW - timedelta(days=1), _NOW, _HORIZON) is True


def test_message_exactly_at_the_horizon_is_in_window() -> None:
    # boundary is inclusive: a message put exactly horizon days ago is still in scope
    assert in_window(_NOW - timedelta(days=3), _NOW, _HORIZON) is True


def test_message_past_the_horizon_is_excluded() -> None:
    assert in_window(_NOW - timedelta(days=4), _NOW, _HORIZON) is False


def test_terminal_instruction_is_never_aged_out() -> None:
    old = _NOW - timedelta(days=10)
    assert is_aged_out(_state(State.SETTLED, old), _NOW, _HORIZON) is False


def test_nonterminal_instruction_past_horizon_is_aged_out() -> None:
    old = _NOW - timedelta(days=3)
    assert is_aged_out(_state(State.INSTRUCTED, old), _NOW, _HORIZON) is True


def test_nonterminal_recent_instruction_is_not_aged_out() -> None:
    recent = _NOW - timedelta(days=1)
    assert is_aged_out(_state(State.ACKNOWLEDGED, recent), _NOW, _HORIZON) is False


def test_compression_shrinks_the_real_window() -> None:
    # A message 2h old is inside the real 3-day window, but past the 72-min
    # compressed window at C=60 — the window works in sim-time.
    two_hours_old = _NOW - timedelta(hours=2)
    assert in_window(two_hours_old, _NOW, _HORIZON) is True
    assert in_window(two_hours_old, _NOW, _COMPRESSED) is False


def test_compression_ages_out_where_real_horizon_would_not() -> None:
    # The same 2h-old non-terminal instruction: not aged out at the real horizon,
    # but aged out under compression (2h > 72min).
    two_hours_old = _NOW - timedelta(hours=2)
    assert is_aged_out(_state(State.INSTRUCTED, two_hours_old), _NOW, _HORIZON) is False
    assert is_aged_out(_state(State.INSTRUCTED, two_hours_old), _NOW, _COMPRESSED) is True


def test_compression_one_collapses_to_real_time() -> None:
    # C=1 is the production setting: the horizon is exactly 3 real days.
    real = Horizon(days=3, compression=1.0)
    exactly_horizon_old = _NOW - timedelta(days=3)
    assert in_window(exactly_horizon_old, _NOW, real) is True
    assert is_aged_out(_state(State.INSTRUCTED, exactly_horizon_old), _NOW, real) is True
