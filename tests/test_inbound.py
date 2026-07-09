"""Tests for the inbound path — streaming primary + get-copy fallback."""

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.inbound import receive, receive_via_get_copy
from gwlab.messaging import Message

QS = QueueSet.for_counterparty("CPB")


def _reply_streamed() -> FakeMessaging:
    fm = FakeMessaging()
    fm.configure_stream(QS.reply, QS.reply_archive)
    return fm


def test_receive_consumes_reply_that_was_archived_on_arrival() -> None:
    fm = _reply_streamed()
    fm.put(QS.reply, Message(b"r", b"R:1", correl_id=b"1"))  # arrival streams to archive
    fm.commit()

    got = receive(fm, QS)

    assert got is not None
    assert got.correl_id == b"1"
    assert fm.browse(QS.reply) == []  # consumed
    assert [m.correl_id for m in fm.browse(QS.reply_archive)] == [b"1"]  # archived on arrival


def test_receive_returns_none_when_reply_empty() -> None:
    assert receive(FakeMessaging(), QS) is None


def test_get_copy_fallback_moves_reply_to_archive() -> None:
    fm = FakeMessaging()  # no stream configured
    fm.put(QS.reply, Message(b"r", b"R:1", correl_id=b"1"))
    fm.commit()

    got = receive_via_get_copy(fm, QS)

    assert got is not None
    assert fm.browse(QS.reply) == []
    assert [m.correl_id for m in fm.browse(QS.reply_archive)] == [b"1"]


def test_get_copy_fallback_returns_none_when_empty() -> None:
    assert receive_via_get_copy(FakeMessaging(), QS) is None
