"""Tests for the pymqi adapter — the one true MQ boundary — via a fake ``pymqi``.

``pymqi_messaging`` is the single seam that talks to a real queue manager: it
needs the IBM MQ client libraries to *run*, so it has always been box-only. But
its *logic* — how it drives the MQI surface (open/put/get/browse under
syncpoint, the persistent browse cursor, the reconnect reset) — is ordinary code
and fully testable once the boundary is faked.

This module injects a **fake ``pymqi``** into ``sys.modules`` before the adapter
does its lazy ``import pymqi``. The fake reproduces exactly the MQI surface the
adapter reads — ``CD``/``SCO``/``QueueManager``/``Queue``/``MD``/``GMO``/``PMO``,
``MQMIError`` (carrying ``.reason``), and a ``CMQC`` namespace of every constant —
and lets each test script what ``Queue.get`` returns or raises. No MQ client is
needed. All names/ids are invented and anonymised.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from gwlab import pymqi_messaging
from gwlab.messaging import Message
from gwlab.pymqi_messaging import PyMqiMessaging, _put_time

# --- the fake pymqi surface ---------------------------------------------------

# A CMQC namespace holding every constant the adapter reads. Reason codes carry
# their real MQ values; the option bits are distinct powers of two so the
# adapter's bitwise composition stays meaningful under assertion.
_CMQC = SimpleNamespace(
    MQXPT_TCP=1,
    MQCNO_RECONNECT_Q_MGR=2,
    MQOO_OUTPUT=0x10,
    MQOO_INPUT_SHARED=0x20,
    MQOO_BROWSE=0x40,
    MQPMO_SYNCPOINT=0x2,
    MQPMO_NEW_MSG_ID=0x400,
    MQGMO_SYNCPOINT=0x2,
    MQGMO_NO_WAIT=0x4,
    MQGMO_FAIL_IF_QUIESCING=0x2000,
    MQGMO_BROWSE_FIRST=0x10,
    MQGMO_BROWSE_NEXT=0x20,
    MQRC_NO_MSG_AVAILABLE=2033,
    MQRC_CONNECTION_BROKEN=2009,
)

# A reason code the adapter does not special-case (neither 2033 nor 2009), used
# to prove the "any other MQMIError re-raises" branches.
_OTHER_REASON = 2085  # MQRC_UNKNOWN_OBJECT_NAME


class _FakeMQMIError(Exception):
    """Stand-in for ``pymqi.MQMIError`` — the adapter only reads ``.reason``."""

    def __init__(self, reason: int) -> None:
        super().__init__(f"MQMIError comp=2 reason={reason}")
        self.reason = reason


class _FakeMD:
    """An MQMD carrying just the fields the adapter reads/writes."""

    def __init__(self, **kw: Any) -> None:
        self.MsgId = b""
        self.CorrelId = b""
        self.PutDate = b""
        self.PutTime = b""
        for key, value in kw.items():
            setattr(self, key, value)


class _FakeCD:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _FakeSCO:
    def __init__(self, **kw: Any) -> None:
        self.__dict__.update(kw)


class _FakeGMO:
    def __init__(self, Options: int = 0) -> None:  # noqa: N803 — MQI kw name
        self.Options = Options


class _FakePMO:
    def __init__(self, Options: int = 0) -> None:  # noqa: N803 — MQI kw name
        self.Options = Options


class _FakeQueueManager:
    def __init__(self, _name: Any) -> None:
        self.connect_args: dict[str, Any] | None = None
        self.committed = 0
        self.backed_out = 0
        self.disconnected = False

    def connect_with_options(
        self, qmgr_name: str, cd: Any = None, sco: Any = None, opts: Any = None
    ) -> None:
        self.connect_args = {"qmgr_name": qmgr_name, "cd": cd, "sco": sco, "opts": opts}

    def commit(self) -> None:
        self.committed += 1

    def backout(self) -> None:
        self.backed_out += 1

    def disconnect(self) -> None:
        self.disconnected = True


class _FakePyMqi:
    """A drop-in ``pymqi`` module: attribute access mirrors the real client.

    ``scripts`` maps a queue name to a list of outcomes consumed FIFO by
    ``Queue.get``: a dict spec is returned as a body (stamping the ``MD``), a
    bare ``int`` is raised as an ``MQMIError`` with that reason. ``puts`` and
    ``queues`` record what the adapter did for assertions.
    """

    def __init__(self) -> None:
        self.CMQC = _CMQC
        self.MQMIError = _FakeMQMIError
        self.CD = _FakeCD
        self.SCO = _FakeSCO
        self.MD = _FakeMD
        self.GMO = _FakeGMO
        self.PMO = _FakePMO
        self.QueueManager = _FakeQueueManager
        self.scripts: dict[str, list[Any]] = {}
        self.puts: list[dict[str, Any]] = []
        self.queues: list[Any] = []

        module = self

        class _Queue:
            def __init__(self, _qmgr: Any, name: str, opts: int) -> None:
                self.name = name
                self.opts = opts
                self.closed = False
                self.get_options: list[int] = []
                module.queues.append(self)

            def get(self, _buf: Any, md: _FakeMD, gmo: _FakeGMO) -> bytes:
                self.get_options.append(gmo.Options)
                script = module.scripts.get(self.name)
                if not script:
                    raise AssertionError(f"unscripted Queue.get on {self.name!r}")
                outcome = script.pop(0)
                if isinstance(outcome, int):
                    raise _FakeMQMIError(outcome)
                md.MsgId = outcome.get("msg_id", b"")
                md.CorrelId = outcome.get("correl_id", b"")
                md.PutDate = outcome.get("put_date", b"")
                md.PutTime = outcome.get("put_time", b"")
                return outcome["body"]

            def put(self, body: bytes, md: _FakeMD, pmo: _FakePMO) -> None:
                if pmo.Options & _CMQC.MQPMO_NEW_MSG_ID:
                    md.MsgId = b"MINTED-ID-000000"
                module.puts.append(
                    {
                        "name": self.name,
                        "body": body,
                        "msg_id": bytes(md.MsgId),
                        "correl_id": bytes(md.CorrelId),
                        "options": pmo.Options,
                    }
                )

            def close(self) -> None:
                self.closed = True

        self.Queue = _Queue


@pytest.fixture
def fake_pymqi(monkeypatch: pytest.MonkeyPatch) -> _FakePyMqi:
    """Install a fresh fake ``pymqi`` for the adapter's lazy import to pick up."""
    fake = _FakePyMqi()
    monkeypatch.setitem(sys.modules, "pymqi", fake)
    return fake


def _connect(fake: _FakePyMqi, **kw: Any) -> PyMqiMessaging:
    return PyMqiMessaging("QMX", "host(1414)", "APP.SVRCONN", **kw)


# --- __init__: plaintext vs TLS, the reconnect option -------------------------


def test_init_plaintext_connects_reconnectable_without_sco(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    args = adapter._qmgr.connect_args
    assert args is not None
    assert args["qmgr_name"] == "QMX"
    assert args["sco"] is None  # plaintext: no SSL config object
    assert args["opts"] == _CMQC.MQCNO_RECONNECT_Q_MGR
    assert args["cd"].ChannelName == b"APP.SVRCONN"
    assert args["cd"].ConnectionName == b"host(1414)"
    assert args["cd"].TransportType == _CMQC.MQXPT_TCP
    assert not hasattr(args["cd"], "SSLCipherSpec")


def test_init_tls_sets_cipher_key_repo_and_cert_label(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(
        fake_pymqi,
        tls_cipher="ANY_TLS13_OR_HIGHER",
        key_repo="/var/mqm/ssl/key",
        cert_label="gwlab-client",
    )
    args = adapter._qmgr.connect_args
    assert args is not None
    assert args["cd"].SSLCipherSpec == b"ANY_TLS13_OR_HIGHER"
    assert args["sco"].KeyRepository == b"/var/mqm/ssl/key"
    assert args["sco"].CertificateLabel == b"gwlab-client"


def test_init_tls_without_cert_label_or_key_repo(fake_pymqi: _FakePyMqi) -> None:
    # cert_label None → CertificateLabel is never set; key_repo None → "".
    adapter = _connect(fake_pymqi, tls_cipher="ANY_TLS13_OR_HIGHER")
    args = adapter._qmgr.connect_args
    assert args is not None
    assert args["sco"].KeyRepository == b""
    assert not hasattr(args["sco"], "CertificateLabel")


# --- _open: cache miss then hit -----------------------------------------------


def test_open_caches_the_handle_per_queue_and_opts(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    first = adapter._open("GW.CPB.REPLY", _CMQC.MQOO_INPUT_SHARED)
    second = adapter._open("GW.CPB.REPLY", _CMQC.MQOO_INPUT_SHARED)
    assert first is second  # cache hit: same handle returned
    assert len(fake_pymqi.queues) == 1  # opened only once


# --- put: explicit MsgId vs queue-manager-minted ------------------------------


def test_put_with_explicit_msg_id_does_not_request_a_new_id(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    message = Message(body=b"trade-1", msg_id=b"ID-EXPLICIT-0001", correl_id=b"C-1")
    adapter.put("GW.CPB.REQUEST", message)
    (record,) = fake_pymqi.puts
    assert record["msg_id"] == b"ID-EXPLICIT-0001"
    assert record["correl_id"] == b"C-1"
    assert record["options"] & _CMQC.MQPMO_SYNCPOINT
    assert not record["options"] & _CMQC.MQPMO_NEW_MSG_ID
    assert message.msg_id == b"ID-EXPLICIT-0001"  # unchanged


def test_put_without_msg_id_mints_a_new_id_and_reads_it_back(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    message = Message(body=b"trade-2")  # empty msg_id
    adapter.put("GW.CPB.REQUEST", message)
    (record,) = fake_pymqi.puts
    assert record["options"] & _CMQC.MQPMO_NEW_MSG_ID
    assert message.msg_id == b"MINTED-ID-000000"  # read back onto the message


# --- get: present / empty / error ---------------------------------------------


def test_get_returns_the_message_when_one_is_present(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    fake_pymqi.scripts["GW.CPB.REPLY"] = [
        {"body": b"reply-1", "msg_id": b"M-1", "correl_id": b"C-1"}
    ]
    message = adapter.get("GW.CPB.REPLY")
    assert message is not None
    assert message.body == b"reply-1"
    assert message.msg_id == b"M-1"
    assert message.correl_id == b"C-1"
    assert message.put_time is None  # no PutDate/PutTime stamped


def test_get_returns_none_on_no_msg_available(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    fake_pymqi.scripts["GW.CPB.REPLY"] = [_CMQC.MQRC_NO_MSG_AVAILABLE]
    assert adapter.get("GW.CPB.REPLY") is None


def test_get_reraises_any_other_mqmi_error(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    fake_pymqi.scripts["GW.CPB.REPLY"] = [_OTHER_REASON]
    with pytest.raises(_FakeMQMIError) as excinfo:
        adapter.get("GW.CPB.REPLY")
    assert excinfo.value.reason == _OTHER_REASON


# --- browse (non-persistent, drains from BROWSE_FIRST each call) ---------------


def test_browse_drains_the_queue_from_the_first_message(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    fake_pymqi.scripts["GW.CPB.REQUEST.AUDIT"] = [
        {"body": b"a", "msg_id": b"A"},
        {"body": b"b", "msg_id": b"B"},
        _CMQC.MQRC_NO_MSG_AVAILABLE,
    ]
    messages = adapter.browse("GW.CPB.REQUEST.AUDIT")
    assert [m.body for m in messages] == [b"a", b"b"]
    handle = fake_pymqi.queues[0]
    # First get positions at BROWSE_FIRST; every later get uses BROWSE_NEXT.
    assert handle.get_options[0] & _CMQC.MQGMO_BROWSE_FIRST
    assert handle.get_options[1] & _CMQC.MQGMO_BROWSE_NEXT
    assert handle.get_options[2] & _CMQC.MQGMO_BROWSE_NEXT


def test_browse_reraises_any_other_mqmi_error(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    fake_pymqi.scripts["GW.CPB.REQUEST.AUDIT"] = [_OTHER_REASON]
    with pytest.raises(_FakeMQMIError):
        adapter.browse("GW.CPB.REQUEST.AUDIT")


# --- browse_new (persistent cursor) -------------------------------------------


def test_browse_new_first_call_reads_from_first_then_cursor_persists(
    fake_pymqi: _FakePyMqi,
) -> None:
    adapter = _connect(fake_pymqi)
    queue = "GW.CPB.REPLY.AUDIT"
    fake_pymqi.scripts[queue] = [
        {"body": b"m1", "msg_id": b"1"},
        {"body": b"m2", "msg_id": b"2"},
        _CMQC.MQRC_NO_MSG_AVAILABLE,
    ]
    first = adapter.browse_new(queue)
    assert [m.body for m in first] == [b"m1", b"m2"]
    handle = fake_pymqi.queues[0]
    assert handle.get_options[0] & _CMQC.MQGMO_BROWSE_FIRST
    assert handle.get_options[1] & _CMQC.MQGMO_BROWSE_NEXT

    # Second call reuses the same parked cursor and reads only the new delta,
    # starting from BROWSE_NEXT (cursor already started).
    fake_pymqi.scripts[queue] = [
        {"body": b"m3", "msg_id": b"3"},
        _CMQC.MQRC_NO_MSG_AVAILABLE,
    ]
    second = adapter.browse_new(queue)
    assert [m.body for m in second] == [b"m3"]
    assert len(fake_pymqi.queues) == 1  # no new browse handle opened
    assert handle.get_options[2] & _CMQC.MQGMO_BROWSE_NEXT


def test_browse_new_honours_the_limit_cap(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    queue = "GW.CPB.REPLY.AUDIT"
    fake_pymqi.scripts[queue] = [
        {"body": b"m1", "msg_id": b"1"},
        {"body": b"m2", "msg_id": b"2"},
        {"body": b"m3", "msg_id": b"3"},  # must remain unread under the cap
    ]
    batch = adapter.browse_new(queue, limit=2)
    assert [m.body for m in batch] == [b"m1", b"m2"]
    assert fake_pymqi.scripts[queue] == [{"body": b"m3", "msg_id": b"3"}]  # parked


def test_browse_new_reopens_at_first_after_connection_broken(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    queue = "GW.CPB.REPLY.AUDIT"
    fake_pymqi.scripts[queue] = [
        {"body": b"m1", "msg_id": b"1"},
        _CMQC.MQRC_CONNECTION_BROKEN,
    ]
    partial = adapter.browse_new(queue)
    assert [m.body for m in partial] == [b"m1"]
    assert queue not in adapter._browse_cursors  # cursor discarded

    # Next call re-opens a fresh handle and re-reads from BROWSE_FIRST.
    fake_pymqi.scripts[queue] = [
        {"body": b"m1", "msg_id": b"1"},
        _CMQC.MQRC_NO_MSG_AVAILABLE,
    ]
    reread = adapter.browse_new(queue)
    assert [m.body for m in reread] == [b"m1"]
    assert len(fake_pymqi.queues) == 2  # a new browse handle after the break
    assert fake_pymqi.queues[1].get_options[0] & _CMQC.MQGMO_BROWSE_FIRST


def test_browse_new_reraises_any_other_mqmi_error(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    queue = "GW.CPB.REPLY.AUDIT"
    fake_pymqi.scripts[queue] = [_OTHER_REASON]
    with pytest.raises(_FakeMQMIError):
        adapter.browse_new(queue)


# --- unit-of-work + lifecycle -------------------------------------------------


def test_commit_backout_disconnect_delegate_to_the_queue_manager(
    fake_pymqi: _FakePyMqi,
) -> None:
    adapter = _connect(fake_pymqi)
    adapter.commit()
    adapter.backout()
    adapter.disconnect()
    assert adapter._qmgr.committed == 1
    assert adapter._qmgr.backed_out == 1
    assert adapter._qmgr.disconnected is True


def test_clear_drains_then_commits_and_counts(fake_pymqi: _FakePyMqi) -> None:
    adapter = _connect(fake_pymqi)
    queue = "GW.CPB.REQUEST"
    fake_pymqi.scripts[queue] = [
        {"body": b"x", "msg_id": b"X"},
        {"body": b"y", "msg_id": b"Y"},
        _CMQC.MQRC_NO_MSG_AVAILABLE,
    ]
    removed = adapter.clear(queue)
    assert removed == 2
    assert adapter._qmgr.committed == 1


# --- _put_time: both branches -------------------------------------------------


def test_put_time_parses_putdate_and_puttime_to_utc() -> None:
    md = _FakeMD(PutDate=b"20260729", PutTime=b"14300000")
    parsed = _put_time(md)
    assert parsed == datetime(2026, 7, 29, 14, 30, 0, tzinfo=UTC)


def test_put_time_returns_none_when_unstamped() -> None:
    assert _put_time(_FakeMD(PutDate=b"", PutTime=b"")) is None


def test_module_exposes_the_put_time_helper() -> None:
    # Guard the public name the reconciliation tier imports.
    assert pymqi_messaging._put_time is _put_time
