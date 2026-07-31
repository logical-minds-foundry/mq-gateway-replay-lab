"""Tests for the ``gwlab-collector`` entry-point stub (epic #65, T1).

The T1 skeleton is a pure argparse + print ``main`` — no MQ client libraries and
no bound socket — so it is unit-testable directly (unlike the box-only
``serve_http``). These tests pin the resolved configuration the stub prints and
its defaults; all values are invented and anonymised (``QMX`` / ``host(1414)``).
"""

import pytest

from gwlab.swift.stream import serve_collector


def test_main_prints_resolved_config_without_tls(capsys: pytest.CaptureFixture[str]) -> None:
    """The stub echoes qmgr/conn/channel/port and reports TLS off by default."""
    serve_collector.main(
        ["--qmgr", "QMX", "--conn", "host(1414)", "--channel", "MON.SVRCONN", "--port", "9111"]
    )
    out = capsys.readouterr().out
    assert "gwlab-collector: QMX via host(1414)" in out
    assert "channel=MON.SVRCONN" in out
    assert "on :9111" in out
    assert "tls=off" in out
    assert "horizon=3d" in out


def test_main_defaults_channel_port_and_horizon(capsys: pytest.CaptureFixture[str]) -> None:
    """Only qmgr and conn are required; channel/port/horizon carry the collector defaults."""
    serve_collector.main(["--qmgr", "QMX", "--conn", "host(1414)"])
    out = capsys.readouterr().out
    assert "channel=MON.SVRCONN" in out
    assert "on :9111" in out
    assert "horizon=3d" in out


def test_main_reports_the_configured_tls_cipher(capsys: pytest.CaptureFixture[str]) -> None:
    """Passing a cipher flips the printed TLS field on (the other ternary branch)."""
    serve_collector.main(
        [
            "--qmgr",
            "QMX",
            "--conn",
            "host(1414)",
            "--tls-cipher",
            "ANY_TLS13_OR_HIGHER",
            "--key-repo",
            "/var/mqm/ssl/mon-client/key",
            "--cert-label",
            "mon-client",
            "--horizon",
            "5",
        ]
    )
    out = capsys.readouterr().out
    assert "tls=ANY_TLS13_OR_HIGHER" in out
    assert "horizon=5d" in out


def test_main_requires_qmgr_and_conn() -> None:
    """The required connection args make an argument-less invocation exit non-zero."""
    with pytest.raises(SystemExit) as excinfo:
        serve_collector.main([])
    assert excinfo.value.code != 0
