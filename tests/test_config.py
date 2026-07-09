"""Tests for the queue-name source of truth."""

from gwlab.config import QueueSet


def test_for_counterparty_derives_the_four_queues() -> None:
    qs = QueueSet.for_counterparty("CPB")
    assert qs.send == "GW.CPB.SEND"
    assert qs.send_archive == "GW.CPB.SEND.ARCHIVE"
    assert qs.reply == "GW.CPB.REPLY"
    assert qs.reply_archive == "GW.CPB.REPLY.ARCHIVE"


def test_counterparty_tag_flows_into_every_name() -> None:
    qs = QueueSet.for_counterparty("CPA")
    assert all(name.startswith("GW.CPA.") for name in (qs.send, qs.reply))
