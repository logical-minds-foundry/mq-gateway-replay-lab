"""Tests for the app-side lifecycle tracker (source #2) and its cross-validation.

The unit tests build :class:`SwiftMessage` fixtures directly (explicit datetimes)
and assert the fold and end-state reduction. The cross-validation test builds one
small fixture of rendered envelopes and asserts the app-side view
(:func:`app_end_states` over the sent/received messages) agrees, per ``SEME``, with
the collector-derived view (``end_state`` over ``collect``'s folded states) — the
whole point of keeping a second book (load-simulation design §2.1).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.swift import corpus
from gwlab.swift.collect import collect
from gwlab.swift.endstate import EndState, end_state
from gwlab.swift.gateway_app import app_end_states, track
from gwlab.swift.model import StatusItem, SwiftMessage, parse_swift
from gwlab.swift.reference import State
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

_BASE = datetime(2026, 7, 29, 9, 0)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _instruction(seme: str, minute: int = 0) -> SwiftMessage:
    return SwiftMessage("541", seme, None, (), _at(minute), True, False)


def _status(seme: str, pairs: list[tuple[str, str]], minute: int) -> SwiftMessage:
    statuses = tuple(StatusItem(qualifier, value, None) for qualifier, value in pairs)
    return SwiftMessage("548", None, seme, statuses, _at(minute), False, False)


def _confirmation(seme: str, minute: int) -> SwiftMessage:
    return SwiftMessage("545", None, seme, (), _at(minute), False, True)


def test_track_folds_a_clean_settlement() -> None:
    sent = [_instruction("S0")]
    received = [
        _status("S0", [("IPRC", "PACK")], 1),
        _status("S0", [("MTCH", "MACH")], 2),
        _confirmation("S0", 3),
    ]
    states = track(sent, received)
    assert [state.current for state in states] == [State.SETTLED]
    assert app_end_states(sent, received) == [EndState("S0", State.SETTLED, _at(3))]


def test_track_folds_a_rejection() -> None:
    sent = [_instruction("S0")]
    received = [_status("S0", [("IPRC", "REJT")], 1)]
    assert app_end_states(sent, received) == [EndState("S0", State.REJECTED, _at(1))]


def test_track_folds_a_failure() -> None:
    sent = [_instruction("S0")]
    received = [
        _status("S0", [("IPRC", "PACK")], 1),
        _status("S0", [("MTCH", "MACH")], 2),
        _status("S0", [("SETT", "PENF")], 3),
    ]
    assert app_end_states(sent, received) == [EndState("S0", State.FAILED, _at(3))]


def test_instruction_with_no_replies_is_in_flight() -> None:
    sent = [_instruction("S0")]
    states = track(sent, [])
    assert [state.current for state in states] == [State.INSTRUCTED]
    assert app_end_states(sent, []) == [EndState("S0", State.INSTRUCTED, None)]


def test_track_folds_multiple_instructions_independently() -> None:
    sent = [_instruction("S0"), _instruction("S1"), _instruction("S2")]
    received = [
        _status("S0", [("IPRC", "PACK")], 1),
        _confirmation("S0", 2),
        _status("S1", [("IPRC", "REJT")], 1),
    ]
    ends = {record.seme: record for record in app_end_states(sent, received)}
    assert ends["S0"].state is State.SETTLED
    assert ends["S1"] == EndState("S1", State.REJECTED, _at(1))
    assert ends["S2"] == EndState("S2", State.INSTRUCTED, None)


def test_app_view_matches_collector_view() -> None:
    # One fixture, folded two independent ways: the collector over the audit queues
    # and the app over its own sent/received messages. They must agree per SEME.
    now = datetime(2026, 7, 29, 12, 0)
    put_at = now - timedelta(hours=1)
    queues = SwiftQueueSet()
    horizon = Horizon(days=3)
    messaging = FakeMessaging()

    semes = ("GWSEC0000000001", "GWSEC0000000002", "GWSEC0000000003")
    sent: list[SwiftMessage] = []
    for seme in semes:
        text = corpus.render_instruction("541", seme)
        messaging.put(queues.instruct_audit, Message(body=text.encode(), put_time=put_at))
        sent.append(parse_swift(text, put_at))

    settle = semes[0]
    reject = semes[1]  # semes[2] stays silent -> in-flight
    replies = [
        (corpus.render_status(settle, [("IPRC", "PACK")]), 1),
        (corpus.render_status(settle, [("MTCH", "MACH")]), 2),
        (corpus.render_confirmation("545", settle), 3),
        (corpus.render_status(reject, [("IPRC", "REJT")]), 1),
    ]
    received: list[SwiftMessage] = []
    for text, minute in replies:
        reply_at = put_at + timedelta(minutes=minute)
        messaging.put(queues.status_audit, Message(body=text.encode(), put_time=reply_at))
        received.append(parse_swift(text, reply_at))
    messaging.commit()

    report = collect(messaging, queues, now, horizon)
    collector_ends = {record.seme: record for record in (end_state(s) for s in report.states)}
    app_ends = {record.seme: record for record in app_end_states(sent, received)}

    assert app_ends == collector_ends
    # The fixture genuinely exercised all three end states (not a vacuous match).
    assert {record.state for record in app_ends.values()} == {
        State.SETTLED,
        State.REJECTED,
        State.INSTRUCTED,
    }
