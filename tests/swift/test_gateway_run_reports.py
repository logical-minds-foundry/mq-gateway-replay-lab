"""Tests for the COA/COD report request on the live requester's put.

Epic #105 attaches plain COA/COD report options to ``put_instructions``' existing
put (gated by ``request_reports``) and commits **per instruction** so each SENT
timestamp is its own transmit moment — the accuracy the ``sent->arrived``
delivery-latency leg depends on (spec §3, §7.1). Driven against the in-memory
``FakeMessaging`` (which records each put's report options) — no live QM.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from typing import TYPE_CHECKING

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import MQRO_COA, MQRO_COD
from gwlab.swift import gateway_run
from gwlab.swift.swiftconfig import SwiftQueueSet

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def test_put_instructions_requests_plain_coa_cod_through_the_qalias_when_enabled() -> None:
    fake = FakeMessaging()
    gateway_run.put_instructions(fake, count=1, seed=0, queues=SwiftQueueSet(request_reports=True))
    put = fake.puts[-1]
    assert put.queue == "GW.SEC.INSTRUCT"  # the request QALIAS
    assert put.report_opts.report == MQRO_COA | MQRO_COD  # plain, never _WITH_DATA
    assert put.report_opts.reply_to_q == "GW.SEC.REPORT"
    assert put.report_opts.reply_to_qmgr is None  # blank → our local QM


def test_put_instructions_requests_no_reports_when_disabled() -> None:
    fake = FakeMessaging()
    gateway_run.put_instructions(fake, count=1, seed=0, queues=SwiftQueueSet(request_reports=False))
    assert fake.puts[-1].report_opts is None


def test_put_instructions_defaults_to_requesting_reports() -> None:
    # No queue set passed → default SwiftQueueSet (request_reports on).
    fake = FakeMessaging()
    gateway_run.put_instructions(fake, count=1, seed=0)
    assert fake.puts[-1].report_opts is not None


def test_put_instructions_commits_per_instruction_with_sent_at_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMessaging()
    commit_calls = 0
    original_commit = fake.commit

    def counting_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit()

    monkeypatch.setattr(fake, "commit", counting_commit)

    # A distinct, increasing clock so each SENT is provably its own commit time.
    ticks = count()
    monkeypatch.setattr(
        gateway_run,
        "datetime",
        _FrozenClock(lambda: datetime(2026, 8, 1, 12, 0, next(ticks), tzinfo=UTC)),
    )

    sent = gateway_run.put_instructions(fake, count=3, seed=0)

    assert commit_calls == 3  # one unit of work per instruction
    assert len(fake.browse("GW.SEC.INSTRUCT")) == 3  # all committed
    put_times = [m.put_time for m in sent]
    assert put_times == sorted(put_times)  # SENT ascends with each commit
    assert len(set(put_times)) == 3  # each instruction's SENT is its own


class _FrozenClock:
    """A stand-in for the ``datetime`` module exposing the ``now`` the source calls."""

    def __init__(self, now: Callable[[], datetime]) -> None:
        self._now = now

    def now(self, tz: object = None) -> datetime:
        return self._now()
