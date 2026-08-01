"""Tests for the SVC responder's box-only runtime loop, boundary-mocked.

The live loop drives pymqi UOWs and sleeps against real time. Here the pymqi
import is monkeypatched (``connect``), the ``Messaging`` is a
:class:`~gwlab.fake_messaging.FakeMessaging`, and the infinite ``run`` loop is
bounded by making ``time.sleep`` raise a sentinel after the first pass. The
planning/scheduling cores it wires (``plan``, ``Scheduler``) run for real.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.swift import serve_responder
from gwlab.swift.generator import generate
from gwlab.swift.responder_app import REPLY_QUEUE
from gwlab.swift.scheduler import Event, Scheduler

_CONN = argparse.Namespace(
    qmgr="SVCQM",
    conn="host(1414)",
    channel="SVC.SVRCONN",
    tls_cipher=None,
    key_repo=None,
    cert_label=None,
)


class _StopError(Exception):
    """Sentinel raised from a patched ``time.sleep`` to break the infinite loop."""


def _seed_instruction(fake: FakeMessaging) -> None:
    """Commit one generated instruction onto the landing queue."""
    text = generate(1, seed=0)[0].text
    fake.put(serve_responder.LANDING_QUEUE, Message(body=text.encode(), put_time=datetime.now(UTC)))
    fake.commit()


# --- connect() ----------------------------------------------------------------


def test_connect_builds_pymqi_messaging_from_args(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakePyMqi:
        def __init__(self, qmgr: str, conn: str, channel: str, **kw: Any) -> None:
            captured.update(qmgr=qmgr, conn=conn, channel=channel, **kw)

    monkeypatch.setattr("gwlab.pymqi_messaging.PyMqiMessaging", _FakePyMqi)
    result = serve_responder.connect(_CONN)
    assert isinstance(result, _FakePyMqi)
    assert captured["qmgr"] == "SVCQM"
    assert captured["channel"] == "SVC.SVRCONN"
    assert captured["cert_label"] is None


# --- _drain_instructions ------------------------------------------------------


def test_drain_instructions_plans_each_landing_message_onto_the_scheduler() -> None:
    fake = FakeMessaging()
    _seed_instruction(fake)
    scheduler = Scheduler()
    serve_responder._drain_instructions(fake, scheduler, 1.0)
    # the instruction was drained (committed away) and produced scheduled events
    assert fake.get(serve_responder.LANDING_QUEUE) is None
    due = scheduler.pop_due(datetime.now(UTC) + timedelta(days=3650))
    assert due  # at least one lifecycle event was planned


def test_drain_instructions_on_empty_landing_commits_and_plans_nothing() -> None:
    fake = FakeMessaging()
    scheduler = Scheduler()
    serve_responder._drain_instructions(fake, scheduler, 1.0)
    assert scheduler.pop_due(datetime.now(UTC) + timedelta(days=3650)) == []


# --- _emit_due ----------------------------------------------------------------


def test_emit_due_puts_every_due_event_and_commits() -> None:
    fake = FakeMessaging()
    scheduler = Scheduler()
    scheduler.push(
        Event(emit_at=datetime.now(UTC) - timedelta(seconds=1), queue=REPLY_QUEUE, body="R:1")
    )
    serve_responder._emit_due(fake, scheduler)
    committed = fake.browse(REPLY_QUEUE)
    assert [m.body for m in committed] == [b"R:1"]


def test_emit_due_with_nothing_due_commits_an_empty_uow() -> None:
    fake = FakeMessaging()
    scheduler = Scheduler()
    scheduler.push(
        Event(emit_at=datetime.now(UTC) + timedelta(days=1), queue=REPLY_QUEUE, body="later")
    )
    serve_responder._emit_due(fake, scheduler)
    assert fake.browse(REPLY_QUEUE) == []


# --- run() --------------------------------------------------------------------


def test_run_drains_emits_then_sleeps_until_killed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMessaging()
    _seed_instruction(fake)
    monkeypatch.setattr(serve_responder, "connect", lambda _args: fake)

    def _boom(_seconds: float) -> None:
        raise _StopError

    monkeypatch.setattr(serve_responder.time, "sleep", _boom)
    args = argparse.Namespace(compression=1.0, poll_seconds=1.0, **vars(_CONN))
    with pytest.raises(_StopError):
        serve_responder.run(args)
    # the loop body ran once before the sentinel: the instruction was drained
    assert fake.get(serve_responder.LANDING_QUEUE) is None


# --- main() -------------------------------------------------------------------


def test_main_parses_defaults_and_dispatches_to_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setattr(serve_responder, "run", lambda args: seen.update(vars(args)))
    serve_responder.main(["--qmgr", "SVCQM", "--conn", "host(1414)"])
    assert seen["qmgr"] == "SVCQM"
    assert seen["channel"] == "SVC.SVRCONN"
    assert seen["compression"] == 1.0
    assert seen["poll_seconds"] == 1.0


def test_main_requires_qmgr_and_conn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_responder, "run", lambda args: None)
    with pytest.raises(SystemExit):
        serve_responder.main([])
