"""Tests for the live gateway requester runtime, boundary-mocked.

``gateway_run`` opens a pymqi connection and runs a blocking reply-drain loop.
Here the pymqi import is monkeypatched (``connect``), the ``Messaging`` is a
:class:`~gwlab.fake_messaging.FakeMessaging`, and the drain loop's clock/sleep
are scripted so the empty-queue wait and the deadline-return branches both run
deterministically. The generator, ``parse_swift`` and ``app_end_states`` it
drives run for real.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.swift import gateway_run
from gwlab.swift.endstate import EndState
from gwlab.swift.generator import generate
from gwlab.swift.reference import State

_CONN = argparse.Namespace(
    qmgr="NHAUAPP",
    conn="host(1414)",
    channel="APP.SVRCONN",
    tls_cipher=None,
    key_repo=None,
    cert_label=None,
)


# --- connect() ----------------------------------------------------------------


def test_connect_builds_pymqi_messaging_from_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakePyMqi:
        def __init__(self, qmgr: str, conn: str, channel: str, **kw: Any) -> None:
            captured.update(qmgr=qmgr, conn=conn, channel=channel, **kw)

    monkeypatch.setattr("gwlab.pymqi_messaging.PyMqiMessaging", _FakePyMqi)
    result = gateway_run.connect(_CONN)
    assert isinstance(result, _FakePyMqi)
    assert captured["qmgr"] == "NHAUAPP"
    assert captured["channel"] == "APP.SVRCONN"


# --- put_instructions ---------------------------------------------------------


def test_put_instructions_puts_and_returns_the_parsed_batch() -> None:
    fake = FakeMessaging()
    sent = gateway_run.put_instructions(fake, count=3, seed=0)
    assert len(sent) == 3
    assert len(fake.browse(gateway_run.INSTRUCT_QUEUE)) == 3


# --- drain_replies ------------------------------------------------------------


def test_drain_replies_gets_commits_and_parses_until_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMessaging()
    text = generate(2, seed=1)
    # one reply carries a put_time (used directly), one does not (falls back to now)
    fake.put(
        gateway_run.REPLY_QUEUE,
        Message(body=text[0].text.encode(), put_time=datetime.now(UTC)),
    )
    fake.put(gateway_run.REPLY_QUEUE, Message(body=text[1].text.encode()))
    fake.commit()

    times = iter([50.0, 150.0])  # first empty-check < deadline (sleep), second >= (return)
    monkeypatch.setattr(gateway_run.time, "time", lambda: next(times))
    slept: list[float] = []
    monkeypatch.setattr(gateway_run.time, "sleep", slept.append)

    received = gateway_run.drain_replies(fake, deadline=100.0)
    assert len(received) == 2
    assert slept == [1.0]  # waited once when the queue was momentarily empty
    assert fake.browse(gateway_run.REPLY_QUEUE) == []  # both replies consumed


# --- emit_end_states ----------------------------------------------------------


def test_emit_end_states_logs_terminal_and_in_flight_records(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    terminal = EndState(seme="GWSEC0000000001", state=State.SETTLED, terminal_at=datetime.now(UTC))
    in_flight = EndState(seme="GWSEC0000000002", state=State.INSTRUCTED, terminal_at=None)
    monkeypatch.setattr(
        gateway_run.gateway_app, "app_end_states", lambda _s, _r: [terminal, in_flight]
    )
    with caplog.at_level(logging.INFO, logger="gwlab.swift.gateway_run"):
        records = gateway_run.emit_end_states([], [])
    assert records == [terminal, in_flight]
    messages = "\n".join(caplog.messages)
    assert "GWSEC0000000001" in messages  # terminal_at rendered as isoformat
    assert "terminal_at=-" in messages  # the in-flight record's None -> "-"


# --- run() --------------------------------------------------------------------


def test_run_puts_drains_and_emits(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMessaging()
    monkeypatch.setattr(gateway_run, "connect", lambda _args: fake)
    monkeypatch.setattr(gateway_run.time, "time", lambda: 1000.0)  # deadline reached at once
    args = argparse.Namespace(
        count=2, seed=0, drain_seconds=0.0, request_reports=True, **vars(_CONN)
    )
    result = gateway_run.run(args)
    assert isinstance(result, list)
    assert len(fake.browse(gateway_run.INSTRUCT_QUEUE)) == 2


# --- main() -------------------------------------------------------------------


def test_main_parses_defaults_and_dispatches_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(gateway_run, "run", lambda args: seen.update(vars(args)))
    gateway_run.main(["--qmgr", "NHAUAPP", "--conn", "host(1414)"])
    assert seen["qmgr"] == "NHAUAPP"
    assert seen["channel"] == "APP.SVRCONN"
    assert seen["count"] == 1000
    assert seen["seed"] == 0
    assert seen["drain_seconds"] == 120.0
    assert seen["request_reports"] is True  # reports on by default


def test_main_requires_qmgr_and_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_run, "run", lambda args: None)
    with pytest.raises(SystemExit):
        gateway_run.main([])
