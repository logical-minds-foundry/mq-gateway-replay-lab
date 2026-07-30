"""Tests for the v2 state-machine settlement board (Metrics -> self-contained HTML).

Mirrors v1's ``test_dashboard.py`` idiom — string/DOM-contains assertions — but
against the state-machine board. Fixtures are built directly from the
observe/lifecycle dataclasses with invented anonymised SEMEs and explicit
datetimes (never ``datetime.now()``), and every panel is exercised both empty
(no rows) and populated so both branches of each loop/guard are covered.
"""

from __future__ import annotations

from datetime import datetime

from gwlab.swift.dashboard import (
    _HEADER_AGED_OUT,
    _HEADER_AGING,
    _HEADER_FAILED,
    _HEADER_REJECTED,
    render_board,
)
from gwlab.swift.lifecycle import Hop, InstructionState
from gwlab.swift.observe import LatencyStats, Metrics
from gwlab.swift.reference import State

_NOW = datetime(2026, 7, 29, 17, 0)


def _instruction(seme: str, first_hop_at: datetime, current: State) -> InstructionState:
    return InstructionState(seme=seme, current=current, hops=[Hop(State.INSTRUCTED, first_hop_at)])


def _stage(label: str, count: int) -> str:
    """The exact hero-funnel fragment tying a state label to its count."""
    return f'{label}</span><span class="count">{count}</span>'


def _populated() -> Metrics:
    return Metrics(
        distribution={
            State.INSTRUCTED: 2,
            State.ACKNOWLEDGED: 3,
            State.MATCHED: 4,
            State.SETTLED: 5,
            State.REJECTED: 6,
            State.FAILED: 7,
        },
        latencies=[
            LatencyStats("instructed->acknowledged", 120.0, 180.0),
            LatencyStats("acknowledged->matched", 240.0, 240.0),
        ],
        aging=[
            _instruction("GWSEC0000000020", datetime(2026, 7, 27, 9, 0), State.INSTRUCTED),
            _instruction("GWSEC0000000021", datetime(2026, 7, 28, 9, 0), State.ACKNOWLEDGED),
        ],
        aged_out=[_instruction("GWSEC0000000030", datetime(2026, 7, 26, 17, 0), State.INSTRUCTED)],
        rejected=[_instruction("GWSEC0000000024", datetime(2026, 7, 29, 8, 0), State.REJECTED)],
        failed=[_instruction("GWSEC0000000025", datetime(2026, 7, 29, 8, 0), State.FAILED)],
        anomalies=3,
        anomalies_by_kind={"malformed": 2, "field-violation": 0, "orphan": 1, "transition": 0},
        scanned=4200,
    )


def _empty() -> Metrics:
    return Metrics(
        distribution=dict.fromkeys(State, 0),
        latencies=[],
        aging=[],
        aged_out=[],
        rejected=[],
        failed=[],
        anomalies=0,
        anomalies_by_kind={"malformed": 0, "field-violation": 0, "orphan": 0, "transition": 0},
        scanned=0,
    )


def test_render_board_is_a_self_contained_html_document() -> None:
    board = render_board(_populated(), _NOW)
    assert board.startswith("<!doctype html>")
    assert "<style>" in board  # inline CSS, no external asset link
    assert "http://" not in board and "https://" not in board  # nothing external
    assert board.rstrip().endswith("</html>")


def test_hero_funnel_shows_every_state_label_with_its_count() -> None:
    board = render_board(_populated(), _NOW)
    assert _stage("INSTRUCTED", 2) in board
    assert _stage("ACKNOWLEDGED", 3) in board
    assert _stage("MATCHED", 4) in board
    assert _stage("SETTLED", 5) in board
    assert _stage("REJECTED", 6) in board
    assert _stage("FAILED", 7) in board


def test_empty_metrics_still_show_all_six_states_zeroed() -> None:
    board = render_board(_empty(), _NOW)
    for label in ("INSTRUCTED", "ACKNOWLEDGED", "MATCHED", "SETTLED", "REJECTED", "FAILED"):
        assert _stage(label, 0) in board


def test_latency_panel_lists_each_hop_with_mean_and_p95_when_populated() -> None:
    board = render_board(_populated(), _NOW)
    assert "instructed->acknowledged" in board
    assert "acknowledged->matched" in board
    assert "120.0s (2.0m)" in board  # mean rendered in seconds and minutes
    assert "180.0s (3.0m)" in board  # p95
    assert "No completed hops yet." not in board


def test_latency_panel_shows_empty_state_when_no_hops() -> None:
    board = render_board(_empty(), _NOW)
    assert "No completed hops yet." in board
    assert "instructed->acknowledged" not in board


def test_attention_sets_list_headers_and_their_semes_when_populated() -> None:
    board = render_board(_populated(), _NOW)
    for header in (_HEADER_AGING, _HEADER_REJECTED, _HEADER_FAILED, _HEADER_AGED_OUT):
        assert f"<h2>{header}</h2>" in board
    assert "GWSEC0000000020" in board  # aging
    assert "GWSEC0000000021" in board  # aging
    assert "GWSEC0000000024" in board  # rejected
    assert "GWSEC0000000025" in board  # failed
    assert "GWSEC0000000030" in board  # aged out
    # age is rendered from now minus the first hop: 2026-07-27 09:00 -> 2026-07-29 17:00.
    assert "2d 08h 00m" in board


def test_attention_sets_render_headers_but_no_rows_when_empty() -> None:
    board = render_board(_empty(), _NOW)
    for header in (_HEADER_AGING, _HEADER_REJECTED, _HEADER_FAILED, _HEADER_AGED_OUT):
        assert f"<h2>{header}</h2>" in board
    assert 'class="empty">none</td>' in board  # placeholder row present
    assert "GWSEC" not in board  # no instruction rows


def test_anomaly_panel_breaks_out_each_kind_and_the_total() -> None:
    board = render_board(_populated(), _NOW)
    assert "<td>malformed</td><td>2</td>" in board
    assert "<td>field-violation</td><td>0</td>" in board
    assert "<td>orphan</td><td>1</td>" in board
    assert "<td>transition</td><td>0</td>" in board
    assert '<tr class="total"><td>total</td><td>3</td></tr>' in board
    assert 'class="hot"' in board  # a non-zero kind is highlighted
    assert 'class="cold"' in board  # a zero kind is not


def test_anomaly_panel_all_zero_when_empty() -> None:
    board = render_board(_empty(), _NOW)
    for kind in ("malformed", "field-violation", "orphan", "transition"):
        assert f"<td>{kind}</td><td>0</td>" in board
    assert '<tr class="total"><td>total</td><td>0</td></tr>' in board
    assert 'class="hot"' not in board  # nothing highlighted when all zero


def test_scan_cost_line_shows_the_scanned_count() -> None:
    assert "4200 messages scanned" in render_board(_populated(), _NOW)
    assert "0 messages scanned" in render_board(_empty(), _NOW)
