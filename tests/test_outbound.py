"""Tests for the outbound atomic two-put."""

import pytest

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.outbound import send

QS = QueueSet.for_counterparty("CPB")


def test_send_puts_the_same_msgid_to_both_queues() -> None:
    fm = FakeMessaging()
    returned = send(fm, QS, body=b"trade-1", msg_id=b"id-1")
    assert returned == b"id-1"
    sent = fm.browse(QS.send)
    archived = fm.browse(QS.send_archive)
    assert [m.msg_id for m in sent] == [b"id-1"]
    assert [m.msg_id for m in archived] == [b"id-1"]
    assert sent[0].body == archived[0].body == b"trade-1"


def test_send_is_atomic_a_failed_second_put_commits_neither() -> None:
    class FailingSecondPut(FakeMessaging):
        def __init__(self) -> None:
            super().__init__()
            self._puts = 0

        def put(self, queue: str, message: Message) -> None:
            self._puts += 1
            if self._puts == 2:
                raise RuntimeError("boom on the archive put")
            super().put(queue, message)

    fm = FailingSecondPut()
    with pytest.raises(RuntimeError, match="boom"):
        send(fm, QS, body=b"trade-2", msg_id=b"id-2")

    # The first PUT was only staged, and commit() never ran — so nothing is
    # visible on either queue. No "sent but unarchived" state (I1).
    assert fm.browse(QS.send) == []
    assert fm.browse(QS.send_archive) == []
