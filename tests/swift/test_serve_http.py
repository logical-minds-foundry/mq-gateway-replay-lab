"""Tests for the box-only settlement HTTP server, boundary-mocked.

``serve_http`` opens a pymqi connection and binds a socket, so on the box it is
end-to-end. Here every external edge is faked: the ``connect`` seam yields a
:class:`~gwlab.fake_messaging.FakeMessaging`, the pymqi import is monkeypatched
so no MQ client is needed, the ``HTTPServer`` is replaced so nothing binds, and
the request handler is driven against in-memory ``wfile``/routing so the
``do_GET`` path logic is exercised directly. All data is invented/anonymised.
"""

from __future__ import annotations

import argparse
import io
from typing import Any

import pytest

from gwlab.fake_messaging import FakeMessaging
from gwlab.swift import serve_http
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

_CONN = argparse.Namespace(
    qmgr="QMX",
    conn="host(1414)",
    channel="APP.SVRCONN",
    tls_cipher=None,
    key_repo=None,
    cert_label=None,
)


class _RecordingHandler:
    """Captures the header/status/body a ``do_GET`` writes without a real socket."""

    def __init__(self, handler_cls: Any, path: str) -> None:
        self._inst = handler_cls.__new__(handler_cls)
        self._inst.path = path
        self._inst.wfile = io.BytesIO()
        self.status: int | None = None
        self.headers: list[tuple[str, str]] = []
        self.ended = False
        self._inst.send_response = self._send_response
        self._inst.send_header = self._send_header
        self._inst.end_headers = self._end_headers

    def _send_response(self, code: int) -> None:
        self.status = code

    def _send_header(self, key: str, value: str) -> None:
        self.headers.append((key, value))

    def _end_headers(self) -> None:
        self.ended = True

    def get(self) -> None:
        self._inst.do_GET()

    @property
    def body(self) -> bytes:
        return self._inst.wfile.getvalue()

    @property
    def inst(self) -> Any:
        return self._inst


# --- connect() wires the parsed args into the pymqi adapter -------------------


def test_connect_builds_pymqi_messaging_from_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakePyMqi:
        def __init__(self, qmgr: str, conn: str, channel: str, **kw: Any) -> None:
            captured.update(qmgr=qmgr, conn=conn, channel=channel, **kw)

    monkeypatch.setattr("gwlab.pymqi_messaging.PyMqiMessaging", _FakePyMqi)
    result = serve_http.connect(_CONN)
    assert isinstance(result, _FakePyMqi)
    assert captured == {
        "qmgr": "QMX",
        "conn": "host(1414)",
        "channel": "APP.SVRCONN",
        "tls_cipher": None,
        "key_repo": None,
        "cert_label": None,
    }


# --- the request handler's do_GET routing ------------------------------------


def _handler_for(path: str) -> _RecordingHandler:
    handler_cls = serve_http._make_handler(FakeMessaging(), SwiftQueueSet(), Horizon(days=3))
    return _RecordingHandler(handler_cls, path)


def test_do_get_metrics_returns_200_and_writes_exposition_body() -> None:
    rec = _handler_for("/metrics")
    rec.get()
    assert rec.status == 200
    assert ("Content-Type", "text/plain; version=0.0.4") in rec.headers
    assert rec.body  # the rendered exposition was written
    assert b"swift_settlement_state" in rec.body


def test_do_get_root_path_also_serves_the_exposition() -> None:
    rec = _handler_for("/")
    rec.get()
    assert rec.status == 200
    assert rec.body


def test_do_get_unknown_path_returns_404_with_no_body() -> None:
    rec = _handler_for("/nope")
    rec.get()
    assert rec.status == 404
    assert rec.ended
    assert rec.body == b""


def test_log_message_is_silenced() -> None:
    rec = _handler_for("/metrics")
    assert rec.inst.log_message("%s", "anything") is None


# --- run() connects once and serves without binding a socket ------------------


def test_run_connects_and_serves_forever_without_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMessaging()
    monkeypatch.setattr(serve_http, "connect", lambda _args: fake)
    built: dict[str, Any] = {}

    class _FakeServer:
        def __init__(self, address: tuple[str, int], handler: type) -> None:
            built["address"] = address
            built["handler"] = handler

        def serve_forever(self) -> None:
            built["served"] = True

    monkeypatch.setattr(serve_http, "HTTPServer", _FakeServer)
    args = argparse.Namespace(port=9110, horizon=3, compression=1.0, **vars(_CONN))
    serve_http.run(args)
    assert built["address"] == ("", 9110)
    assert built["served"] is True


# --- main() parses and dispatches --------------------------------------------


def test_main_parses_defaults_and_dispatches_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(serve_http, "run", lambda args: seen.update(vars(args)))
    serve_http.main(["--qmgr", "QMX", "--conn", "host(1414)"])
    assert seen["qmgr"] == "QMX"
    assert seen["conn"] == "host(1414)"
    assert seen["channel"] == "APP.SVRCONN"
    assert seen["port"] == 9110
    assert seen["horizon"] == 3
    assert seen["compression"] == 1.0


def test_main_requires_qmgr_and_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_http, "run", lambda args: None)
    with pytest.raises(SystemExit):
        serve_http.main([])
