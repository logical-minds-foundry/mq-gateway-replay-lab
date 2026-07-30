"""Tests for the monotonic lifecycle fold."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.correlate import Correlated
from gwlab.swift.lifecycle import (
    REASON_BACKWARD,
    REASON_UNREACHABLE,
    fold,
    triggers_for,
)
from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.reference import State

_BASE = datetime(2026, 7, 29, 9, 0)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _instruction(seme: str | None = "S0", minute: int = 0) -> SwiftMessage:
    return SwiftMessage("541", seme, None, (), _at(minute), True, False)


def _status(pairs: list[tuple[str, str]], minute: int = 0) -> SwiftMessage:
    statuses = tuple(StatusItem(qualifier, value, None) for qualifier, value in pairs)
    return SwiftMessage("548", None, "S0", statuses, _at(minute), False, False)


def _confirmation(minute: int = 0) -> SwiftMessage:
    return SwiftMessage("545", None, "S0", (), _at(minute), False, True)


def _corr(instruction: SwiftMessage, responses: list[SwiftMessage]) -> Correlated:
    return Correlated(instruction, responses)


def test_clean_settle_records_four_hops() -> None:
    state = fold(
        _corr(
            _instruction(),
            [_status([("IPRC", "PACK")], 1), _status([("MTCH", "MACH")], 2), _confirmation(3)],
        )
    )
    assert state.current is State.SETTLED
    assert [hop.state for hop in state.hops] == [
        State.INSTRUCTED,
        State.ACKNOWLEDGED,
        State.MATCHED,
        State.SETTLED,
    ]
    assert state.anomalies == []


def test_multi_status_message_advances_two_hops() -> None:
    state = fold(_corr(_instruction(), [_status([("IPRC", "PACK"), ("MTCH", "MACH")], 1)]))
    assert state.current is State.MATCHED
    assert len(state.hops) == 3


def test_multi_status_out_of_field_order_still_advances_cleanly() -> None:
    # statuses listed matched-before-accepted must not read as a backward step
    state = fold(_corr(_instruction(), [_status([("MTCH", "MACH"), ("IPRC", "PACK")], 1)]))
    assert state.current is State.MATCHED
    assert state.anomalies == []


def test_repeat_status_is_noop_not_anomaly() -> None:
    state = fold(
        _corr(_instruction(), [_status([("IPRC", "PACK")], 1), _status([("IPRC", "PACK")], 2)])
    )
    assert state.current is State.ACKNOWLEDGED
    assert state.anomalies == []
    assert sum(1 for hop in state.hops if hop.state is State.ACKNOWLEDGED) == 1


def test_reject_from_instructed_is_terminal() -> None:
    state = fold(_corr(_instruction(), [_status([("IPRC", "REJT")], 1)]))
    assert state.current is State.REJECTED


def test_failing_can_recover_to_settled_within_the_window() -> None:
    state = fold(_corr(_instruction(), [_status([("SETT", "PENF")], 1), _confirmation(2)]))
    assert state.current is State.SETTLED
    assert state.anomalies == []


def test_settled_after_rejected_is_unreachable_anomaly() -> None:
    state = fold(_corr(_instruction(), [_status([("IPRC", "REJT")], 1), _confirmation(2)]))
    assert state.current is State.REJECTED
    assert state.anomalies[0].reason == REASON_UNREACHABLE
    assert state.anomalies[0].attempted is State.SETTLED


def test_acknowledged_after_matched_is_backward_anomaly() -> None:
    state = fold(
        _corr(_instruction(), [_status([("MTCH", "MACH")], 1), _status([("IPRC", "PACK")], 2)])
    )
    assert state.current is State.MATCHED
    anomaly = state.anomalies[0]
    assert anomaly.reason == REASON_BACKWARD
    assert anomaly.attempted is State.ACKNOWLEDGED
    assert anomaly.from_state is State.MATCHED
    assert anomaly.seme == "S0"


def test_seme_less_instruction_folds_to_empty_seme() -> None:
    state = fold(_corr(_instruction(seme=None), []))
    assert state.seme == ""
    assert state.current is State.INSTRUCTED


def test_triggers_for_confirmation_is_settled() -> None:
    assert triggers_for(_confirmation()) == [State.SETTLED]


def test_triggers_for_status_maps_via_reference() -> None:
    assert triggers_for(_status([("IPRC", "PACK")])) == [State.ACKNOWLEDGED]


def test_triggers_for_non_advancing_status_is_empty() -> None:
    # SETT//PEND is documented but does not advance the machine
    assert triggers_for(_status([("SETT", "PEND")])) == []


def test_triggers_for_instruction_is_empty() -> None:
    assert triggers_for(_instruction()) == []
