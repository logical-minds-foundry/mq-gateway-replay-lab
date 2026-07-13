"""Tests for the start-of-day / end-of-day audit-queue lifecycle."""

import pytest

from gwlab.config import QueueSet
from gwlab.dayboundary import (
    UnreconciledDayError,
    clear_audits,
    end_of_day,
    start_of_day,
)
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message

QS = QueueSet.for_counterparty("CPB")


def _audit(fm: FakeMessaging, *, sent: bytes, confirmed: bool) -> None:
    fm.put(QS.send_audit, Message(b"t", sent))
    if confirmed:
        fm.put(QS.reply_audit, Message(b"r", b"R:" + sent, correl_id=sent))
    fm.commit()


def test_clear_audits_empties_both_and_counts() -> None:
    fm = FakeMessaging()
    _audit(fm, sent=b"1", confirmed=True)
    assert clear_audits(fm, QS) == 2  # one send + one reply
    assert fm.browse(QS.send_audit) == []
    assert fm.browse(QS.reply_audit) == []


def test_start_of_day_clears_residue() -> None:
    fm = FakeMessaging()
    _audit(fm, sent=b"1", confirmed=True)
    assert start_of_day(fm, QS) == 2
    assert fm.browse(QS.send_audit) == []


def test_end_of_day_clears_a_fully_reconciled_day() -> None:
    fm = FakeMessaging()
    _audit(fm, sent=b"1", confirmed=True)
    assert end_of_day(fm, QS) == 2
    assert fm.browse(QS.send_audit) == []


def test_end_of_day_refuses_while_unconfirmed() -> None:
    fm = FakeMessaging()
    _audit(fm, sent=b"1", confirmed=False)
    with pytest.raises(UnreconciledDayError, match="unconfirmed"):
        end_of_day(fm, QS)
    assert fm.browse(QS.send_audit) != []  # nothing cleared


def test_end_of_day_force_clears_despite_unconfirmed() -> None:
    fm = FakeMessaging()
    _audit(fm, sent=b"1", confirmed=False)
    assert end_of_day(fm, QS, force=True) == 1
    assert fm.browse(QS.send_audit) == []
