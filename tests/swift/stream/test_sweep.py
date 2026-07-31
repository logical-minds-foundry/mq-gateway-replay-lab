"""Tests for the streaming collector's one-pass round-robin ingest (design §4).

The sweep is the MQ boundary of the streaming collector: it drives
:meth:`Messaging.browse_new` over the audit queues **in order** (instruct then
status), decodes each browsed body with ``collect._decode_and_parse``, folds the
parsed message into a :class:`SettlementState`, and ages the picture. Message
bodies are full, invented, anonymised ``{1:}{2:}{4:}`` envelopes seeded onto a
:class:`FakeMessaging` via ordinary puts + commit — exactly how the live audit
copies would be browsed behind a persistent cursor.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import gwlab.swift.stream.sweep as sweep_module
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.swift.reference import State
from gwlab.swift.stream.state import SettlementState
from gwlab.swift.stream.sweep import sweep
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

if TYPE_CHECKING:
    import pytest

_QUEUES = SwiftQueueSet()
_HORIZON = Horizon(days=3)
_NOW = datetime(2026, 7, 29, 17, 0)
_RECENT = datetime(2026, 7, 29, 9, 0)  # comfortably inside the 3-day horizon
_STALE = datetime(2026, 7, 24, 17, 0)  # 5 days before now, outside the horizon


def _instruction(seme: str, mt: str = "541") -> str:
    return (
        f"{{1:F01GWLABXXA0XXX0000000000}}{{2:I{mt}CPTYAAA0XXXN}}{{4:\n"
        ":16R:GENL\n"
        f":20C::SEME//{seme}\n"
        ":23G:NEWM\n"
        ":16S:GENL\n"
        ":16R:TRADDET\n"
        ":98A::SETT//20260729\n"
        ":35B:ISIN XX0000000001\n"
        "GENERIC EQUITY NPV\n"
        ":16S:TRADDET\n"
        "-}"
    )


def _confirmation(own_seme: str, rela: str, mt: str = "545") -> str:
    return (
        f"{{1:F01GWLABXXA0XXX0000000000}}{{2:O{mt}0900260729CPTYBBB0XXXN}}{{4:\n"
        ":16R:GENL\n"
        f":20C::SEME//{own_seme}\n"
        ":16R:LINK\n"
        f":20C::RELA//{rela}\n"
        ":16S:LINK\n"
        ":16S:GENL\n"
        "-}"
    )


# A body whose only field sits outside any :16R:/:16S: block: it tokenizes to a
# TokenizeError (a ValueError) -> a counted "malformed" anomaly.
_UNTOKENIZABLE = (
    "{1:F01GWLABXXA0XXX0000000000}{2:O5480900260729CPTYBBB0XXXN}{4:\n"
    ":20C::SEME//GWSECBADBAD000001\n"
    "-}"
)


def _seed(fake: FakeMessaging, queue: str, body: str, put_time: datetime) -> None:
    fake.put(queue, Message(body=body.encode(), put_time=put_time))
    fake.commit()


def test_sweep_reads_instruct_before_status_and_ages() -> None:
    fake = FakeMessaging()
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000010"), _RECENT)
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000011", "GWSEC0000000010"), _RECENT)
    state = SettlementState()

    result = sweep(fake, state, _QUEUES, _NOW, _HORIZON)

    assert result.ingested == 2
    report = state.report(_NOW, _HORIZON)
    assert report.states[0].current is State.SETTLED
    assert report.anomalies == []


def test_second_sweep_folds_only_new_arrivals() -> None:
    fake = FakeMessaging()
    state = SettlementState()
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000020"), _RECENT)

    first = sweep(fake, state, _QUEUES, _NOW, _HORIZON)

    assert first.ingested == 1  # only the instruction so far
    assert state.report(_NOW, _HORIZON).states[0].current is State.INSTRUCTED

    # A confirmation arrives after the first sweep; the second sweep sees ONLY it —
    # the continuous cursor never re-reads the already-browsed instruction.
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000021", "GWSEC0000000020"), _RECENT)

    second = sweep(fake, state, _QUEUES, _NOW, _HORIZON)

    assert second.ingested == 1  # the new confirmation only: no re-read, no double-count
    assert state.report(_NOW, _HORIZON).states[0].current is State.SETTLED


def test_malformed_body_is_counted_and_does_not_abort_the_sweep() -> None:
    fake = FakeMessaging()
    state = SettlementState()
    # A malformed body sits ahead of a valid confirmation on the same queue.
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000030"), _RECENT)
    _seed(fake, _QUEUES.status_audit, _UNTOKENIZABLE, _RECENT)
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000031", "GWSEC0000000030"), _RECENT)

    result = sweep(fake, state, _QUEUES, _NOW, _HORIZON)

    assert result.ingested == 3  # every browsed message is counted, malformed included
    report = state.report(_NOW, _HORIZON)
    assert [a.kind for a in report.anomalies] == ["malformed"]
    # The bad message never aborts: the valid confirmation still folds to SETTLED.
    assert report.states[0].current is State.SETTLED


def test_out_of_window_message_is_counted_but_not_ingested() -> None:
    fake = FakeMessaging()
    state = SettlementState()
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000040"), _STALE)

    result = sweep(fake, state, _QUEUES, _NOW, _HORIZON)

    assert result.ingested == 1  # browsed and decoded
    assert state.report(_NOW, _HORIZON).states == []  # but filtered out of the window


def test_unbounded_sweep_reports_not_more() -> None:
    fake = FakeMessaging()
    state = SettlementState()
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000050"), _RECENT)

    result = sweep(fake, state, _QUEUES, _NOW, _HORIZON)  # batch defaults to 0

    # An unbounded pass drains everything, so it never signals "more to come".
    assert result.ingested == 1
    assert result.more is False


def test_batched_sweep_bounds_the_pass_and_drains_across_calls() -> None:
    fake = FakeMessaging()
    state = SettlementState()
    semes = [f"GWSEC00000001{i:02d}" for i in range(5)]
    for seme in semes:
        _seed(fake, _QUEUES.instruct_audit, _instruction(seme), _RECENT)

    # batch=2 over 5 instructions: at most `batch` per queue, `more` flags a full
    # batch (its cursor likely has more), so the caller keeps sweeping.
    first = sweep(fake, state, _QUEUES, _NOW, _HORIZON, batch=2)
    assert first.ingested <= 2  # the per-queue cap held
    assert first.more is True

    second = sweep(fake, state, _QUEUES, _NOW, _HORIZON, batch=2)
    assert second.ingested <= 2
    assert second.more is True

    # The final call drains the remainder (a short batch) and signals caught-up.
    third = sweep(fake, state, _QUEUES, _NOW, _HORIZON, batch=2)
    assert third.more is False

    # No message dropped and none double-counted: exactly the 5 distinct
    # instructions folded across the batched calls, each seen once.
    assert first.ingested + second.ingested + third.ingested == 5
    report = state.report(_NOW, _HORIZON)
    assert len(report.states) == 5
    assert all(s.current is State.INSTRUCTED for s in report.states)


def test_batched_sweep_preserves_instruct_before_status_correlation() -> None:
    fake = FakeMessaging()
    state = SettlementState()
    # Instruction and its confirmation both present before the batched drain: the
    # instruct queue is fully drained before the status queue within one call, so
    # the confirmation still folds to SETTLED (the correlation invariant, §5).
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000200"), _RECENT)
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000201", "GWSEC0000000200"), _RECENT)

    result = sweep(fake, state, _QUEUES, _NOW, _HORIZON, batch=10)

    assert result.ingested == 2
    assert result.more is False
    assert state.report(_NOW, _HORIZON).states[0].current is State.SETTLED


def test_sweep_yields_the_gil_every_yield_every_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    # The pure-Python fold holds the GIL; sweep hands it off every _YIELD_EVERY
    # folded messages so the box-only HTTP thread can serve mid-batch (issue #152).
    yields: list[float] = []
    monkeypatch.setattr(sweep_module, "_YIELD_EVERY", 2)
    monkeypatch.setattr(sweep_module.time, "sleep", lambda seconds: yields.append(seconds))
    fake = FakeMessaging()
    state = SettlementState()
    for i in range(4):
        _seed(fake, _QUEUES.instruct_audit, _instruction(f"GWSEC00000003{i:02d}"), _RECENT)

    result = sweep(fake, state, _QUEUES, _NOW, _HORIZON)

    assert result.ingested == 4
    # 4 messages, yield every 2 → a cooperative sleep(0) at the 2nd and 4th.
    assert yields == [0, 0]
