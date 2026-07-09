"""Tests for the inbound get-and-copy path."""

import pytest

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.inbound import drain
from gwlab.messaging import Message

QS = QueueSet.for_counterparty("CPB")


def test_drain_moves_the_reply_to_the_archive_in_one_uow() -> None:
    fm = FakeMessaging()
    fm.put(QS.reply, Message(b"r", b"R:1", correl_id=b"1"))
    fm.commit()

    got = drain(fm, QS)

    assert got is not None
    assert got.correl_id == b"1"
    assert fm.browse(QS.reply) == []
    assert [m.correl_id for m in fm.browse(QS.reply_archive)] == [b"1"]


def test_drain_returns_none_when_the_reply_queue_is_empty() -> None:
    assert drain(FakeMessaging(), QS) is None


def test_drain_failed_archive_put_leaves_the_reply_in_place() -> None:
    class FailingPut(FakeMessaging):
        def put(self, queue: str, message: Message) -> None:
            raise RuntimeError("boom on the archive put")

    fm = FailingPut()
    FakeMessaging.put(fm, QS.reply, Message(b"r", b"R:1", correl_id=b"1"))
    fm.commit()

    with pytest.raises(RuntimeError, match="boom"):
        drain(fm, QS)

    # The GET was never committed, so the confirmation is still on the queue —
    # redelivered rather than lost.
    assert len(fm.browse(QS.reply)) == 1
