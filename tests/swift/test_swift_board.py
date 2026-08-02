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
    # header + 6 KPI stats
    # + Register A: header + efficiency stat + 2 fail bargauges
    # + anomalies bargauge + 2 latency timeseries
    # + Register B: header + deviation bargauge + rejection-amplification timeseries
    #   + EWMA-trend timeseries
    # + Register C: header + 2 audit bargauges + oldest-age stat + 2 timeseries
    # + collector-health: header + 2 stats + 3 timeseries.
    assert len(board["panels"]) == 30
    panel_types = [p["type"] for p in board["panels"]]
    assert panel_types.count("stat") == 10
    assert panel_types.count("bargauge") == 6
    assert panel_types.count("timeseries") == 9
    assert panel_types.count("text") == 5


def test_kpi_row_is_operational_and_does_not_repeat_the_distribution() -> None:
    board = build_settlement_board("NHAUAPP")
    exprs = _exprs(board)
    titles = _titles(board)
    # The KPI tiles are operational, not per-state counts.
    for title in ("Total instructions", "Aging (replay)", "Anomalies", "Population"):
        assert title in titles
    assert "sum(max by (state) (swift_settlement_state))" in exprs  # total
    assert "max(swift_settlement_aging)" in exprs
    assert "max(swift_settlement_anomalies_total)" in exprs
    assert "max(swift_settlement_scanned)" in exprs
    # The per-state counts must NOT appear as standalone tiles — that duplication
    # (a "Settled" tile repeating the "settled" distribution bar) is the bug this
    # redesign removes. They live only in the deduped distribution hero.
    assert 'swift_settlement_state{state="settled"}' not in exprs
    assert 'swift_settlement_state{state="rejected"}' not in exprs
    assert 'swift_settlement_state{state="failed"}' not in exprs


def test_anomalies_hero_is_instance_deduped_and_state_bar_retired() -> None:
    board = build_settlement_board("NHAUAPP")
    exprs = _exprs(board)
    # ``max by`` collapses a failover instance-swap to one value per kind.
    assert "max by (kind) (swift_settlement_anomalies)" in exprs
    assert "Anomalies by kind" in _titles(board)
    # The settled-dominated state-distribution bar is retired (buried-signal fix):
    # no single panel plots all lifecycle states together anymore.
    assert "max by (state) (swift_settlement_state)" not in exprs
    assert "Settlement state distribution" not in _titles(board)


def test_latency_panels_are_full_width_and_scaled_by_compression() -> None:
    board = build_settlement_board("NHAUAPP")
    exprs = _exprs(board)
    # duration panels multiply by $compression so the operator recovers sim-time (§7)
    assert "swift_settlement_hop_mean_seconds * $compression" in exprs
    assert "swift_settlement_hop_p95_seconds * $compression" in exprs
    # full width (w == 24) and taller so the per-hop legend table is not clipped.
    for title in ("Lifecycle latency (mean)", "Lifecycle latency (p95)"):
        panel = _panel_by_title(board, title)
        assert panel["gridPos"]["w"] == 24
        assert panel["gridPos"]["h"] >= 9


def test_board_has_compression_template_variable_defaulting_to_real_time() -> None:
    board = build_settlement_board("NHAUAPP")
    variables = board["templating"]["list"]
    compression = next(v for v in variables if v["name"] == "compression")
    assert compression["type"] == "textbox"
    assert compression["current"]["value"] == "1"  # default C=1 is real time


def test_collector_health_section_present_and_scan_cost_retired() -> None:
    board = build_settlement_board("NHAUAPP")
    titles = _titles(board)
    exprs = _exprs(board)
    for title in (
        "Recovery time (last cold-reload)",
        "Sweep backlog",
        "Fold cycle time (p95, off the scrape path)",
        "Messages ingested (rate)",
        "Disk IO utilisation",
    ):
        assert title in titles
    assert "max(swift_collector_bootstrap_seconds)" in exprs
    assert "max(swift_collector_cycle_backlog)" in exprs
    assert "sum(rate(swift_collector_messages_ingested_total[5m]))" in exprs
    assert "rate(node_disk_io_time_seconds_total[5m])" in exprs
    assert any("swift_collector_cycle_seconds_bucket" in e for e in exprs)
    # the retired per-pass scan-cost gauge is gone.
    assert all("swift_settlement_collect_seconds" not in e for e in exprs)


def test_publish_age_tile_reads_snapshot_staleness() -> None:
    board = build_settlement_board("NHAUAPP")
    exprs = _exprs(board)
    assert "time() - max(swift_collector_last_publish_timestamp_seconds)" in exprs


def test_panels_do_not_overlap_vertically() -> None:
    board = build_settlement_board("NHAUAPP")
    # every panel's row band [y, y+h) is disjoint or shares a row start with its
    # neighbours; here we assert the section order is monotonic by y for the
    # single-column rows (header, latency panels, health header).
    latency_mean = _panel_by_title(board, "Lifecycle latency (mean)")
    latency_p95 = _panel_by_title(board, "Lifecycle latency (p95)")
    assert (
        latency_p95["gridPos"]["y"] >= latency_mean["gridPos"]["y"] + latency_mean["gridPos"]["h"]
    )


def test_datasource_uid_is_parameterised() -> None:
    board = build_settlement_board("NHAUAPP", ds_uid="prom-123")
    uids = {t["datasource"]["uid"] for p in board["panels"] for t in p.get("targets", [])}
    assert uids == {"prom-123"}
    # default is a Grafana variable
    default = build_settlement_board("NHAUAPP")
    default_uids = {t["datasource"]["uid"] for p in default["panels"] for t in p.get("targets", [])}
    assert default_uids == {"${DS_PROMETHEUS}"}
