"""Tests for the queue-name source of truth."""

from gwlab.config import QueueSet


def test_for_counterparty_derives_audit_names() -> None:
    qs = QueueSet.for_counterparty("CPB")
    assert qs.send == "GW.CPB.SEND"
    assert qs.send_audit == "GW.CPB.SEND.AUDIT"
    assert qs.reply == "GW.CPB.REPLY"
    assert qs.reply_audit == "GW.CPB.REPLY.AUDIT"


def test_counterparty_tag_flows_into_every_name() -> None:
    qs = QueueSet.for_counterparty("CPA")
    assert all(name.startswith("GW.CPA.") for name in (qs.send, qs.reply))
