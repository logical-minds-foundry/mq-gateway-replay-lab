"""Tests for the responder (the counterparty stand-in)."""

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.responder import Responder

QS = QueueSet.for_counterparty("CPB")


def _seed_request(fm: FakeMessaging, msg_id: bytes) -> None:
    fm.put(QS.request, Message(b"trade", msg_id))
    fm.commit()


def test_reply_correlid_equals_the_request_msgid() -> None:
    fm = FakeMessaging()
    _seed_request(fm, b"1")

    assert Responder(fm, QS).process_one() == "replied"

    assert [m.correl_id for m in fm.browse(QS.reply)] == [b"1"]
    assert fm.browse(QS.request) == []  # the request was consumed


def test_every_nth_reply_is_dropped() -> None:
    fm = FakeMessaging()
    for i in range(1, 4):
        _seed_request(fm, str(i).encode())

    responder = Responder(fm, QS, drop_every=2)
    results = [responder.process_one() for _ in range(3)]

    assert results == ["replied", "dropped", "replied"]
    assert len(fm.browse(QS.reply)) == 2  # the dropped one produced no reply


def test_process_one_returns_none_when_no_requests() -> None:
    assert Responder(FakeMessaging(), QS).process_one() is None
