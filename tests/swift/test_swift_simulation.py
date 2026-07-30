"""Tests for the multi-day settlement-window simulation driver (Task 11).

A single :class:`Scenario` drives a realistic batch through the audit queues:
several instructions that settle over T…T+2 (one via a *late* T+2 confirmation
that must still correlate — not an orphan), a rejection and a settlement failure,
one instruction left aging (silent), and one injected orphan status. The tests
assert the collected :class:`DayReport`'s state distribution, its aged-out set and
its orphan anomaly all match the scenario.

Everything is deterministic: ``start`` / ``now`` are fixed and every put-time is
derived from them, so the collected picture is fully reproducible. All references
are invented and anonymised (``GWSEC…``, Counterparty A/B).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from gwlab.fake_messaging import FakeMessaging
from gwlab.swift.reference import State
from gwlab.swift.simulation import (
    SILENT_PATH,
    InstructionSpec,
    Scenario,
    run_window,
)
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

_QUEUES = SwiftQueueSet()
_HORIZON = Horizon(days=3)

#: The window opens at T and is collected at T+3 17:00. ``start`` sits exactly a
#: horizon before ``now`` so an offset-0 instruction is both in-window and, if
#: non-terminal, aged out — the "about to fall out of retention" boundary.
_START = datetime(2026, 7, 26, 17, 0)
_NOW = datetime(2026, 7, 29, 17, 0)

#: Two days, in seconds: pushes a confirmation from T to a late T+2 arrival.
_TWO_DAYS = 2 * 24 * 60 * 60.0


def _distribution(report_states: list[State]) -> Counter[State]:
    return Counter(report_states)


def test_run_window_settles_rejects_fails_ages_and_flags_orphan() -> None:
    scenario = Scenario(
        specs=[
            # Settles over T..T+2 via a late confirmation (still correlates).
            InstructionSpec(
                seme="GWSEC0000000001",
                path="clean-settle",
                instructed_offset_days=0,
                response_delays=[300.0, 300.0, _TWO_DAYS],
            ),
            # Settles same day (fast path).
            InstructionSpec(
                seme="GWSEC0000000002",
                path="clean-settle",
                instructed_offset_days=1,
                response_delays=[300.0, 600.0, 900.0],
            ),
            # Rejected at input validation.
            InstructionSpec(
                seme="GWSEC0000000003",
                path="rejected-at-validation",
                instructed_offset_days=1,
                response_delays=[300.0],
            ),
            # Matched, then fails to settle.
            InstructionSpec(
                seme="GWSEC0000000004",
                path="matched-then-failing",
                instructed_offset_days=1,
                response_delays=[300.0, 600.0, 900.0],
            ),
            # Acknowledged and left in-flight (not yet aged out at T+1).
            InstructionSpec(
                seme="GWSEC0000000005",
                path="acknowledged-aging",
                instructed_offset_days=2,
                response_delays=[300.0],
            ),
            # Instructed and never answered: ages out at the horizon boundary.
            InstructionSpec(
                seme="GWSEC0000000006",
                path=SILENT_PATH,
                instructed_offset_days=0,
            ),
        ],
        inject_orphan=True,
    )

    report = run_window(FakeMessaging(), _QUEUES, _HORIZON, scenario, _START, _NOW)

    by_seme = {state.seme: state.current for state in report.states}
    assert by_seme == {
        "GWSEC0000000001": State.SETTLED,
        "GWSEC0000000002": State.SETTLED,
        "GWSEC0000000003": State.REJECTED,
        "GWSEC0000000004": State.FAILED,
        "GWSEC0000000005": State.ACKNOWLEDGED,
        "GWSEC0000000006": State.INSTRUCTED,
    }
    assert _distribution([s.current for s in report.states]) == Counter(
        {
            State.SETTLED: 2,
            State.REJECTED: 1,
            State.FAILED: 1,
            State.ACKNOWLEDGED: 1,
            State.INSTRUCTED: 1,
        }
    )

    # Only the silent instruction has aged out; the acknowledged-aging one is still
    # in-flight but within the window.
    assert [state.seme for state in report.aged_out] == ["GWSEC0000000006"]

    # The one injected orphan is the only anomaly; the late T+2 confirmation is
    # correctly correlated and never flagged.
    assert [(a.kind, a.reference) for a in report.anomalies] == [("orphan", "GWSECORPHAN00000")]


def test_run_window_without_orphan_injection_has_no_orphan_anomaly() -> None:
    scenario = Scenario(
        specs=[
            InstructionSpec(
                seme="GWSEC0000000101",
                path="clean-settle",
                instructed_offset_days=1,
                response_delays=[300.0, 600.0, 900.0],
            ),
        ],
        inject_orphan=False,
    )

    report = run_window(FakeMessaging(), _QUEUES, _HORIZON, scenario, _START, _NOW)

    assert [state.current for state in report.states] == [State.SETTLED]
    assert report.aged_out == []
    assert report.anomalies == []
