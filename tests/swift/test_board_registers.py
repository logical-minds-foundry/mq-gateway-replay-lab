"""Tests for the three-register board: Register A (normal-day) + Register C (infra).

These lock the Task-7 rework of :func:`gwlab.swift.board.build_settlement_board`:
a settlement-efficiency headline and per-dimension fail breakdowns (Register A),
audit-queue infra health (Register C), and the retirement of the settled-dominated
state-distribution bar (the buried-signal antipattern the epic removes).
"""

from __future__ import annotations

from gwlab.swift.board import build_settlement_board


def _exprs(board: dict) -> list[str]:
    return [t["expr"] for panel in board["panels"] for t in panel.get("targets", [])]


def _titles(board: dict) -> list[str]:
    return [panel.get("title", "") for panel in board["panels"]]


def _panel_exprs(panel: dict) -> list[str]:
    return [t["expr"] for t in panel.get("targets", [])]


# --- Register A: normal-day efficiency headline + fail breakdowns --------------


def test_board_has_efficiency_headline_and_infra_register() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    titles = _titles(board)
    assert any("Efficiency" in t for t in titles)
    assert any("Audit queue" in t or "Fill" in t for t in titles)


def test_efficiency_is_settled_over_all_segments() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    exprs = _exprs(board)
    efficiency = next(e for e in exprs if "settled" in e and "swift_settlement_segment_total" in e)
    # settled numerator over the whole population — a ratio, not a raw settled count.
    assert 'swift_settlement_segment_total{state="settled"}' in efficiency
    assert "/" in efficiency


def test_fails_are_broken_out_by_reason_family_and_msg_type_on_their_own_scale() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    by_family = next(p for p in board["panels"] if p.get("title") == "Fails by reason family")
    by_msgtype = next(p for p in board["panels"] if p.get("title") == "Fails by message type")
    fam_expr = _panel_exprs(by_family)[0]
    msg_expr = _panel_exprs(by_msgtype)[0]
    assert "by (reason_family)" in fam_expr
    assert "by (msg_type)" in msg_expr
    # Each fail panel excludes the settled majority, so fails are never dwarfed by
    # (nor co-plotted against) settled volume — the whole point of Register A.
    for expr in (fam_expr, msg_expr):
        assert "failed" in expr
        assert 'state="settled"' not in expr


# --- Register C: audit-queue infra health -------------------------------------


def test_register_c_covers_depth_fill_age_and_expiry_window_stability() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    exprs = _exprs(board)
    assert "max by (queue) (swift_audit_queue_depth)" in exprs
    assert "max by (queue) (swift_audit_queue_fill_ratio)" in exprs
    assert "max(swift_audit_oldest_age_seconds)" in exprs
    # The expiry-window-stability panel is a timeseries on the depth series over time.
    stability = next(
        p
        for p in board["panels"]
        if p["type"] == "timeseries" and "swift_audit_queue_depth" in _panel_exprs(p)[0]
    )
    assert stability["type"] == "timeseries"


def test_register_c_disk_log_is_mq_exporter_filesystem_not_node_disk_io() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    consumption = next(p for p in board["panels"] if p.get("title", "").startswith("Audit disk"))
    joined = " ".join(_panel_exprs(consumption))
    # sourced from the IBM MQ exporter's filesystem-in-use series (footprint), and
    # scoped to this queue manager — NOT the node-exporter disk-IO contention series.
    assert "ibmmq_qmgr" in joined
    assert "file_system" in joined
    assert 'qmgr="NHAUAPP"' in joined
    assert "node_disk" not in joined


# --- Retirement of the settled-dominated bar ----------------------------------


def test_board_retires_settled_dominated_bar() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    exprs = _exprs(board)
    # no single panel plots all states together (the buried-signal antipattern)
    assert "max by (state) (swift_settlement_state)" not in exprs
    assert "Settlement state distribution" not in _titles(board)
