"""Tests for the ``gwlab swift`` subcommand group and its Prometheus exposition.

Every subcommand is driven through ``cli.main`` against a fake-messaging-backed
audit set (no live queue manager): ``report`` / ``board`` reach their box-only
connection through the ``serve_http.connect`` seam, which is faked here, and
``serve`` reaches its socket-binding loop through ``serve_http.run``, likewise
faked. The pure exposition fold (``render_exposition``) is unit-tested directly.

All data is invented and anonymised (``GWSEC…`` references); ``NOW`` is a fixed
injected instant, so nothing reads the wall clock.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gwlab import cli
from gwlab.fake_messaging import FakeMessaging
from gwlab.swift import cliswift
from gwlab.swift.collect import collect
from gwlab.swift.observe import Metrics, metrics
from gwlab.swift.simulation import run_window
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

#: A fixed reporting instant; ``start`` sits exactly a horizon before it so the
#: demonstrator's offset-0 instruction is both in-window and (if non-terminal) aged
#: out — mirroring the simulation driver's proven boundary.
NOW = datetime(2026, 7, 29, 17, 0, tzinfo=UTC)
_HORIZON = Horizon(days=3)
_CONN_ARGS = ["--qmgr", "QMX", "--conn", "host(1414)", "--channel", "APP.SVRCONN"]


def _seeded_fake() -> FakeMessaging:
    """A FakeMessaging seeded with the demonstrator settlement window."""
    fake = FakeMessaging()
    start = NOW - timedelta(days=_HORIZON.days)
    run_window(fake, SwiftQueueSet(), _HORIZON, cliswift._demo_scenario(), start, NOW)
    return fake


def _seeded_metrics() -> Metrics:
    """Fold the seeded audit set into a settlement Metrics snapshot at NOW."""
    report = collect(_seeded_fake(), SwiftQueueSet(), NOW, _HORIZON)
    return metrics(report, NOW)


def _use_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeMessaging) -> None:
    """Point the connection seam at ``fake`` and pin ``now`` to NOW."""
    from gwlab.swift import serve_http

    monkeypatch.setattr(serve_http, "connect", lambda _args: fake)
    monkeypatch.setattr(cliswift, "_now", lambda: NOW)


# --- the pure Prometheus exposition ------------------------------------------


def test_render_exposition_emits_distribution_anomalies_and_scan_lines() -> None:
    out = cliswift.render_exposition(_seeded_metrics())
    assert 'swift_settlement_state{state="settled"} 1' in out
    assert 'swift_settlement_state{state="rejected"} 1' in out
    assert 'swift_settlement_state{state="matched"} 0' in out
    assert 'swift_settlement_anomalies{kind="orphan"} 1' in out
    assert "swift_settlement_anomalies_total 1" in out
    assert any(line.startswith("swift_settlement_scanned ") for line in out.splitlines())
    # latencies are present for the settled instruction's hops
    assert 'swift_settlement_hop_mean_seconds{hop="' in out
    assert 'swift_settlement_hop_p95_seconds{hop="' in out


def test_render_exposition_has_help_and_type_for_every_metric() -> None:
    out = cliswift.render_exposition(_seeded_metrics())
    for name in (
        "swift_settlement_state",
        "swift_settlement_anomalies",
        "swift_settlement_anomalies_total",
        "swift_settlement_aging",
        "swift_settlement_aged_out",
        "swift_settlement_scanned",
        "swift_settlement_hop_mean_seconds",
        "swift_settlement_hop_p95_seconds",
    ):
        assert f"# HELP {name} " in out
        assert f"# TYPE {name} gauge" in out
    assert out.endswith("\n")


def test_render_exposition_on_empty_metrics_has_zeroed_states_and_no_hops() -> None:
    empty = metrics(collect(FakeMessaging(), SwiftQueueSet(), NOW, _HORIZON), NOW)
    out = cliswift.render_exposition(empty)
    assert 'swift_settlement_state{state="instructed"} 0' in out
    assert "swift_settlement_scanned 0" in out
    assert "swift_settlement_hop_mean_seconds{" not in out  # no completed hops


# --- the exporter self-timing metric -----------------------------------------


def test_timed_metrics_measures_the_pass_with_an_injected_monotonic() -> None:
    # scripted monotonic: 0.5s elapses between the two reads bracketing the pass
    ticks = iter([100.0, 100.5])
    snapshot, collect_seconds = cliswift.timed_metrics(
        _seeded_fake(), SwiftQueueSet(), NOW, _HORIZON, lambda: next(ticks)
    )
    assert collect_seconds == 0.5
    assert snapshot.scanned > 0  # the seeded audit set was actually folded


def test_render_exposition_emits_collect_seconds_when_timed() -> None:
    out = cliswift.render_exposition(_seeded_metrics(), collect_seconds=0.5)
    assert "# TYPE swift_settlement_collect_seconds gauge" in out
    assert "swift_settlement_collect_seconds 0.5" in out


def test_render_exposition_omits_collect_seconds_when_untimed() -> None:
    out = cliswift.render_exposition(_seeded_metrics())
    assert "swift_settlement_collect_seconds" not in out


# --- report -------------------------------------------------------------------


def test_report_prints_the_state_distribution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_fake(monkeypatch, _seeded_fake())
    rc = cli.main(["swift", "report", *_CONN_ARGS])
    assert rc == 0
    out = capsys.readouterr().out
    assert "settled: 1" in out
    assert "rejected: 1" in out
    assert "failed: 1" in out
    assert "matched: 0" in out
    assert "orphan: 1" in out


def test_report_json_emits_a_parseable_distribution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    _use_fake(monkeypatch, _seeded_fake())
    rc = cli.main(["swift", "report", "--json", *_CONN_ARGS])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["distribution"]["settled"] == 1
    assert payload["anomalies_by_kind"]["orphan"] == 1
    assert payload["anomalies_total"] == 1


# --- board --------------------------------------------------------------------


def test_board_writes_html_with_state_labels_to_a_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _use_fake(monkeypatch, _seeded_fake())
    out_file = tmp_path / "board.html"
    rc = cli.main(["swift", "board", "--output", str(out_file), *_CONN_ARGS])
    assert rc == 0
    page = out_file.read_text(encoding="utf-8")
    assert "<!doctype html>" in page
    assert "INSTRUCTED" in page
    assert "SETTLED" in page
    assert "wrote" in capsys.readouterr().err


def test_board_emits_html_to_stdout_when_no_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_fake(monkeypatch, _seeded_fake())
    rc = cli.main(["swift", "board", *_CONN_ARGS])
    assert rc == 0
    out = capsys.readouterr().out
    assert "<!doctype html>" in out
    assert "SWIFT settlement lifecycle" in out


# --- simulate -----------------------------------------------------------------


def test_simulate_runs_the_demo_scenario_and_prints_the_distribution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cliswift, "_now", lambda: NOW)
    rc = cli.main(["swift", "simulate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "settled: 1" in out
    assert "orphan: 1" in out


# --- serve --------------------------------------------------------------------


def test_serve_invokes_the_box_only_serve_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from gwlab.swift import serve_http

    calls: list[object] = []
    monkeypatch.setattr(serve_http, "run", calls.append)
    rc = cli.main(["swift", "serve", *_CONN_ARGS])
    assert rc == 0
    assert len(calls) == 1


# --- argument parsing & port distinctness ------------------------------------


def test_subcommands_route_to_their_handlers() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["swift", "simulate"]).handler is cliswift.simulate_cmd
    assert parser.parse_args(["swift", "report", *_CONN_ARGS]).handler is cliswift.report_cmd
    assert parser.parse_args(["swift", "board", *_CONN_ARGS]).handler is cliswift.board_cmd
    assert parser.parse_args(["swift", "serve", *_CONN_ARGS]).handler is cliswift.serve_cmd


def test_serve_default_port_is_distinct_from_v1() -> None:
    parser = cli.build_parser()
    port = parser.parse_args(["swift", "serve", *_CONN_ARGS]).port
    assert port == cliswift.DEFAULT_PORT
    assert cliswift.DEFAULT_PORT != cliswift.V1_PORT
    assert cliswift.V1_PORT == 9109  # v1's exporter default (gwlab.exporter_server)


def test_compression_flag_defaults_to_real_time_and_parses() -> None:
    parser = cli.build_parser()
    assert parser.parse_args(["swift", "report", *_CONN_ARGS]).compression == 1.0
    args = parser.parse_args(["swift", "report", "--compression", "60", *_CONN_ARGS])
    assert args.compression == 60.0


def test_missing_swift_subcommand_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["swift"])


def test_now_seam_returns_a_utc_instant() -> None:
    assert cliswift._now().tzinfo is UTC
