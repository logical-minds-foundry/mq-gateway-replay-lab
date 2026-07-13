"""Tests for reconciliation-by-browse."""

from datetime import datetime, timedelta

import pytest

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.reconcile import (
    REASON_DUPLICATE,
    REASON_ORPHAN,
    STATE_AGED,
    STATE_AWAITING,
    STATE_WITHIN_SLA,
    Unconfirmed,
    daily_metrics,
    metrics,
    report,
    state_for,
    unconfirmed,
    unconfirmed_detail,
    unexpected_detail,
)

QS = QueueSet.for_counterparty("CPB")
NOW = datetime(2026, 7, 8, 16, 20, 0)  # noqa: DTZ001 — injected clock, deterministic


def _audit_sent(fm: FakeMessaging, *msg_ids: bytes) -> None:
    for msg_id in msg_ids:
        fm.put(QS.send_audit, Message(b"t", msg_id))
    fm.commit()


def _audit_replies(fm: FakeMessaging, *correl_ids: bytes) -> None:
    for correl_id in correl_ids:
        fm.put(QS.reply_audit, Message(b"r", b"R:" + correl_id, correl_id=correl_id))
    fm.commit()


def _send_at(fm: FakeMessaging, msg_id: bytes, put_time: datetime) -> None:
    fm.put(QS.send_audit, Message(b"t", msg_id, put_time=put_time))
    fm.commit()


def _reply_at(fm: FakeMessaging, correl_id: bytes, put_time: datetime) -> None:
    reply = Message(b"r", b"R:" + correl_id, correl_id=correl_id, put_time=put_time)
    fm.put(QS.reply_audit, reply)
    fm.commit()


# --- raw layer (time-agnostic) ---------------------------------------------


def test_unconfirmed_is_sent_minus_confirmed() -> None:
    fm = FakeMessaging()
    _audit_sent(fm, b"1", b"2", b"3")
    _audit_replies(fm, b"1", b"3")
    assert [u.msg_id for u in unconfirmed(fm, QS)] == [b"2"]


def test_all_confirmed_yields_empty() -> None:
    fm = FakeMessaging()
    _audit_sent(fm, b"1")
    _audit_replies(fm, b"1")
    assert unconfirmed(fm, QS) == []


def test_metrics_counts_sent_confirmed_unconfirmed() -> None:
    fm = FakeMessaging()
    _audit_sent(fm, b"1", b"2", b"3")
    _audit_replies(fm, b"1")
    m = metrics(fm, QS)
    assert (m.sent, m.confirmed, m.unconfirmed) == (3, 1, 2)


def test_reconciliation_browses_only_the_audit_queues_never_live_queues() -> None:
    class RecordingBrowse(FakeMessaging):
        def __init__(self) -> None:
            super().__init__()
            self.browsed: list[str] = []

        def browse(self, queue: str) -> list[Message]:
            self.browsed.append(queue)
            return super().browse(queue)

    fm = RecordingBrowse()
    _audit_sent(fm, b"1")
    assert unconfirmed(fm, QS) == [Unconfirmed(b"1", b"t")]
    assert set(fm.browsed) <= {QS.send_audit, QS.reply_audit}
    assert QS.send not in fm.browsed
    assert QS.reply not in fm.browsed


# --- state bucketing --------------------------------------------------------


def test_state_for_buckets_by_age() -> None:
    assert state_for(timedelta(minutes=0)) == STATE_WITHIN_SLA
    assert state_for(timedelta(minutes=9)) == STATE_WITHIN_SLA
    assert state_for(timedelta(minutes=10)) == STATE_AWAITING
    assert state_for(timedelta(minutes=29)) == STATE_AWAITING
    assert state_for(timedelta(minutes=30)) == STATE_AGED
    assert state_for(timedelta(hours=1)) == STATE_AGED


# --- current-trading-day detail --------------------------------------------


def test_unconfirmed_detail_carries_sent_time_age_and_state() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"aged", NOW - timedelta(minutes=40))
    _send_at(fm, b"matched", NOW - timedelta(minutes=15))
    _send_at(fm, b"fresh", NOW - timedelta(minutes=2))
    _reply_at(fm, b"matched", NOW - timedelta(minutes=14))

    detail = {d.msg_id: d for d in unconfirmed_detail(fm, QS, NOW)}
    assert set(detail) == {b"aged", b"fresh"}  # matched is excluded
    assert detail[b"aged"].state == STATE_AGED
    assert detail[b"aged"].age == timedelta(minutes=40)
    assert detail[b"aged"].sent_at == NOW - timedelta(minutes=40)
    assert detail[b"fresh"].state == STATE_WITHIN_SLA


def test_detail_ignores_prior_calendar_day() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"today", NOW - timedelta(minutes=5))
    _send_at(fm, b"yesterday", NOW - timedelta(days=1))
    assert [d.msg_id for d in unconfirmed_detail(fm, QS, NOW)] == [b"today"]


def test_daily_metrics_counts_aged_and_mean_latency() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"aged", NOW - timedelta(minutes=40))
    _send_at(fm, b"fresh", NOW - timedelta(minutes=2))
    _send_at(fm, b"matched", NOW - timedelta(minutes=15))
    _reply_at(fm, b"matched", NOW - timedelta(minutes=14))  # 60s latency

    dm = daily_metrics(fm, QS, NOW)
    assert (dm.sent, dm.confirmed, dm.unconfirmed) == (3, 1, 2)
    assert dm.aged == 1
    assert dm.mean_latency_seconds == 60.0
    assert dm.scanned_headers == 4  # 3 sent + 1 reply examined this pass


def test_daily_metrics_mean_latency_is_zero_when_nothing_matched() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"a", NOW - timedelta(minutes=5))
    dm = daily_metrics(fm, QS, NOW)
    assert dm.mean_latency_seconds == 0.0
    assert dm.confirmed == 0


def test_scoping_fails_loud_on_a_message_with_no_put_time() -> None:
    fm = FakeMessaging()
    _audit_sent(fm, b"nostamp")  # put_time defaults to None
    with pytest.raises(ValueError, match="no put_time"):
        daily_metrics(fm, QS, NOW)


# --- unexpected confirmations (Algorithm 3) ---------------------------------


def test_orphan_reply_is_flagged_unexpected() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"1", NOW - timedelta(minutes=5))
    _reply_at(fm, b"1", NOW - timedelta(minutes=4))  # legit confirmation
    _reply_at(fm, b"999", NOW - timedelta(minutes=1))  # orphan: no sent MsgId 999
    result = unexpected_detail(fm, QS, NOW)
    assert [(u.correl_id, u.reason) for u in result] == [(b"999", REASON_ORPHAN)]


def test_duplicate_confirmation_is_flagged_unexpected() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"1", NOW - timedelta(minutes=5))
    _reply_at(fm, b"1", NOW - timedelta(minutes=4))
    _reply_at(fm, b"1", NOW - timedelta(minutes=2))  # duplicate confirmation for 1
    result = unexpected_detail(fm, QS, NOW)
    assert [(u.correl_id, u.reason) for u in result] == [(b"1", REASON_DUPLICATE)]


def test_report_counts_unexpected_and_leaves_unconfirmed_intact() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"1", NOW - timedelta(minutes=5))  # confirmed
    _send_at(fm, b"2", NOW - timedelta(minutes=5))  # unconfirmed
    _reply_at(fm, b"1", NOW - timedelta(minutes=4))
    _reply_at(fm, b"777", NOW - timedelta(minutes=1))  # orphan
    r = report(fm, QS, NOW)
    assert r.metrics.unconfirmed == 1
    assert r.metrics.unexpected == 1
    assert [u.msg_id for u in r.unconfirmed] == [b"2"]
    assert [u.reason for u in r.unexpected] == [REASON_ORPHAN]
