"""Tests for Register B — the exceptional-event-detection register (Task 10).

These lock the per-reason-family deviation panel (with z-threshold coloring), the
rare-event amplification panel for the rejection family, and the EWMA slow-drift
trend — the register that reads the Task-9 ``swift_segment_fail_deviation`` and
``swift_segment_fail_ewma{dim="reason_family",id=...}`` series.
"""

from __future__ import annotations

from gwlab.swift.board import build_settlement_board


def _panel_exprs(panel: dict) -> list[str]:
    return [t["expr"] for t in panel.get("targets", [])]


def _panel_by_title_contains(board: dict, needle: str) -> dict:
    return next(p for p in board["panels"] if needle in p.get("title", ""))


def _steps(panel: dict) -> list[dict]:
    return panel["fieldConfig"]["defaults"]["thresholds"]["steps"]


# --- The per-reason-family deviation panel ------------------------------------


def test_deviation_panel_scores_each_reason_family() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    panel = _panel_by_title_contains(board, "Deviation")
    expr = _panel_exprs(panel)[0]
    # references the Task-9 z-score series, broken out per reason_family (by id),
    # and instance-deduped like every other value query on the board.
    assert "swift_segment_fail_deviation" in expr
    assert 'dim="reason_family"' in expr
    assert "by (id)" in expr
    assert expr.startswith("max by (id)")


def test_deviation_panel_sets_a_red_threshold_at_z_ge_3() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    panel = _panel_by_title_contains(board, "Deviation")
    steps = _steps(panel)
    # a spike (z >= 3) must color red — the whole point of the exceptional-event
    # register: a three-sigma family jump can never read as a benign green bar.
    red = next(s for s in steps if s["color"] == "red")
    assert red["value"] == 3
    # the coloring is driven by the thresholds, not the classic palette.
    assert panel["fieldConfig"]["defaults"]["color"]["mode"] == "thresholds"


# --- The rare-event amplification panel (rejection family) --------------------


def test_rejection_amplification_panel_isolates_the_rejection_family() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    panel = _panel_by_title_contains(board, "Rejection")
    expr = _panel_exprs(panel)[0]
    # the rejection family is rare, so its z-score amplifies a 0->N burst loud; the
    # panel is a sparkline timeseries filtered to that one family over time.
    assert panel["type"] == "timeseries"
    assert "swift_segment_fail_deviation" in expr
    assert 'id="rejection"' in expr


# --- The EWMA slow-drift trend ------------------------------------------------


def test_ewma_trend_panel_tracks_each_family_over_time() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    panel = _panel_by_title_contains(board, "EWMA")
    expr = _panel_exprs(panel)[0]
    assert panel["type"] == "timeseries"
    assert "swift_segment_fail_ewma" in expr
    assert 'dim="reason_family"' in expr
    assert "by (id)" in expr


# --- Register B placement (no overlap with the neighbouring registers) --------


def test_register_b_sits_between_register_a_and_register_c() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    reg_b_header = next(
        p for p in board["panels"] if "Register B" in p.get("options", {}).get("content", "")
    )
    reg_c_header = next(
        p for p in board["panels"] if "Register C" in p.get("options", {}).get("content", "")
    )
    reg_a_last = _panel_by_title_contains(board, "Lifecycle latency (p95)")
    # A ends, then B, then C — monotonic by y, no band overlap.
    a_bottom = reg_a_last["gridPos"]["y"] + reg_a_last["gridPos"]["h"]
    assert reg_b_header["gridPos"]["y"] >= a_bottom
    assert reg_c_header["gridPos"]["y"] > reg_b_header["gridPos"]["y"]


def test_register_b_panels_do_not_overlap_each_other() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    ewma = _panel_by_title_contains(board, "EWMA")
    deviation = _panel_by_title_contains(board, "Deviation")
    # the full-width EWMA trend sits below the deviation/amplification row.
    assert ewma["gridPos"]["y"] >= deviation["gridPos"]["y"] + deviation["gridPos"]["h"]
