"""Tests for the streaming collector's in-memory :class:`SettlementState`.

Pure logic: messages are constructed as :class:`SwiftMessage` values directly
(as in ``test_lifecycle``), so these tests exercise the incremental fold,
window-gated orphan detection, lifecycle-aware retention, aging, and the
recorded-decode-anomaly channel with no MQ boundary in sight.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.collect import Anomaly
from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.reference import State
from gwlab.swift.stream.state import SettlementState
from gwlab.swift.swiftconfig import Horizon

_HORIZON = Horizon(days=3)
_HORIZON_DELTA = timedelta(days=3)
_NOW = datetime(2026, 7, 29, 17, 0)
_RECENT = _NOW - timedelta(days=1)  # comfortably inside a 3-day horizon
_LONG_AGO = _NOW - timedelta(days=10)  # well outside it


def _instruction(seme: str | None, at: datetime) -> SwiftMessage:
    return SwiftMessage("541", seme, None, (), at, True, False)


def _status(rela: str | None, pairs: list[tuple[str, str]], at: datetime) -> SwiftMessage:
    statuses = tuple(StatusItem(qualifier, value, None) for qualifier, value in pairs)
    return SwiftMessage("548", None, rela, statuses, at, False, False)


def _confirmation(rela: str, at: datetime) -> SwiftMessage:
    return SwiftMessage("545", None, rela, (), at, False, True)


def test_ingest_folds_instruction_and_responses_to_settled() -> None:
    state = SettlementState()
    state.ingest(_instruction("S1", _RECENT))
    state.ingest(_status("S1", [("IPRC", "PACK")], _RECENT))
    state.ingest(_confirmation("S1", _RECENT))

    report = state.report(_NOW, _HORIZON)

    currents = {s.seme: s.current for s in report.states}
    assert currents["S1"] is State.SETTLED
    assert report.anomalies == []
    assert report.aged_out == []


def test_unknown_response_is_orphan_only_when_in_window() -> None:
    state = SettlementState()
    state.ingest(_status("UNKNOWN", [("IPRC", "PACK")], _RECENT))
    state.ingest(_status("UNKNOWN2", [("IPRC", "PACK")], _LONG_AGO))

    report = state.report(_NOW, _HORIZON)

    orphans = [a for a in report.anomalies if a.kind == "orphan"]
    assert [a.reference for a in orphans] == ["UNKNOWN"]  # the recent one only
    assert report.states == []


def test_response_with_no_rela_is_orphan_when_in_window() -> None:
    state = SettlementState()
    state.ingest(_status(None, [("IPRC", "PACK")], _RECENT))

    report = state.report(_NOW, _HORIZON)

    assert [(a.kind, a.reference) for a in report.anomalies] == [("orphan", "")]


def test_age_keeps_open_purges_completed_and_expired() -> None:
    state = SettlementState()
    state.ingest(_instruction("OPEN", _LONG_AGO))  # never confirmed: open, old
    state.ingest(_instruction("DONE", _LONG_AGO))  # settled AND expired
    state.ingest(_confirmation("DONE", _LONG_AGO))
    state.ingest(_instruction("FRESH", _RECENT))  # settled but still in-window
    state.ingest(_confirmation("FRESH", _RECENT))

    state.age(_NOW, _HORIZON)

    semes = {s.seme for s in state.report(_NOW, _HORIZON).states}
    assert semes == {"OPEN", "FRESH"}  # open retained, in-window terminal retained
    assert "DONE" not in semes  # complete AND expired -> purged


def test_age_prunes_out_of_window_orphan_candidates() -> None:
    state = SettlementState()
    state.ingest(_status("UNKNOWN", [("IPRC", "PACK")], _RECENT))
    state.ingest(_status("UNKNOWN2", [("IPRC", "PACK")], _LONG_AGO))
    assert state.report(_NOW, _HORIZON).scanned == 2  # both held before aging

    state.age(_NOW, _HORIZON)

    assert state.report(_NOW, _HORIZON).scanned == 1  # the expired candidate is gone


def test_recorded_decode_anomalies_surface_in_the_report() -> None:
    state = SettlementState()
    state.record_anomalies([Anomaly("malformed", "abc", "bad body")])
    state.record_anomalies([Anomaly("field-violation", "20C:SEME", "'x' fails length")])

    kinds = [a.kind for a in state.report(_NOW, _HORIZON).anomalies]

    assert kinds == ["malformed", "field-violation"]


def test_transition_anomaly_surfaces_in_the_report() -> None:
    state = SettlementState()
    state.ingest(_instruction("S1", _RECENT))
    state.ingest(_confirmation("S1", _RECENT))
    # MATCHED asserted after SETTLED is unreachable -> a transition anomaly.
    state.ingest(_status("S1", [("MTCH", "MACH")], _RECENT + timedelta(minutes=1)))

    report = state.report(_NOW, _HORIZON)

    assert report.states[0].current is State.SETTLED
    transitions = [a for a in report.anomalies if a.kind == "transition"]
    assert [a.reference for a in transitions] == ["S1"]


def test_seme_less_instruction_is_skipped() -> None:
    state = SettlementState()
    state.ingest(_instruction(None, _RECENT))  # cannot be keyed/correlated

    report = state.report(_NOW, _HORIZON)

    assert report.states == []
    assert report.scanned == 0


def test_scanned_counts_current_message_population() -> None:
    state = SettlementState()
    state.ingest(_instruction("S1", _RECENT))
    state.ingest(_status("S1", [("IPRC", "PACK")], _RECENT))
    state.ingest(_confirmation("S1", _RECENT))
    state.ingest(_status("ORPH", [("IPRC", "PACK")], _RECENT))  # orphan candidate

    # instruction + 2 responses + 1 orphan candidate = 4
    assert state.report(_NOW, _HORIZON).scanned == 4


def test_reingested_instruction_preserves_earlier_responses() -> None:
    state = SettlementState()
    state.ingest(_instruction("S1", _RECENT))
    state.ingest(_status("S1", [("IPRC", "PACK")], _RECENT))
    state.ingest(_instruction("S1", _RECENT))  # duplicate audit copy of the instruction
    state.ingest(_confirmation("S1", _RECENT))

    report = state.report(_NOW, _HORIZON)

    assert report.states[0].current is State.SETTLED  # both responses still folded
    assert report.scanned == 3  # one instruction entry + two responses


def test_non_terminal_past_horizon_is_aged_out() -> None:
    state = SettlementState()
    state.ingest(_instruction("S1", _NOW - _HORIZON_DELTA))

    report = state.report(_NOW, _HORIZON)

    assert [s.seme for s in report.aged_out] == ["S1"]
    assert report.states[0].current is State.INSTRUCTED
