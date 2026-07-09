"""Tests for the in-memory FakeMessaging — exercising the UOW semantics."""

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message


def test_put_is_invisible_until_commit() -> None:
    fm = FakeMessaging()
    fm.put("Q", Message(b"a", b"1"))
    assert fm.browse("Q") == []
    fm.commit()
    assert [m.msg_id for m in fm.browse("Q")] == [b"1"]


def test_backout_discards_staged_puts() -> None:
    fm = FakeMessaging()
    fm.put("Q", Message(b"a", b"1"))
    fm.backout()
    fm.commit()
    assert fm.browse("Q") == []


def test_two_puts_one_commit_are_atomic() -> None:
    fm = FakeMessaging()
    fm.put("XMIT", Message(b"a", b"1"))
    fm.put("ARCH", Message(b"a", b"1"))
    fm.commit()
    assert len(fm.browse("XMIT")) == 1
    assert len(fm.browse("ARCH")) == 1


def test_get_and_copy_in_one_uow_consumes_and_archives() -> None:
    fm = FakeMessaging()
    fm.put("REPLY", Message(b"r", b"9"))
    fm.commit()

    got = fm.get("REPLY")
    assert got is not None
    assert got.msg_id == b"9"
    fm.put("REPLY.ARCH", got)
    fm.commit()

    assert fm.browse("REPLY") == []
    assert [m.msg_id for m in fm.browse("REPLY.ARCH")] == [b"9"]


def test_get_on_empty_queue_returns_none() -> None:
    assert FakeMessaging().get("EMPTY") is None


def test_get_then_backout_leaves_the_message() -> None:
    fm = FakeMessaging()
    fm.put("Q", Message(b"a", b"1"))
    fm.commit()
    fm.get("Q")
    fm.backout()
    assert len(fm.browse("Q")) == 1
