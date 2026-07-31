"""Tests for the SWIFT settlement Grafana board generator."""

from __future__ import annotations

from gwlab.swift.board import build_settlement_board


def _exprs(board: dict) -> list[str]:
    return [t["expr"] for panel in board["panels"] for t in panel.get("targets", [])]


def _titles(board: dict) -> list[str]:
    return [panel.get("title", "") for panel in board["panels"]]


def test_board_identity_and_shape() -> None:
    board = build_settlement_board("NHAUAPP")
    assert board["uid"] == "gwlab-swift-settlement"
    assert "NHAUAPP" in board["title"]
    assert board["timezone"] == "utc"
    # 1 header text + 6 KPI stats + 2 bargauges + 2 latency timeseries
    assert len(board["panels"]) == 11
    panel_types = [p["type"] for p in board["panels"]]
    assert panel_types.count("stat") == 6
    assert panel_types.count("bargauge") == 2
    assert panel_types.count("timeseries") == 2
    assert panel_types.count("text") == 1


def test_kpi_and_distribution_exprs_present() -> None:
    exprs = _exprs(build_settlement_board("NHAUAPP"))
    assert 'swift_settlement_state{state="settled"}' in exprs
    assert 'sum(swift_settlement_state{state=~"instructed|acknowledged|matched"})' in exprs
    assert 'swift_settlement_state{state="rejected"}' in exprs
    assert 'swift_settlement_state{state="failed"}' in exprs
    assert "swift_settlement_anomalies_total" in exprs
    assert "swift_settlement_scanned" in exprs
    assert "swift_settlement_state" in exprs  # the distribution hero
    assert "swift_settlement_anomalies" in exprs  # anomalies by kind


def test_latency_panels_query_both_hops_scaled_by_compression() -> None:
    board = build_settlement_board("NHAUAPP")
    exprs = _exprs(board)
    # duration panels multiply by $compression so the operator recovers sim-time (§7)
    assert "swift_settlement_hop_mean_seconds * $compression" in exprs
    assert "swift_settlement_hop_p95_seconds * $compression" in exprs
    assert "Lifecycle latency (mean)" in _titles(board)
    assert "Lifecycle latency (p95)" in _titles(board)


def test_board_has_compression_template_variable_defaulting_to_real_time() -> None:
    board = build_settlement_board("NHAUAPP")
    variables = board["templating"]["list"]
    compression = next(v for v in variables if v["name"] == "compression")
    assert compression["type"] == "textbox"
    assert compression["current"]["value"] == "1"  # default C=1 is real time


def test_datasource_uid_is_parameterised() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="prom-123")
    uids = {t["datasource"]["uid"] for p in board["panels"] for t in p.get("targets", [])}
    assert uids == {"prom-123"}
    # default is a Grafana variable
    default = build_settlement_board("NHAUAPP")
    default_uids = {t["datasource"]["uid"] for p in default["panels"] for t in p.get("targets", [])}
    assert default_uids == {"${DS_PROMETHEUS}"}
