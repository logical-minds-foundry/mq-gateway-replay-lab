"""Tests for the counterparty responder (all references invented/anonymised)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gwlab.fake_messaging import FakeMessaging
from gwlab.swift.corpus import render_instruction
from gwlab.swift.correlate import correlate
from gwlab.swift.lifecycle import fold
from gwlab.swift.model import SwiftMessage, parse_swift
from gwlab.swift.reference import STATUS_MT, State
from gwlab.swift.responder import Plan, respond
from gwlab.swift.swiftconfig import SwiftQueueSet

#: Invented, anonymised driven-instruction reference (never client-identifiable).
_SEME = "GWSEC0000000042"
_START = datetime(2026, 7, 29, 9, 0)


def _instruction(seme: str = _SEME) -> SwiftMessage:
    return parse_swift(render_instruction("541", seme), _START)


def _emitted(plan: Plan, instruction: SwiftMessage | None = None) -> list[SwiftMessage]:
    """Drive ``plan``, then browse the status audit and re-parse each response."""
    messaging = FakeMessaging()
    queues = SwiftQueueSet()
    respond(messaging, queues, instruction or _instruction(), plan, _START)
    parsed: list[SwiftMessage] = []
    for message in messaging.browse(queues.status_audit):
        put_time = message.put_time
        if put_time is None:
            raise AssertionError("the responder always stamps a put-time")
        parsed.append(parse_swift(message.body.decode(), put_time))
    return parsed


def test_matched_then_failing_emits_three_parseable_548s() -> None:
    instruction = _instruction()
    responses = _emitted(
        Plan(path="matched-then-failing", delays_seconds=[60, 120, 3600]), instruction
    )

    assert [r.mt for r in responses] == [STATUS_MT, STATUS_MT, STATUS_MT]
    assert [(s.qualifier, s.value) for r in responses for s in r.statuses] == [
        ("IPRC", "PACK"),
        ("MTCH", "MACH"),
        ("SETT", "PENF"),
    ]
    assert all(r.rela == instruction.seme for r in responses)


def test_matched_then_failing_put_times_are_strictly_increasing() -> None:
    responses = _emitted(Plan(path="matched-then-failing", delays_seconds=[60, 120, 3600]))

    put_times = [r.put_time for r in responses]
    assert put_times == [
        _START + timedelta(seconds=60),
        _START + timedelta(seconds=180),
        _START + timedelta(seconds=3780),
    ]
    assert put_times == sorted(put_times)
    assert len(set(put_times)) == len(put_times)


def test_clean_settle_ends_with_a_545_confirmation() -> None:
    instruction = _instruction()
    responses = _emitted(Plan(path="clean-settle", delays_seconds=[30, 60, 90]), instruction)

    assert [(s.qualifier, s.value) for r in responses[:2] for s in r.statuses] == [
        ("IPRC", "PACK"),
        ("MTCH", "MACH"),
    ]
    assert responses[-1].mt == "545"
    assert responses[-1].is_confirmation
    assert all(r.rela == instruction.seme for r in responses)


def test_clean_settle_folds_to_settled() -> None:
    instruction = _instruction()
    responses = _emitted(Plan(path="clean-settle", delays_seconds=[30, 60, 90]), instruction)

    correlated, orphans = correlate([instruction], responses)
    assert orphans == []
    assert len(correlated) == 1
    assert fold(correlated[0]).current is State.SETTLED


@pytest.mark.parametrize(
    ("path", "delays"),
    [
        ("clean-settle", [1.0, 2.0, 3.0]),
        ("rejected-at-validation", [1.0]),
        ("matched-then-failing", [1.0, 2.0, 3.0]),
        ("acknowledged-aging", [1.0]),
    ],
)
def test_each_path_emits_one_response_per_delay_all_echoing_seme(
    path: str, delays: list[float]
) -> None:
    instruction = _instruction()
    responses = _emitted(Plan(path=path, delays_seconds=delays), instruction)

    assert len(responses) == len(delays)
    assert all(r.rela == instruction.seme for r in responses)


def test_unknown_path_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown responder path"):
        respond(FakeMessaging(), SwiftQueueSet(), _instruction(), Plan(path="no-such-path"), _START)


def test_wrong_length_delays_raises_value_error() -> None:
    with pytest.raises(ValueError, match="delay"):
        respond(
            FakeMessaging(),
            SwiftQueueSet(),
            _instruction(),
            Plan(path="clean-settle", delays_seconds=[1.0, 2.0]),
            _START,
        )


def test_instruction_without_seme_raises_value_error() -> None:
    seme_less = SwiftMessage(
        mt="541",
        seme=None,
        rela=None,
        statuses=(),
        put_time=_START,
        is_instruction=True,
        is_confirmation=False,
    )
    with pytest.raises(ValueError, match="no SEME"):
        respond(
            FakeMessaging(),
            SwiftQueueSet(),
            seme_less,
            Plan(path="clean-settle", delays_seconds=[1.0, 2.0, 3.0]),
            _START,
        )
