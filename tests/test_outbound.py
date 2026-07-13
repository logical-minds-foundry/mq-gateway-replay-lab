"""Tests for the outbound path — streaming primary + two-put fallback."""

import pytest

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.outbound import send, send_via_two_put

QS = QueueSet.for_counterparty("CPB")


def _streamed() -> FakeMessaging:
    fm = FakeMessaging()
    fm.configure_stream(QS.request, QS.request_audit)
    return fm


def test_send_single_put_audits_via_stream_with_same_msgid() -> None:
    fm = _streamed()
    msg_id = send(fm, QS, body=b"trade-1")  # the queue manager mints the id
    assert msg_id  # a real, non-empty id was assigned and returned
    assert [m.msg_id for m in fm.browse(QS.request)] == [msg_id]
    assert [m.msg_id for m in fm.browse(QS.request_audit)] == [msg_id]


def test_streamed_copy_rides_the_send_uow() -> None:
    # MUSTDUP: the copy is staged in the send's unit of work, so a backout before
    # commit discards both — the both-or-neither guarantee, QM-enforced.
    fm = _streamed()
    fm.put(QS.request, Message(b"t", b"id-x"))
    fm.backout()
    assert fm.browse(QS.request) == []
    assert fm.browse(QS.request_audit) == []


def test_two_put_fallback_writes_both_queues() -> None:
    fm = FakeMessaging()  # no stream configured
    msg_id = send_via_two_put(fm, QS, body=b"trade-3")
    assert msg_id  # the queue manager mints it; both copies share it
    assert [m.msg_id for m in fm.browse(QS.request)] == [msg_id]
    assert [m.msg_id for m in fm.browse(QS.request_audit)] == [msg_id]


def test_two_put_fallback_is_atomic() -> None:
    class FailingSecondPut(FakeMessaging):
        def __init__(self) -> None:
            super().__init__()
            self._puts = 0

        def put(self, queue: str, message: Message) -> None:
            self._puts += 1
            if self._puts == 2:
                raise RuntimeError("boom on the audit put")
            super().put(queue, message)

    fm = FailingSecondPut()
    with pytest.raises(RuntimeError, match="boom"):
        send_via_two_put(fm, QS, body=b"t")
    assert fm.browse(QS.request) == []
    assert fm.browse(QS.request_audit) == []
