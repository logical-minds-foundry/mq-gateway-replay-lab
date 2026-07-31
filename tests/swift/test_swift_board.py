"""Tests for the SWIFT settlement Grafana board generator."""

from __future__ import annotations

from gwlab.swift.board import build_settlement_board


def _exprs(board: dict) -> list[str]:
    return [t["expr"] for panel in board["panels"] for t in panel.get("targets", [])]


def _titles(board: dict) -> list[str]:
    return [panel.get("title", "") for panel in board["panels"]]


def _panel_by_title(board: dict, title: str) -> dict:
    return next(p for p in board["panels"] if p.get("title") == title)


def test_board_identity_and_shape() -> None:
    board = build_settlement_board("NHAUAPP")
    assert board["uid"] == "gwlab-swift-settlement"
    assert "NHAUAPP" in board["title"]
    assert board["timezone"] == "utc"
    # 1 header + 6 KPI stats + 2 bargauges + 2 latency timeseries, then the
    # Load & IO section: 1 section header + 1 scan-cost stat + 3 timeseries.
    assert len(board["panels"]) == 16
    panel_types = [p["type"] for p in board["panels"]]
    assert panel_types.count("stat") == 7
    assert panel_types.count("bargauge") == 2
    assert panel_types.count("timeseries") == 5
    assert panel_types.count("text") == 2


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


def test_load_io_section_titles_and_exprs_present() -> None:
    board = build_settlement_board("NHAUAPP")
    titles = _titles(board)
    exprs = _exprs(board)
    # Scan cost — our own self-timing metric (#121), as a stat and a timeseries.
    assert "Scan cost (last pass)" in titles
    assert "Scan cost (per-pass collect wall-clock)" in titles
    assert "swift_settlement_collect_seconds" in exprs
    # Audit + transmission queue depth — the lab's IBM MQ exporter series.
    assert "Audit + transmission queue depth" in titles
    assert 'ibmmq_queue_depth{queue=~"GW.SEC.*"}' in exprs
    # Disk IO — the node exporter's disk-contention signal.
    assert "Disk IO utilisation" in titles
    assert "rate(node_disk_io_time_seconds_total[5m])" in exprs


def test_scan_cost_stat_is_threshold_coloured_towards_red() -> None:
    board = build_settlement_board("NHAUAPP")
    stat = _panel_by_title(board, "Scan cost (last pass)")
    defaults = stat["fieldConfig"]["defaults"]
    assert defaults["unit"] == "s"
    assert defaults["color"] == {"mode": "thresholds"}
    steps = defaults["thresholds"]["steps"]
    # base green (open-ended), then amber past target, red approaching the 30s scrape.
    assert steps[0] == {"color": "green", "value": None}
    assert steps[-1]["color"] == "red"
    assert [s["value"] for s in steps] == [None, 3, 15]


def test_load_io_section_uses_the_configured_datasource() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="prom-xyz")
    for title in (
        "Scan cost (last pass)",
        "Audit + transmission queue depth",
        "Disk IO utilisation",
    ):
        panel = _panel_by_title(board, title)
        assert panel["datasource"]["uid"] == "prom-xyz"
        assert {t["datasource"]["uid"] for t in panel["targets"]} == {"prom-xyz"}


def test_new_panels_do_not_overlap_the_existing_ones() -> None:
    board = build_settlement_board("NHAUAPP")
    # the last pre-existing row (latency timeseries) sits at y=16 h=8 → bottom 24;
    # every Load & IO panel must start at or below that.
    for title in (
        "Scan cost (last pass)",
        "Scan cost (per-pass collect wall-clock)",
        "Audit + transmission queue depth",
        "Disk IO utilisation",
    ):
        assert _panel_by_title(board, title)["gridPos"]["y"] >= 24


def test_datasource_uid_is_parameterised() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="prom-123")
    uids = {t["datasource"]["uid"] for p in board["panels"] for t in p.get("targets", [])}
    assert uids == {"prom-123"}
    # default is a Grafana variable
    default = build_settlement_board("NHAUAPP")
    default_uids = {t["datasource"]["uid"] for p in default["panels"] for t in p.get("targets", [])}
    assert default_uids == {"${DS_PROMETHEUS}"}
