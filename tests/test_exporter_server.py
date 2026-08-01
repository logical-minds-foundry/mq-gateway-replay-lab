"""Tests for the box-only v1 exporter HTTP server, boundary-mocked.

``exporter_server`` opens a pymqi connection and binds a socket, so on the box it
runs end-to-end. Here every external edge is faked: ``PyMqiMessaging`` is
replaced so no MQ client is needed, ``HTTPServer`` is replaced so nothing binds,
and the request handler is driven against an in-memory ``wfile`` + stubbed
``send_*`` so the ``do_GET`` routing is exercised directly. The exposition body
itself is ``exporter.render`` (unit-tested separately). All data is anonymised.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from gwlab import exporter_server
from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging

_QS = QueueSet.for_counterparty("CPB")


class _RecordingHandler:
    """Captures the status/headers/body a ``do_GET`` writes without a real socket."""

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


def _handler_for(path: str) -> _RecordingHandler:
    handler_cls = exporter_server._make_handler(FakeMessaging(), _QS, "CPB")
    return _RecordingHandler(handler_cls, path)


# --- do_GET routing -----------------------------------------------------------


def test_do_get_metrics_returns_200_and_writes_the_exposition() -> None:
    rec = _handler_for("/metrics")
    rec.get()
    assert rec.status == 200
    assert ("Content-Type", "text/plain; version=0.0.4") in rec.headers
    assert any(key == "Content-Length" for key, _ in rec.headers)
    assert rec.ended
    assert b"gwlab_sent" in rec.body  # the rendered exposition was written


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


# --- serve(): connect once, then serve without binding ------------------------


def test_serve_connects_optionally_over_tls_and_serves_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    built: dict[str, Any] = {}

    class _FakePyMqi:
        def __init__(self, qmgr: str, conn: str, channel: str, **kw: Any) -> None:
            captured.update(qmgr=qmgr, conn=conn, channel=channel, **kw)

    class _FakeServer:
        def __init__(self, address: tuple[str, int], handler: type) -> None:
            built["address"] = address
            built["handler"] = handler

        def serve_forever(self) -> None:
            built["served"] = True

    monkeypatch.setattr(exporter_server, "PyMqiMessaging", _FakePyMqi)
    monkeypatch.setattr(exporter_server, "HTTPServer", _FakeServer)

    exporter_server.serve(
        "QMX",
        "host(1414)",
        "APP.SVRCONN",
        "CPB",
        9109,
        tls_cipher="ANY_TLS13_OR_HIGHER",
        key_repo="/var/mqm/ssl/key",
        cert_label="gwlab-client",
    )

    assert captured == {
        "qmgr": "QMX",
        "conn": "host(1414)",
        "channel": "APP.SVRCONN",
        "tls_cipher": "ANY_TLS13_OR_HIGHER",
        "key_repo": "/var/mqm/ssl/key",
        "cert_label": "gwlab-client",
    }
    assert built["address"] == ("", 9109)
    assert built["served"] is True


# --- main(): parse and dispatch ----------------------------------------------


def test_main_parses_defaults_and_dispatches_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def _fake_serve(qmgr: str, conn: str, channel: str, counterparty: str, port: int, **kw: Any):
        seen.update(
            qmgr=qmgr, conn=conn, channel=channel, counterparty=counterparty, port=port, **kw
        )

    monkeypatch.setattr(exporter_server, "serve", _fake_serve)
    exporter_server.main(
        [
            "--qmgr",
            "QMX",
            "--conn",
            "host(1414)",
            "--channel",
            "APP.SVRCONN",
            "--counterparty",
            "CPB",
        ]
    )
    assert seen["qmgr"] == "QMX"
    assert seen["conn"] == "host(1414)"
    assert seen["channel"] == "APP.SVRCONN"
    assert seen["counterparty"] == "CPB"
    assert seen["port"] == 9109  # default
    assert seen["tls_cipher"] is None
    assert seen["key_repo"] is None
    assert seen["cert_label"] is None


def test_main_requires_the_connection_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(exporter_server, "serve", lambda *a, **k: None)
    with pytest.raises(SystemExit):
        exporter_server.main([])
