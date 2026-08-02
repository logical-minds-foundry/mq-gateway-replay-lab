"""Per-reason-family deviation scoring over the retained window (design §7, Task 9).

:meth:`SettlementState.deviations` folds the macro
:meth:`~gwlab.swift.stream.state.SettlementState.segment_counts` picture into a
binomial-proportion z-score per exception family (:func:`gwlab.swift.stream.anomaly.deviation_z`)
against the calibrated baselines, and feeds each family's observed fail-share into
a per-family :class:`~gwlab.swift.stream.anomaly.Ewma` that persists across
``deviations`` calls so the slow-drift (silent-degradation) case accumulates.
Built on populated state via :meth:`SettlementState.ingest`, never on hand-poked
internals.
"""

from __future__ import annotations

from datetime import datetime

from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.stream.state import REASON_FAMILY_BASELINES, SettlementState


def _instr(seme: str, mt: str = "541") -> SwiftMessage:
    return SwiftMessage(mt, seme, None, (), datetime(2026, 1, 1), True, False)


def _resp(rela: str, *st: StatusItem) -> SwiftMessage:
    return SwiftMessage("548", None, rela, st, datetime(2026, 1, 1, 0, 1), False, False)


_FAMILY_STATUS: dict[str, StatusItem] = {
    "rejection": StatusItem("IPRC", "REJT", "NARR"),
    "matching": StatusItem("MTCH", "NMAT", None),
    "settlement": StatusItem("SETT", "PENF", "LACK"),
    "cash": StatusItem("SETT", "PENF", "MONY"),
}


def _populate(fails: int, total: int, *, family: str = "rejection") -> SettlementState:
    """A state of ``total`` instructions, ``fails`` of them classified into ``family``.

    The remainder stay benign (``OTHER``), so ``total`` is the denominator and
    ``fails`` the numerator for that family's window fail-share.
    """
    s = SettlementState()
    for index in range(total):
        seme = f"S{index}"
        s.ingest(_instr(seme))
        if index < fails:
            s.ingest(_resp(seme, _FAMILY_STATUS[family]))
    return s


def test_reason_family_deviation_positive_above_baseline() -> None:
    # 8% observed rejection share vs a 0.3% baseline is a loud spike (z >> 3).
    s = _populate(fails=8, total=100, family="rejection")
    devs = s.deviations({"rejection": 0.003})
    assert devs["rejection"] > 3.0


def test_family_below_min_sample_is_absent() -> None:
    # Only 5 instructions retained — below deviation_z's min_sample gate, so the
    # family is dropped (None), not reported as a spurious z on thin evidence.
    s = _populate(fails=1, total=5, family="rejection")
    devs = s.deviations({"rejection": 0.003})
    assert "rejection" not in devs


def test_zero_when_observed_matches_baseline() -> None:
    # 2 rejections in 100 against a 0.02 baseline: the observed rate equals the
    # expectation, so the z-score is ~0 (present, but not a deviation).
    s = _populate(fails=2, total=100, family="rejection")
    devs = s.deviations({"rejection": 0.02})
    assert abs(devs["rejection"]) < 1e-9


def test_empty_state_reports_no_deviations() -> None:
    # No traffic: total n is 0, every family is below min_sample and dropped, and
    # the total-is-zero rate branch is exercised without dividing by zero.
    s = SettlementState()
    assert s.deviations({"rejection": 0.003}) == {}


def test_ewma_persists_and_drifts_across_deviations_calls() -> None:
    # First call seeds the family EWMA at the observed rate; a second call after
    # the share climbs moves the EWMA toward — but not straight to — the new rate,
    # proving it is retained across calls (the silent-degradation detector).
    s = _populate(fails=2, total=100, family="settlement")
    s.deviations(REASON_FAMILY_BASELINES)
    seeded = s.family_ewmas["settlement"]
    assert seeded == 0.02  # 2 / 100

    for index in range(100, 200):  # 100 more instructions, all settlement fails
        seme = f"S{index}"
        s.ingest(_instr(seme))
        s.ingest(_resp(seme, _FAMILY_STATUS["settlement"]))
    s.deviations(REASON_FAMILY_BASELINES)
    drifted = s.family_ewmas["settlement"]

    # observed share climbed to 102/200 = 0.51; the EWMA drifted up from 0.02 but
    # is damped below the new instantaneous rate — history is carried, not reset.
    assert seeded < drifted < 0.51


def test_baseline_constant_covers_the_four_exception_families() -> None:
    # The config constant is the roll-up of the calibration reference; it must name
    # exactly the non-benign families (OTHER is benign and has no baseline).
    assert set(REASON_FAMILY_BASELINES) == {"settlement", "cash", "matching", "rejection"}
    assert all(0.0 < rate < 1.0 for rate in REASON_FAMILY_BASELINES.values())
