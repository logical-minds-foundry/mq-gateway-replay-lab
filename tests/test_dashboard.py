"""Tests for the code-templated, QM-agnostic, mockup-faithful dashboard generator."""

import json

import pytest

from gwlab import cli
from gwlab.config import QueueSet
from gwlab.dashboard import build_dashboard


def _exprs(dashboard: dict) -> list[str]:
    return [t["expr"] for p in dashboard["panels"] for t in p.get("targets", [])]


def _panel(dashboard: dict, ptype: str) -> list[dict]:
    return [p for p in dashboard["panels"] if p["type"] == ptype]


def test_qmgr_is_a_parameter_never_hardcoded() -> None:
    one = build_dashboard("SOMEQM", "CPB")
    assert any('qmgr="SOMEQM"' in e for e in _exprs(one))
    # a different QM produces different queries — nothing is baked in
    other = build_dashboard("OTHERQM", "CPB")
    assert any('qmgr="OTHERQM"' in e for e in _exprs(other))
    assert not any("SOMEQM" in e for e in _exprs(other))


def test_uid_and_title_identify_the_qm_and_counterparty() -> None:
    dashboard = build_dashboard("NHAUAPP", "CPB")
    assert dashboard["uid"] == "gw-nhauapp-cpb"
    assert "CPB" in dashboard["title"]
    assert "NHAUAPP" in dashboard["title"]


def test_base_layer_reads_the_standard_ibmmq_exporter() -> None:
    qs = QueueSet.for_counterparty("CPB")
    exprs = _exprs(build_dashboard("QM1", "CPB"))
    blob = " ".join(exprs)
    assert any("ibmmq_qmgr_status" in e for e in exprs)  # QM health
    assert f'queue="{qs.send}"' in blob  # live transmit depth, not the archive
    assert f'queue="{qs.reply}"' in blob  # live reply depth


def test_extension_reads_the_gwlab_reconcile_exporter() -> None:
    exprs = _exprs(build_dashboard("QM1", "CPB"))
    blob = " ".join(exprs)
    for metric in ("gwlab_sent", "gwlab_confirmed", "gwlab_unconfirmed", "gwlab_confirm_latency"):
        assert metric in blob
    # gwlab series are matched on qmgr (scrape-applied) AND counterparty
    assert 'counterparty="CPB"' in blob
    assert any('gwlab_sent{qmgr="QM1"' in e for e in exprs)


def test_which_trades_table_is_present() -> None:
    tables = _panel(build_dashboard("QM1", "CPB"), "table")
    assert len(tables) == 1
    table = tables[0]
    assert "gwlab_unconfirmed_message" in table["targets"][0]["expr"]
    assert table["targets"][0]["format"] == "table"
    assert table["targets"][0]["instant"] is True
    # labels become columns (which trade, when, state)
    renamed = table["transformations"][1]["options"]["renameByName"]
    assert renamed["msgid"].startswith("Correlation")
    assert "State" in renamed.values()


def test_scan_cost_panels_are_present() -> None:
    blob = " ".join(_exprs(build_dashboard("QM1", "CPB")))
    assert "gwlab_reconcile_scan_seconds" in blob
    assert "gwlab_reconcile_scanned_headers" in blob


def test_hero_and_rate_charts_are_time_series() -> None:
    titles = {p["title"] for p in _panel(build_dashboard("QM1", "CPB"), "timeseries")}
    assert any("backlog" in t.lower() for t in titles)
    assert any("rate" in t.lower() for t in titles)


def test_datasource_uid_flows_through() -> None:
    dashboard = build_dashboard("QM1", "CPB", ds_uid="my-prom")
    stat = _panel(dashboard, "stat")[0]
    assert stat["datasource"]["uid"] == "my-prom"
    assert stat["targets"][0]["datasource"]["uid"] == "my-prom"


def test_cli_dashboard_prints_valid_targeted_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["--counterparty", "CPB", "dashboard", "--qmgr", "NHAUAPP"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["uid"] == "gw-nhauapp-cpb"
    assert any(
        'qmgr="NHAUAPP"' in t["expr"] for p in parsed["panels"] for t in p.get("targets", [])
    )
