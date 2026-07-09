"""Tests for the Message value type."""

from gwlab.messaging import Message


def test_message_carries_body_and_ids() -> None:
    m = Message(body=b"trade-1", msg_id=b"id-1")
    assert m.body == b"trade-1"
    assert m.msg_id == b"id-1"
    assert m.correl_id == b""


def test_correl_id_is_settable() -> None:
    m = Message(body=b"reply-1", msg_id=b"id-9", correl_id=b"id-1")
    assert m.correl_id == b"id-1"
