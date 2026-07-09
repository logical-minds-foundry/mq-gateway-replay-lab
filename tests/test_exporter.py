"""Tests for the Prometheus exposition."""

from gwlab.config import QueueSet
from gwlab.exporter import render
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message

QS = QueueSet.for_counterparty("CPB")


def _seed(fm: FakeMessaging, *, sent: int, confirmed: int) -> None:
    for i in range(sent):
        fm.put(QS.send_archive, Message(b"t", str(i).encode()))
    for i in range(confirmed):
        fm.put(QS.reply_archive, Message(b"r", b"R", correl_id=str(i).encode()))
    fm.commit()


def test_render_emits_counterparty_labelled_gauge_values() -> None:
    fm = FakeMessaging()
    _seed(fm, sent=3, confirmed=1)
    out = render(fm, QS, "CPB")
    assert 'gwlab_sent{counterparty="CPB"} 3' in out
    assert 'gwlab_confirmed{counterparty="CPB"} 1' in out
    assert 'gwlab_unconfirmed{counterparty="CPB"} 2' in out


def test_render_has_help_and_gauge_type_for_every_metric() -> None:
    out = render(FakeMessaging(), QS, "CPB")
    for name in ("gwlab_sent", "gwlab_confirmed", "gwlab_unconfirmed"):
        assert f"# HELP {name}" in out
        assert f"# TYPE {name} gauge" in out
    assert out.endswith("\n")
