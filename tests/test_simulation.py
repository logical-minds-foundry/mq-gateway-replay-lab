"""Tests for the full trading-day simulation (offline, deterministic)."""

from datetime import datetime, timedelta

from gwlab import reconcile
from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.reconcile import STATE_AGED
from gwlab.responder import Responder
from gwlab.simulation import TROUBLED_DAY, Phase, SimClock, run_day

QS = QueueSet.for_counterparty("CPB")
START = datetime(2026, 7, 8, 9, 0, 0)  # noqa: DTZ001 — injected clock, deterministic
TRADE_GAP = timedelta(seconds=30)
PHASE_GAP = timedelta(minutes=20)

_OUTAGE_TRADES = 18  # matches TROUBLED_DAY
_CONFIRMED_TRADES = 12 + 24 + 16
_TOTAL_TRADES = _OUTAGE_TRADES + _CONFIRMED_TRADES


def _streamed_fake(clock: SimClock) -> FakeMessaging:
    fm = FakeMessaging(clock=clock)
    fm.configure_stream(QS.send, QS.send_archive)
    fm.configure_stream(QS.reply, QS.reply_archive)
    return fm


def _run_troubled_day() -> tuple[FakeMessaging, SimClock]:
    clock = SimClock(START)
    fm = _streamed_fake(clock)
    responder = Responder(fm, QS)
    run_day(
        fm,
        QS,
        responder,
        TROUBLED_DAY,
        after_send=lambda _phase: clock.advance(TRADE_GAP),
        after_phase=lambda _phase: clock.advance(PHASE_GAP),
    )
    return fm, clock


def test_outage_backlog_is_left_unconfirmed_at_end_of_day() -> None:
    fm, clock = _run_troubled_day()
    dm = reconcile.report(fm, QS, clock()).metrics
    assert dm.sent == _TOTAL_TRADES
    assert dm.confirmed == _CONFIRMED_TRADES
    assert dm.unconfirmed == _OUTAGE_TRADES  # exactly the outage phase


def test_the_unconfirmed_set_names_the_outage_trades_and_they_are_aged() -> None:
    fm, clock = _run_troubled_day()
    detail = reconcile.report(fm, QS, clock()).unconfirmed
    assert len(detail) == _OUTAGE_TRADES
    # sent hours before end-of-day → all past the aged threshold
    assert all(item.state == STATE_AGED for item in detail)
    # each carries a distinct MsgId (which trade), the differentiator
    assert len({item.msg_id for item in detail}) == _OUTAGE_TRADES


def test_confirmed_trades_have_a_positive_mean_latency() -> None:
    fm, clock = _run_troubled_day()
    dm = reconcile.report(fm, QS, clock()).metrics
    assert dm.mean_latency_seconds > 0


def test_run_day_without_timing_hooks_drives_and_drains_both_phase_kinds() -> None:
    fm = FakeMessaging()  # no clock — exercises the no-hook path
    fm.configure_stream(QS.send, QS.send_archive)
    fm.configure_stream(QS.reply, QS.reply_archive)
    responder = Responder(fm, QS)
    sent = run_day(
        fm,
        QS,
        responder,
        (Phase("confirmed", 3, confirm=True), Phase("outage", 2, confirm=False)),
    )
    assert sent == 5
    assert fm.browse(QS.send) == []  # confirmed drained + outage consumed
    # the outage copies remain on the outbound archive with no confirmation
    assert len(fm.browse(QS.send_archive)) == 5
    assert len(fm.browse(QS.reply_archive)) == 3
