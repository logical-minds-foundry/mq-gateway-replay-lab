"""Tests for reconciliation-by-browse."""

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.reconcile import Unconfirmed, metrics, unconfirmed

QS = QueueSet.for_counterparty("CPB")


def _archive_sent(fm: FakeMessaging, *msg_ids: bytes) -> None:
    for msg_id in msg_ids:
        fm.put(QS.send_archive, Message(b"t", msg_id))
    fm.commit()


def _archive_replies(fm: FakeMessaging, *correl_ids: bytes) -> None:
    for correl_id in correl_ids:
        fm.put(QS.reply_archive, Message(b"r", b"R:" + correl_id, correl_id=correl_id))
    fm.commit()


def test_unconfirmed_is_sent_minus_confirmed() -> None:
    fm = FakeMessaging()
    _archive_sent(fm, b"1", b"2", b"3")
    _archive_replies(fm, b"1", b"3")
    assert [u.msg_id for u in unconfirmed(fm, QS)] == [b"2"]


def test_all_confirmed_yields_empty() -> None:
    fm = FakeMessaging()
    _archive_sent(fm, b"1")
    _archive_replies(fm, b"1")
    assert unconfirmed(fm, QS) == []


def test_metrics_counts_sent_confirmed_unconfirmed() -> None:
    fm = FakeMessaging()
    _archive_sent(fm, b"1", b"2", b"3")
    _archive_replies(fm, b"1")
    m = metrics(fm, QS)
    assert (m.sent, m.confirmed, m.unconfirmed) == (3, 1, 2)


def test_reconciliation_browses_only_the_archives_never_live_queues() -> None:
    class RecordingBrowse(FakeMessaging):
        def __init__(self) -> None:
            super().__init__()
            self.browsed: list[str] = []

        def browse(self, queue: str) -> list[Message]:
            self.browsed.append(queue)
            return super().browse(queue)

    fm = RecordingBrowse()
    _archive_sent(fm, b"1")
    assert unconfirmed(fm, QS) == [Unconfirmed(b"1", b"t")]
    assert set(fm.browsed) <= {QS.send_archive, QS.reply_archive}
    assert QS.send not in fm.browsed
    assert QS.reply not in fm.browsed
