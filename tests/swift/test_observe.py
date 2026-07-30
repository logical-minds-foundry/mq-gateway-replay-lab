"""Tests for the observability fold (DayReport -> Metrics).

Fixtures are built directly from the collector/lifecycle dataclasses with
explicit invented SEMEs and explicit datetimes — never ``datetime.now()`` — so
every derived number (distribution counts, hop latencies, aging order) is exact
and deterministic.
"""

from __future__ import annotations

from datetime import datetime

from gwlab.swift.collect import Anomaly, DayReport
from gwlab.swift.lifecycle import Hop, InstructionState
from gwlab.swift.observe import metrics
from gwlab.swift.reference import State

_NOW = datetime(2026, 7, 29, 17, 0)


def _instruction(
    seme: str,
    hops: list[tuple[State, datetime]],
    current: State | None = None,
) -> InstructionState:
    hop_list = [Hop(state, at) for state, at in hops]
    resolved = current if current is not None else hop_list[-1].state
    return InstructionState(seme=seme, current=resolved, hops=hop_list)


def _report(
    states: list[InstructionState],
    *,
    aged_out: list[InstructionState] | None = None,
    anomalies: list[Anomaly] | None = None,
    scanned: int = 0,
) -> DayReport:
    return DayReport(
        states=states,
        aged_out=aged_out or [],
        anomalies=anomalies or [],
        scanned=scanned,
    )


def test_distribution_counts_each_state_with_every_bucket_present() -> None:
    at = datetime(2026, 7, 29, 9, 0)
    states = [
        _instruction("GWSEC0000000001", [(State.INSTRUCTED, at)]),
        _instruction("GWSEC0000000002", [(State.INSTRUCTED, at)]),
        _instruction("GWSEC0000000003", [(State.INSTRUCTED, at), (State.ACKNOWLEDGED, at)]),
        _instruction("GWSEC0000000004", [(State.INSTRUCTED, at), (State.MATCHED, at)]),
        _instruction("GWSEC0000000005", [(State.INSTRUCTED, at), (State.SETTLED, at)]),
        _instruction("GWSEC0000000006", [(State.INSTRUCTED, at)], current=State.REJECTED),
        _instruction("GWSEC0000000007", [(State.INSTRUCTED, at)], current=State.FAILED),
    ]

    result = metrics(_report(states, scanned=7), _NOW)

    assert result.distribution == {
        State.INSTRUCTED: 2,
        State.ACKNOWLEDGED: 1,
        State.MATCHED: 1,
        State.SETTLED: 1,
        State.REJECTED: 1,
        State.FAILED: 1,
    }
    # Every state is a key even when its count is non-zero here; the empty-report
    # test proves the zero-fill. scanned is passed straight through.
    assert set(result.distribution) == set(State)
    assert result.scanned == 7


def test_latencies_use_hop_timestamps_with_correct_labels_and_mean_p95() -> None:
    # Two instructions share the instructed->acknowledged hop (samples 60s, 180s);
    # only the first also matches (a single acknowledged->matched sample of 240s).
    first = _instruction(
        "GWSEC0000000010",
        [
            (State.INSTRUCTED, datetime(2026, 7, 29, 9, 0, 0)),
            (State.ACKNOWLEDGED, datetime(2026, 7, 29, 9, 1, 0)),
            (State.MATCHED, datetime(2026, 7, 29, 9, 5, 0)),
        ],
    )
    second = _instruction(
        "GWSEC0000000011",
        [
            (State.INSTRUCTED, datetime(2026, 7, 29, 10, 0, 0)),
            (State.ACKNOWLEDGED, datetime(2026, 7, 29, 10, 3, 0)),
        ],
    )

    result = metrics(_report([first, second]), _NOW)

    by_label = {stat.hop: stat for stat in result.latencies}
    assert set(by_label) == {"instructed->acknowledged", "acknowledged->matched"}
    ack = by_label["instructed->acknowledged"]
    assert ack.mean_seconds == 120.0  # (60 + 180) / 2
    assert ack.p95_seconds == 180.0  # nearest-rank of [60, 180]: rank ceil(1.9)=2 -> 180
    match = by_label["acknowledged->matched"]
    assert match.mean_seconds == 240.0  # single sample
    assert match.p95_seconds == 240.0  # nearest-rank of one sample is that sample


def test_attention_sets_carry_the_right_semes_and_aging_is_oldest_first() -> None:
    oldest = _instruction("GWSEC0000000020", [(State.INSTRUCTED, datetime(2026, 7, 27, 9, 0))])
    middle = _instruction(
        "GWSEC0000000021",
        [
            (State.INSTRUCTED, datetime(2026, 7, 28, 9, 0)),
            (State.ACKNOWLEDGED, datetime(2026, 7, 28, 9, 5)),
        ],
    )
    newest = _instruction(
        "GWSEC0000000022",
        [
            (State.INSTRUCTED, datetime(2026, 7, 29, 9, 0)),
            (State.MATCHED, datetime(2026, 7, 29, 9, 5)),
        ],
    )
    settled = _instruction(
        "GWSEC0000000023",
        [
            (State.INSTRUCTED, datetime(2026, 7, 29, 8, 0)),
            (State.SETTLED, datetime(2026, 7, 29, 8, 5)),
        ],
    )
    rejected = _instruction(
        "GWSEC0000000024",
        [(State.INSTRUCTED, datetime(2026, 7, 29, 8, 0))],
        current=State.REJECTED,
    )
    failed = _instruction(
        "GWSEC0000000025",
        [(State.INSTRUCTED, datetime(2026, 7, 29, 8, 0))],
        current=State.FAILED,
    )

    result = metrics(_report([newest, oldest, settled, middle, rejected, failed]), _NOW)

    # aging is the non-terminal set only (settled/rejected/failed excluded), oldest first.
    assert [state.seme for state in result.aging] == [
        "GWSEC0000000020",
        "GWSEC0000000021",
        "GWSEC0000000022",
    ]
    assert [state.seme for state in result.rejected] == ["GWSEC0000000024"]
    assert [state.seme for state in result.failed] == ["GWSEC0000000025"]


def test_aged_out_is_passed_through_from_the_report() -> None:
    open_edge = _instruction("GWSEC0000000030", [(State.INSTRUCTED, datetime(2026, 7, 26, 17, 0))])

    result = metrics(_report([open_edge], aged_out=[open_edge]), _NOW)

    assert [state.seme for state in result.aged_out] == ["GWSEC0000000030"]


def test_anomalies_by_kind_tallies_all_four_and_total_is_their_sum() -> None:
    anomalies = [
        Anomaly("malformed", "GWSEC0000000040", "untokenizable"),
        Anomaly("malformed", "GWSEC0000000041", "no put-time"),
        Anomaly("field-violation", "20C:SEME", "too long"),
        Anomaly("orphan", "GWSECORPHAN00000", "no instruction"),
        Anomaly("transition", "GWSEC0000000042", "backward"),
    ]

    result = metrics(_report([], anomalies=anomalies), _NOW)

    assert result.anomalies_by_kind == {
        "malformed": 2,
        "field-violation": 1,
        "orphan": 1,
        "transition": 1,
    }
    assert result.anomalies == 5  # the total across every kind
    assert result.anomalies == sum(result.anomalies_by_kind.values())


def test_empty_report_yields_zeroed_metrics_with_no_divide_by_zero() -> None:
    result = metrics(_report([], scanned=0), _NOW)

    assert result.distribution == dict.fromkeys(State, 0)
    assert result.latencies == []
    assert result.aging == []
    assert result.aged_out == []
    assert result.rejected == []
    assert result.failed == []
    assert result.anomalies == 0
    assert result.anomalies_by_kind == {
        "malformed": 0,
        "field-violation": 0,
        "orphan": 0,
        "transition": 0,
    }
    assert result.scanned == 0
