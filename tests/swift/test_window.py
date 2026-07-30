"""Tests for settlement-window scoping (in-scope / aged-out)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.lifecycle import Hop, InstructionState
from gwlab.swift.reference import State
from gwlab.swift.swiftconfig import Horizon
from gwlab.swift.window import in_window, is_aged_out

_NOW = datetime(2026, 7, 29, 17, 0)
_HORIZON = Horizon(days=3)


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
