"""Tests for automated recovery — resend of aged, unconfirmed messages."""

from datetime import datetime, timedelta

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.recovery import resend_aged

QS = QueueSet.for_counterparty("CPB")
NOW = datetime(2026, 7, 8, 16, 0, 0)  # noqa: DTZ001 — injected clock, deterministic
THRESHOLD = timedelta(minutes=30)


def _audit(sent: list[tuple[bytes, datetime]], confirmed: set[bytes]) -> FakeMessaging:
    fm = FakeMessaging()
    for msg_id, put_time in sent:
        fm.put(QS.send_audit, Message(b"body-" + msg_id, msg_id, put_time=put_time))
    for correl_id in confirmed:
        fm.put(QS.reply_audit, Message(b"ack", b"R" + correl_id, correl_id=correl_id, put_time=NOW))
    fm.commit()
    return fm


def test_resends_only_the_aged_unconfirmed_messages() -> None:
    fm = _audit(
        [(b"old", NOW - timedelta(minutes=40)), (b"fresh", NOW - timedelta(minutes=2))],
        confirmed=set(),
    )
    resent = resend_aged(fm, QS, NOW, THRESHOLD)
    assert resent == [b"old"]
    # the aged one is re-transmitted on the live SEND queue with its MsgId + body
    on_send = fm.browse(QS.send)
    assert [m.msg_id for m in on_send] == [b"old"]
    assert on_send[0].body == b"body-old"


def test_confirmed_messages_are_never_resent() -> None:
    fm = _audit([(b"a", NOW - timedelta(hours=1))], confirmed={b"a"})
    assert resend_aged(fm, QS, NOW, THRESHOLD) == []
    assert fm.browse(QS.send) == []


def test_nothing_old_enough_resends_nothing() -> None:
    fm = _audit([(b"young", NOW - timedelta(minutes=5))], confirmed=set())
    assert resend_aged(fm, QS, NOW, THRESHOLD) == []
    assert fm.browse(QS.send) == []
