"""Tests for replay + idempotent resend — the end-to-end finale."""

from gwlab import inbound, outbound, reconcile
from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.replay import resend
from gwlab.responder import Responder

QS = QueueSet.for_counterparty("CPB")


def _drain_all(fm: FakeMessaging) -> None:
    while inbound.drain(fm, QS) is not None:
        pass


def _process_all(responder: Responder) -> list[str]:
    results: list[str] = []
    while (result := responder.process_one()) is not None:
        results.append(result)
    return results


def test_resend_reuses_msgid_and_does_not_re_archive() -> None:
    fm = FakeMessaging()
    outbound.send(fm, QS, b"trade", b"1")
    un = reconcile.unconfirmed(fm, QS)
    assert [u.msg_id for u in un] == [b"1"]

    assert resend(fm, QS, un) == 1

    # Re-transmitted with the same MsgId; the archive is NOT duplicated.
    assert all(m.msg_id == b"1" for m in fm.browse(QS.send))
    assert len(fm.browse(QS.send_archive)) == 1


def test_replayed_message_is_reconfirmed_not_reprocessed() -> None:
    fm = FakeMessaging()
    fm.put(QS.send, Message(b"trade", b"1"))
    fm.commit()
    responder = Responder(fm, QS)

    assert responder.process_one() == "replied"

    fm.put(QS.send, Message(b"trade", b"1"))  # the same MsgId comes back
    fm.commit()
    assert responder.process_one() == "reconfirmed"  # I2: not processed twice


def test_replay_drains_the_unconfirmed_set_to_zero() -> None:
    fm = FakeMessaging()
    for msg_id in (b"1", b"2", b"3"):
        outbound.send(fm, QS, b"trade", msg_id)

    responder = Responder(fm, QS, drop_every=2)  # the 2nd new message is dropped
    _process_all(responder)
    _drain_all(fm)

    unconfirmed = reconcile.unconfirmed(fm, QS)
    assert [u.msg_id for u in unconfirmed] == [b"2"]

    assert resend(fm, QS, unconfirmed) == 1
    replay_results = _process_all(responder)
    assert "reconfirmed" in replay_results  # I2: re-confirmed, not re-processed
    _drain_all(fm)

    assert reconcile.unconfirmed(fm, QS) == []  # backlog drained to zero
