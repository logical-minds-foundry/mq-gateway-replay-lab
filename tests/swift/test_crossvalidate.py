"""Tests for the per-SEME cross-validation of the two lifecycle views (#122)."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.crossvalidate import (
    KIND_MISSING_IN_APP,
    KIND_MISSING_IN_COLLECTOR,
    KIND_STATE_MISMATCH,
    Divergence,
    collector_end_states,
    cross_validate,
    format_report,
)
from gwlab.swift.endstate import EndState
from gwlab.swift.lifecycle import Hop, InstructionState
from gwlab.swift.reference import State

_BASE = datetime(2026, 7, 29, 9, 0)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _settled(seme: str) -> EndState:
    return EndState(seme, State.SETTLED, _at(3))


def _failed(seme: str) -> EndState:
    return EndState(seme, State.FAILED, _at(2))


def test_full_agreement_all_match() -> None:
    collector = [_settled("ALPHA01"), _settled("ALPHA02"), _failed("ALPHA03")]
    app = [_settled("ALPHA01"), _settled("ALPHA02"), _failed("ALPHA03")]

    result = cross_validate(collector, app)

    assert result.agreeing == 3
    assert result.total == 3
    assert result.divergences == []


def test_state_mismatch_is_named() -> None:
    # BRAVO09 settled in the collector's view but failed in the app's view.
    collector = [_settled("BRAVO07"), _settled("BRAVO09")]
    app = [_settled("BRAVO07"), _failed("BRAVO09")]

    result = cross_validate(collector, app)

    assert result.agreeing == 1
    assert result.total == 2
    assert result.divergences == [
        Divergence("BRAVO09", State.SETTLED, State.FAILED, KIND_STATE_MISMATCH),
    ]


def test_missing_in_app() -> None:
    collector = [_settled("CHARLIE11"), _settled("CHARLIE12")]
    app = [_settled("CHARLIE11")]

    result = cross_validate(collector, app)

    assert result.agreeing == 1
    assert result.total == 2
    assert result.divergences == [
        Divergence("CHARLIE12", State.SETTLED, None, KIND_MISSING_IN_APP),
    ]


def test_missing_in_collector() -> None:
    collector = [_settled("DELTA21")]
    app = [_settled("DELTA21"), _failed("DELTA22")]

    result = cross_validate(collector, app)

    assert result.agreeing == 1
    assert result.total == 2
    assert result.divergences == [
        Divergence("DELTA22", None, State.FAILED, KIND_MISSING_IN_COLLECTOR),
    ]


def test_empty_inputs() -> None:
    result = cross_validate([], [])

    assert result.agreeing == 0
    assert result.total == 0
    assert result.divergences == []


def test_collector_end_states_maps_folded_states() -> None:
    folded = [
        InstructionState(
            "ECHO31",
            State.SETTLED,
            hops=[
                Hop(State.INSTRUCTED, _at(0)),
                Hop(State.ACKNOWLEDGED, _at(1)),
                Hop(State.MATCHED, _at(2)),
                Hop(State.SETTLED, _at(3)),
            ],
        ),
        InstructionState("ECHO32", State.INSTRUCTED, hops=[Hop(State.INSTRUCTED, _at(0))]),
    ]

    assert collector_end_states(folded) == [
        EndState("ECHO31", State.SETTLED, _at(3)),
        EndState("ECHO32", State.INSTRUCTED, None),
    ]


def test_collector_end_states_feeds_cross_validate() -> None:
    folded = [
        InstructionState(
            "FOXTROT41",
            State.SETTLED,
            hops=[Hop(State.INSTRUCTED, _at(0)), Hop(State.SETTLED, _at(3))],
        ),
    ]
    app = [EndState("FOXTROT41", State.SETTLED, _at(3))]

    result = cross_validate(collector_end_states(folded), app)

    assert result.agreeing == 1
    assert result.divergences == []


def test_format_report_all_agree_has_no_divergences() -> None:
    result = cross_validate([_settled("GOLF51")], [_settled("GOLF51")])

    report = format_report(result)

    assert report == "cross-validation: 1/1 agree, 0 divergences"


def test_format_report_contains_counts_and_divergent_semes() -> None:
    collector = [_settled("HOTEL61"), _settled("HOTEL62"), _settled("HOTEL63")]
    app = [_settled("HOTEL61"), _failed("HOTEL62"), _settled("HOTEL64")]

    report = format_report(cross_validate(collector, app))

    # Headline counts: 1 agreement out of 4 distinct SEMEs, 3 divergences.
    assert "cross-validation: 1/4 agree" in report
    assert "3 divergences" in report
    # Each divergent SEME is named; the agreeing one is not listed.
    assert "HOTEL62: state-mismatch (collector=settled app=failed)" in report
    assert "HOTEL63: missing-in-app (collector=settled app=-)" in report
    assert "HOTEL64: missing-in-collector (collector=- app=settled)" in report
    assert "HOTEL61" not in report
