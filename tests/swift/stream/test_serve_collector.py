"""Tests for the streaming settlement collector's box-only wiring, boundary-mocked.

The collector needs pymqi, binds a socket, reads the real clock and loops
forever. Here the pymqi import is monkeypatched (``connect``), ``HTTPServer`` and
``threading.Thread`` are replaced so nothing binds or spawns, ``sweep`` is
scripted so the "more work" and "caught up -> sleep" loop branches both run, and
``time.sleep`` raises a sentinel to break the loop. Every pure piece it wires
(state, snapshot, stats, render) runs for real.
"""

from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

from gwlab.fake_messaging import FakeMessaging
from gwlab.swift.stream import serve_collector
from gwlab.swift.stream.snapshot import SnapshotHolder
from gwlab.swift.stream.sweep import SweepResult

_CONN = argparse.Namespace(
    qmgr="NHAUAPP",
    conn="host(1414)",
    channel="MON.SVRCONN",
    tls_cipher=None,
    key_repo=None,
    cert_label=None,
)


class _StopError(Exception):
    """Sentinel raised from a patched ``time.sleep`` to break the infinite loop."""


class _RecordingHandler:
    """Captures a ``do_GET`` write against an in-memory ``wfile`` (no socket)."""

    def __init__(self, handler_cls: Any, path: str) -> None:
        self._inst = handler_cls.__new__(handler_cls)
        self._inst.path = path
        self._inst.wfile = io.BytesIO()
        self.status: int | None = None
        self.ended = False
        self._inst.send_response = lambda code: setattr(self, "status", code)
        self._inst.send_header = lambda _k, _v: None
        self._inst.end_headers = lambda: setattr(self, "ended", True)

    def get(self) -> None:
        self._inst.do_GET()

    @property
    def body(self) -> bytes:
        return self._inst.wfile.getvalue()

    @property
    def inst(self) -> Any:
        return self._inst


# --- connect() ----------------------------------------------------------------


def test_connect_builds_pymqi_messaging_from_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakePyMqi:
        def __init__(self, qmgr: str, conn: str, channel: str, **kw: Any) -> None:
            captured.update(qmgr=qmgr, conn=conn, channel=channel, **kw)

    monkeypatch.setattr("gwlab.pymqi_messaging.PyMqiMessaging", _FakePyMqi)
    result = serve_collector.connect(_CONN)
    assert isinstance(result, _FakePyMqi)
    assert captured["qmgr"] == "NHAUAPP"
    assert captured["channel"] == "MON.SVRCONN"


# --- the request handler serves the pre-computed snapshot ---------------------


def _handler_for(path: str) -> _RecordingHandler:
    holder = SnapshotHolder("# published-snapshot")
    return _RecordingHandler(serve_collector._make_handler(holder), path)


def test_do_get_metrics_returns_the_latest_snapshot() -> None:
    rec = _handler_for("/metrics")
    rec.get()
    assert rec.status == 200
    assert rec.body == b"# published-snapshot"


def test_do_get_root_path_also_serves_the_snapshot() -> None:
    rec = _handler_for("/")
    rec.get()
    assert rec.status == 200
    assert rec.body == b"# published-snapshot"


def test_do_get_unknown_path_returns_404_with_no_body() -> None:
    rec = _handler_for("/nope")
    rec.get()
    assert rec.status == 404
    assert rec.ended
    assert rec.body == b""


def test_log_message_is_silenced() -> None:
    rec = _handler_for("/metrics")
    assert rec.inst.log_message("%s", "anything") is None


# --- _serve binds and serves without a real socket ---------------------------


def test_serve_constructs_the_server_and_serves_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    built: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, address: tuple[str, int], handler: type) -> None:
            built["address"] = address

        def serve_forever(self) -> None:
            built["served"] = True

    monkeypatch.setattr(serve_collector, "HTTPServer", _FakeServer)
    serve_collector._serve(9111, SnapshotHolder("# x"))
    assert built["address"] == ("", 9111)
    assert built["served"] is True


# --- run() sweeps, publishes, and paces itself --------------------------------


def test_run_sweeps_until_caught_up_then_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_collector, "connect", lambda _args: FakeMessaging())

    thread: dict[str, Any] = {}

    class _FakeThread:
        def __init__(self, *, target: Any, args: tuple[Any, ...], daemon: bool) -> None:
            thread["target"] = target
            thread["args"] = args
            thread["daemon"] = daemon

        def start(self) -> None:
            thread["started"] = True

    monkeypatch.setattr(serve_collector.threading, "Thread", _FakeThread)

    # first pass reports more work (no sleep -> loop again); second is caught up
    results = iter([SweepResult(ingested=5, more=True), SweepResult(ingested=0, more=False)])
    calls = {"sweep": 0}

    def _fake_sweep(*_a: Any, **_k: Any) -> SweepResult:
        calls["sweep"] += 1
        return next(results)

    monkeypatch.setattr(serve_collector, "sweep", _fake_sweep)

    def _boom(_seconds: float) -> None:
        raise _StopError

    monkeypatch.setattr(serve_collector.time, "sleep", _boom)

    args = argparse.Namespace(
        port=9111, horizon=3, batch_size=20000, poll_seconds=5.0, **vars(_CONN)
    )
    with pytest.raises(_StopError):
        serve_collector.run(args)

    assert calls["sweep"] == 2  # both the "more" and the "caught up" laps ran
    assert thread["target"] is serve_collector._serve
    assert thread["args"][0] == 9111
    assert thread["daemon"] is True
    assert thread["started"] is True


# --- main() -------------------------------------------------------------------


def test_main_parses_defaults_and_dispatches_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(serve_collector, "run", lambda args: seen.update(vars(args)))
    serve_collector.main(["--qmgr", "NHAUAPP", "--conn", "host(1414)"])
    assert seen["qmgr"] == "NHAUAPP"
    assert seen["channel"] == "MON.SVRCONN"
    assert seen["port"] == 9111
    assert seen["horizon"] == 3
    assert seen["batch_size"] == 20000
    assert seen["poll_seconds"] == 5.0


def test_main_requires_qmgr_and_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_collector, "run", lambda args: None)
    with pytest.raises(SystemExit):
        serve_collector.main([])
