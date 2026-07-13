"""Replay + idempotent resend — the end-to-end finale, over the streaming audit queue."""

from gwlab import inbound, outbound, reconcile
from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.replay import resend
from gwlab.responder import Responder

QS = QueueSet.for_counterparty("CPB")


def _streamed_fake() -> FakeMessaging:
    fm = FakeMessaging()
    fm.configure_stream(QS.request, QS.request_audit)
    fm.configure_stream(QS.reply, QS.reply_audit)
    return fm


def _drain_all(fm: FakeMessaging) -> None:
    while inbound.receive(fm, QS) is not None:
        pass


def _process_all(responder: Responder) -> list[str]:
    results: list[str] = []
    while (result := responder.process_one()) is not None:
        results.append(result)
    return results


def test_resend_re_transmits_by_original_msgid() -> None:
    fm = _streamed_fake()
    msg_id = outbound.send(fm, QS, b"trade")  # the queue manager mints the id
    un = reconcile.unconfirmed(fm, QS)
    assert [u.msg_id for u in un] == [msg_id]

    assert resend(fm, QS, un) == 1
    assert all(m.msg_id == msg_id for m in fm.browse(QS.request))  # replay reuses that id


def test_replayed_message_is_reconfirmed_not_reprocessed() -> None:
    fm = _streamed_fake()
    fm.put(QS.request, Message(b"trade", b"1"))
    fm.commit()
    responder = Responder(fm, QS)

    assert responder.process_one() == "replied"

    fm.put(QS.request, Message(b"trade", b"1"))  # the same MsgId comes back
    fm.commit()
    assert responder.process_one() == "reconfirmed"  # I2: not processed twice


def test_replay_drains_the_unconfirmed_set_to_zero() -> None:
    fm = _streamed_fake()
    ids = [outbound.send(fm, QS, b"trade") for _ in range(3)]  # QM mints each id

    responder = Responder(fm, QS, drop_every=2)  # the 2nd new message is dropped
    _process_all(responder)
    _drain_all(fm)

    unconfirmed = reconcile.unconfirmed(fm, QS)
    assert [u.msg_id for u in unconfirmed] == [ids[1]]  # the dropped (2nd) message

    assert resend(fm, QS, unconfirmed) == 1
    replay_results = _process_all(responder)
    assert "reconfirmed" in replay_results  # I2: re-confirmed, not re-processed
    _drain_all(fm)

    assert reconcile.unconfirmed(fm, QS) == []  # backlog drained to zero
