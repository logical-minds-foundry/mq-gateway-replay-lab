"""Tests for the SVC responder core: directive extraction + lifecycle planning.

All references are invented and anonymised (epic #65, anonymization rule).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from gwlab.swift.corpus import render_instruction
from gwlab.swift.directive import Directive
from gwlab.swift.generator import Mix, generate
from gwlab.swift.model import StatusItem, SwiftMessage, parse_swift
from gwlab.swift.responder_app import REPLY_QUEUE, ResponderError, directive_of, plan

if TYPE_CHECKING:
    from gwlab.swift.scheduler import Event

#: Invented, anonymised instruction reference (never client-identifiable).
_SEME = "GWSEC0000000042"
_NOW = datetime(2026, 7, 29, 9, 0)


def _instruction(mt: str = "541", seme: str = _SEME) -> SwiftMessage:
    return parse_swift(render_instruction(mt, seme), _NOW)


def _statuses(event: Event) -> list[tuple[str, str]]:
    """Parse an event body and return its ``(qualifier, code)`` MT548 statuses."""
    parsed = parse_swift(event.body, event.emit_at)
    return [(s.qualifier, s.value) for s in parsed.statuses]


def _confirmation_mt(event: Event) -> str:
    parsed = parse_swift(event.body, event.emit_at)
    assert parsed.is_confirmation
    return parsed.mt


def test_clean_yields_pack_match_then_matching_confirmation() -> None:
    directive = Directive("clean", "", (10, 20, 30))

    events = plan(_instruction("541"), directive, _NOW, c=1.0)

    assert len(events) == 3
    assert _statuses(events[0]) == [("IPRC", "PACK")]
    assert _statuses(events[1]) == [("MTCH", "MACH")]
    assert _confirmation_mt(events[2]) == "545"
    # Every response goes to the reply QREMOTE and echoes SEME as RELA.
    assert all(e.queue == REPLY_QUEUE for e in events)
    for event in events:
        assert parse_swift(event.body, event.emit_at).rela == _SEME


def test_clean_times_are_cumulative_over_c() -> None:
    directive = Directive("clean", "", (10, 20, 30))

    events = plan(_instruction("541"), directive, _NOW, c=1.0)

    assert [e.emit_at for e in events] == [
        _NOW + timedelta(seconds=10),
        _NOW + timedelta(seconds=30),
        _NOW + timedelta(seconds=60),
    ]


def test_compression_factor_scales_the_times() -> None:
    directive = Directive("clean", "", (60, 120, 180))

    real = plan(_instruction("541"), directive, _NOW, c=1.0)
    compressed = plan(_instruction("541"), directive, _NOW, c=60.0)

    assert [e.emit_at for e in real] == [
        _NOW + timedelta(seconds=60),
        _NOW + timedelta(seconds=180),
        _NOW + timedelta(seconds=360),
    ]
    assert [e.emit_at for e in compressed] == [
        _NOW + timedelta(seconds=1),
        _NOW + timedelta(seconds=3),
        _NOW + timedelta(seconds=6),
    ]


def test_a_541_settles_with_a_545_and_a_543_with_a_547() -> None:
    clean = Directive("clean", "", (0, 0, 0))

    assert _confirmation_mt(plan(_instruction("541"), clean, _NOW, c=1.0)[-1]) == "545"
    assert _confirmation_mt(plan(_instruction("543"), clean, _NOW, c=1.0)[-1]) == "547"


def test_reject_yields_a_single_rejt() -> None:
    # The directive carries a reason; the frozen corpus renders :25D: only, so the
    # reason drives the reject path but is not itself re-emitted on the wire.
    directive = Directive("reject", "NARR", (5, 0, 0))

    events = plan(_instruction("541"), directive, _NOW, c=1.0)

    assert len(events) == 1
    assert _statuses(events[0]) == [("IPRC", "REJT")]
    assert events[0].emit_at == _NOW + timedelta(seconds=5)
    assert events[0].queue == REPLY_QUEUE
    assert parse_swift(events[0].body, events[0].emit_at).rela == _SEME


def test_fail_yields_pack_match_then_penf() -> None:
    directive = Directive("fail", "LACK", (10, 20, 30))

    events = plan(_instruction("543"), directive, _NOW, c=1.0)

    assert [_statuses(e) for e in events] == [
        [("IPRC", "PACK")],
        [("MTCH", "MACH")],
        [("SETT", "PENF")],
    ]
    assert [e.emit_at for e in events] == [
        _NOW + timedelta(seconds=10),
        _NOW + timedelta(seconds=30),
        _NOW + timedelta(seconds=60),
    ]


def test_directive_of_round_trips_a_generated_instruction() -> None:
    generated = generate(1, seed=0)[0]

    assert directive_of(generated.text) == generated.directive


def test_directive_of_recovers_a_reject_with_a_reason() -> None:
    # Force a reject-with-reason instruction, then round-trip its directive.
    reject_only = Mix(clean=0.0, reject=1.0, fail=0.0, slow=0.0, ack_delay=5)
    generated = generate(1, mix=reject_only, seed=1)[0]

    assert generated.directive.outcome == "reject"
    assert generated.directive.reason != ""
    assert directive_of(generated.text) == generated.directive


def test_directive_of_raises_when_narrative_absent() -> None:
    # A bare corpus instruction has no :70E::DECL// directive narrative.
    with pytest.raises(ResponderError, match="directive narrative"):
        directive_of(render_instruction("541", _SEME))


def test_plan_without_seme_raises() -> None:
    seme_less = SwiftMessage(
        mt="541",
        seme=None,
        rela=None,
        statuses=(),
        put_time=_NOW,
        is_instruction=True,
        is_confirmation=False,
    )
    with pytest.raises(ResponderError, match="no SEME"):
        plan(seme_less, Directive("clean", "", (0, 0, 0)), _NOW, c=1.0)


def test_clean_with_unmapped_instruction_mt_raises() -> None:
    unmapped = SwiftMessage(
        mt="999",
        seme=_SEME,
        rela=None,
        statuses=(),
        put_time=_NOW,
        is_instruction=True,
        is_confirmation=False,
    )
    with pytest.raises(ResponderError, match="no confirmation MT"):
        plan(unmapped, Directive("clean", "", (0, 0, 0)), _NOW, c=1.0)


def test_unknown_outcome_raises() -> None:
    with pytest.raises(ResponderError, match="unknown directive outcome"):
        plan(_instruction("541"), Directive("bogus", "", (0, 0, 0)), _NOW, c=1.0)


def test_status_item_is_the_parsed_shape() -> None:
    # Guard the helper's assumption: a rendered status parses to one StatusItem.
    events = plan(_instruction("541"), Directive("reject", "", (0, 0, 0)), _NOW, c=1.0)
    parsed = parse_swift(events[0].body, events[0].emit_at)
    assert parsed.statuses == (StatusItem("IPRC", "REJT", None),)
