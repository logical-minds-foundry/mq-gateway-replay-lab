"""Tests for the counterparty scenario responder and the shared phase schedule."""

from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.scenario_responder import ScenarioResponder
from gwlab.simulation import phase_at

QS = QueueSet.for_counterparty("CPB")

# Phase boundaries (cumulative LAST_HOUR_SECONDS): steady [0,300), slowdown
# [300,720), block [720,1320), recovery [1320,1680), then None.
_STEADY, _SLOWDOWN, _BLOCK, _RECOVERY, _AFTER = 100.0, 400.0, 1000.0, 1400.0, 2000.0


def _request(fm: FakeMessaging, msg_id: bytes) -> None:
    fm.put(QS.request, Message(b"trade", msg_id))
    fm.commit()


# --- phase_at -----------------------------------------------------------------


def _label(elapsed: float) -> str:
    phase = phase_at(elapsed)
    assert phase is not None
    return phase.label


def test_phase_at_maps_elapsed_to_phase() -> None:
    assert _label(_STEADY) == "steady"
    assert _label(_SLOWDOWN) == "slowdown"
    assert _label(_BLOCK) == "block"
    assert _label(_RECOVERY) == "recovery"
    assert phase_at(_AFTER) is None


def test_phase_at_boundaries_are_half_open() -> None:
    assert _label(0.0) == "steady"
    assert _label(300.0) == "slowdown"  # first slowdown tick


# --- ScenarioResponder.step ---------------------------------------------------


def test_confirm_replies_and_consumes() -> None:
    fm = FakeMessaging()
    _request(fm, b"1")
    r = ScenarioResponder(fm, QS)
    assert r.step(_STEADY) == "replied"
    assert fm.browse(QS.request) == []  # consumed
    replies = fm.browse(QS.reply)
    assert [m.correl_id for m in replies] == [b"1"]  # CorrelId echoes the request MsgId


def test_idle_when_nothing_waiting() -> None:
    fm = FakeMessaging()
    assert ScenarioResponder(fm, QS).step(_STEADY) == "idle"


def test_block_withholds_and_leaves_the_request() -> None:
    fm = FakeMessaging()
    _request(fm, b"1")
    assert ScenarioResponder(fm, QS).step(_BLOCK) == "block"
    assert [m.msg_id for m in fm.browse(QS.request)] == [b"1"]  # still queued, unconsumed
    assert fm.browse(QS.reply) == []  # no confirmation


def test_recovery_drops_up_to_leave_unconfirmed_then_replies() -> None:
    fm = FakeMessaging()
    for i in (b"1", b"2", b"3"):
        _request(fm, i)
    r = ScenarioResponder(fm, QS, leave_unconfirmed=2)
    assert r.step(_RECOVERY) == "dropped"
    assert r.step(_RECOVERY) == "dropped"
    assert r.step(_RECOVERY) == "replied"  # leave budget spent → confirm the rest
    assert [m.correl_id for m in fm.browse(QS.reply)] == [b"3"]


def test_replay_is_reconfirmed_not_reprocessed() -> None:
    fm = FakeMessaging()
    _request(fm, b"1")
    r = ScenarioResponder(fm, QS)
    assert r.step(_STEADY) == "replied"
    _request(fm, b"1")  # same MsgId arrives again (a replay)
    assert r.step(_STEADY) == "reconfirmed"


def test_anomaly_injected_once_after_the_phases() -> None:
    fm = FakeMessaging()
    r = ScenarioResponder(fm, QS)
    assert r.step(_AFTER) == "anomaly"
    orphan = fm.browse(QS.reply)
    assert [m.correl_id for m in orphan] == [b"\xffNOSENT"]
    assert r.step(_AFTER) == "idle"  # not injected again; nothing left to confirm


def test_anomaly_disabled_never_injects() -> None:
    fm = FakeMessaging()
    assert ScenarioResponder(fm, QS, inject_anomaly=False).step(_AFTER) == "idle"
    assert fm.browse(QS.reply) == []
