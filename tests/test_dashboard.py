"""Tests for the code-templated, QM-agnostic dashboard generator."""

import json

import pytest

from gwlab import cli
from gwlab.config import QueueSet
from gwlab.dashboard import build_dashboard


def _exprs(dashboard: dict) -> list[str]:
    return [t["expr"] for p in dashboard["panels"] for t in p["targets"]]


def test_qmgr_is_a_parameter_never_hardcoded() -> None:
    one = build_dashboard("SOMEQM", "CPB")
    assert any('qmgr="SOMEQM"' in e for e in _exprs(one))
    # a different QM produces different queries — nothing is baked in
    other = build_dashboard("OTHERQM", "CPB")
    assert any('qmgr="OTHERQM"' in e for e in _exprs(other))
    assert not any("SOMEQM" in e for e in _exprs(other))


def test_counterparty_drives_queue_names_and_identity() -> None:
    qs = QueueSet.for_counterparty("CPA")
    dashboard = build_dashboard("QM1", "CPA")
    assert dashboard["uid"] == "gw-cpa"
    assert "CPA" in dashboard["title"]
    blob = " ".join(_exprs(dashboard))
    assert qs.send_archive in blob
    assert qs.reply_archive in blob


def test_has_the_expected_rich_panels() -> None:
    titles = [p["title"] for p in build_dashboard("QM1", "CPB")["panels"]]
    assert "Sent vs Confirmed (archive depth)" in titles
    assert any("rate" in t.lower() for t in titles)  # first derivative
    assert any("backlog" in t.lower() for t in titles)
    assert any("age" in t.lower() for t in titles)
    assert any("CAPEXPRY" in t for t in titles)


def test_datasource_uid_flows_through() -> None:
    dashboard = build_dashboard("QM1", "CPB", ds_uid="my-prom")
    assert dashboard["panels"][0]["datasource"]["uid"] == "my-prom"
    assert dashboard["panels"][0]["targets"][0]["datasource"]["uid"] == "my-prom"


def test_cli_dashboard_prints_valid_targeted_json(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["--counterparty", "CPB", "dashboard", "--qmgr", "NHAUAPP"])
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["uid"] == "gw-cpb"
    assert any('qmgr="NHAUAPP"' in t["expr"] for p in parsed["panels"] for t in p["targets"])
