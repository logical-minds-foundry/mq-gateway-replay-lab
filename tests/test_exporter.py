"""Tests for the Prometheus exposition."""

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from gwlab.config import QueueSet
from gwlab.exporter import render
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message

if TYPE_CHECKING:
    from collections.abc import Callable

QS = QueueSet.for_counterparty("CPB")
NOW = datetime(2026, 7, 8, 16, 20, 0)  # noqa: DTZ001 — injected clock, deterministic


def _seed(fm: FakeMessaging, *, sent: int, confirmed: int) -> None:
    for i in range(sent):
        msg = Message(b"t", str(i).encode(), put_time=NOW - timedelta(minutes=1))
        fm.put(QS.request_audit, msg)
    for i in range(confirmed):
        reply = Message(b"r", b"R", correl_id=str(i).encode(), put_time=NOW - timedelta(seconds=30))
        fm.put(QS.reply_audit, reply)
    fm.commit()


def _fixed_timer(*, elapsed: float) -> Callable[[], float]:
    """A monotonic-clock stand-in that advances by ``elapsed`` between two reads."""
    values = iter([0.0, elapsed])
    return lambda: next(values)


def test_render_emits_counterparty_labelled_gauge_values() -> None:
    fm = FakeMessaging()
    _seed(fm, sent=3, confirmed=1)
    out = render(fm, QS, "CPB", NOW)
    assert 'gwlab_sent{counterparty="CPB"} 3' in out
    assert 'gwlab_confirmed{counterparty="CPB"} 1' in out
    assert 'gwlab_unconfirmed{counterparty="CPB"} 2' in out


def test_render_emits_scan_cost_gauges() -> None:
    fm = FakeMessaging()
    _seed(fm, sent=3, confirmed=1)
    out = render(fm, QS, "CPB", NOW, timer=_fixed_timer(elapsed=0.25))
    assert 'gwlab_reconcile_scanned_headers{counterparty="CPB"} 4' in out
    assert 'gwlab_reconcile_scan_seconds{counterparty="CPB"} 0.25' in out


def test_render_emits_per_message_unconfirmed_series() -> None:
    fm = FakeMessaging()
    fm.put(
        QS.request_audit,
        Message(b"t", bytes.fromhex("9f0a17f3"), put_time=NOW - timedelta(minutes=40)),
    )
    fm.commit()
    out = render(fm, QS, "CPB", NOW)
    # which trade, when, and state — not just how many
    assert 'msgid="9f0a17f3"' in out
    assert 'sent="15:40:00"' in out
    assert 'state="aged"' in out
    assert "gwlab_unconfirmed_message{" in out


def test_render_exposes_unexpected_confirmations() -> None:
    fm = FakeMessaging()
    # one confirmed, one orphan reply (CorrelId 999 was never sent)
    fm.put(QS.request_audit, Message(b"t", b"1", put_time=NOW - timedelta(minutes=5)))
    fm.commit()
    fm.put(
        QS.reply_audit, Message(b"r", b"R:1", correl_id=b"1", put_time=NOW - timedelta(minutes=4))
    )
    fm.commit()
    fm.put(
        QS.reply_audit,
        Message(b"r", b"R:999", correl_id=b"999", put_time=NOW - timedelta(minutes=1)),
    )
    fm.commit()
    text = render(fm, QS, "CPB", NOW)
    assert 'gwlab_unexpected_confirmations{counterparty="CPB"} 1' in text
    assert "gwlab_unexpected_confirmation{" in text
    assert 'reason="orphan"' in text


def test_render_has_help_and_gauge_type_for_every_metric() -> None:
    out = render(FakeMessaging(), QS, "CPB", NOW)
    for name in (
        "gwlab_sent",
        "gwlab_confirmed",
        "gwlab_unconfirmed",
        "gwlab_unconfirmed_aged",
        "gwlab_confirm_latency_seconds",
        "gwlab_reconcile_scanned_headers",
        "gwlab_reconcile_scan_seconds",
        "gwlab_unconfirmed_message",
        "gwlab_unexpected_confirmations",
        "gwlab_unexpected_confirmation",
    ):
        assert f"# HELP {name}" in out
        assert f"# TYPE {name} gauge" in out
    assert out.endswith("\n")
