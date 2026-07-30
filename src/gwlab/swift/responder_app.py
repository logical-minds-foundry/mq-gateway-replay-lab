"""The SVC responder's testable core: read a directive, plan the lifecycle response.

The counterparty-sim on SVCQM drains our streamed instructions, reads the embedded
processing directive, and computes the whole lifecycle response **plan** up front —
an ordered list of timed :class:`~gwlab.swift.scheduler.Event`\\ s to push onto the
one shared scheduler (§6). This module is that pure planning core, fully covered by
tests; the box-only live loop (pymqi, sockets, sleeps, the get/put UOWs) lives in
:mod:`gwlab.swift.serve_responder`, which is coverage-omitted like
:mod:`gwlab.swift.serve_http`.

Two inputs drive one response stream (§5): the **directive** decides the outcome,
its reason, and the per-stage delays; the **instruction** decides correlation and
message type. Each response echoes the instruction's ``:20C::SEME//`` as its
``:20C::RELA//``, and a clean settlement's confirmation MT matches the instruction
MT (540->544, 541->545, 542->546, 543->547 — :data:`_CONFIRMATION_FOR`).

Every delay is divided by the single time-compression factor ``c`` (§7): an event's
``emit_at`` is ``now + Δ_cumulative / c`` seconds. ``c = 1`` is real time.

The response bodies are rendered by the corpus renderers, which model MT548 as
``:25D:`` status lines only (no ``:24B:`` reason field) — mirroring
:mod:`gwlab.swift.responder`. So the directive's ``reason`` selects the negative
outcome (reject/fail) but is not itself re-emitted on the wire; that is a documented
modelling boundary of the frozen corpus interface, not a dropped value.

All references are invented and anonymised (epic #65, anonymization rule).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from gwlab.swift import corpus
from gwlab.swift.directive import decode_directive
from gwlab.swift.scheduler import Event
from gwlab.swift.tokenizer import parse_envelope, tokenize

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.directive import Directive
    from gwlab.swift.model import SwiftMessage

#: The SVCQM reply ``QREMOTE`` that carries responses back to our ``GW.SEC.REPLY``.
REPLY_QUEUE = "GW.SEC.REPLY"

#: Instruction MT -> its matching settlement-confirmation MT (§5). A clean
#: settlement ends in this confirmation; an unknown instruction MT is a loud error.
_CONFIRMATION_FOR: dict[str, str] = {"540": "544", "541": "545", "542": "546", "543": "547"}

#: The ``:70E:`` narrative qualifier the generator stamps the directive under.
_DIRECTIVE_QUALIFIER = "DECL"


class ResponderError(ValueError):
    """Raised when an instruction cannot be planned (never a silent guess)."""


def directive_of(instruction_text: str) -> Directive:
    """Extract and decode the processing directive from an instruction envelope.

    Tokenizes the instruction, finds the ``:70E::DECL//`` narrative field, and
    decodes its **first** value line (the generator stamps ``GWDIR ...`` there,
    ahead of any size padding). Raises :class:`ResponderError` if the directive
    field is absent — the responder never invents a handling.
    """
    _mt, block4 = parse_envelope(instruction_text)
    fields = tokenize(block4).find("70E", _DIRECTIVE_QUALIFIER)
    if not fields:
        message = "instruction carries no :70E::DECL// directive narrative"
        raise ResponderError(message)
    first_line = fields[0].value.splitlines()[0]
    return decode_directive(first_line)


def _event(now: datetime, cumulative_delay: int, c: float, body: str) -> Event:
    """Build an :class:`Event` for ``body`` at ``now + cumulative_delay / c`` seconds."""
    emit_at = now + timedelta(seconds=cumulative_delay / c)
    return Event(emit_at=emit_at, queue=REPLY_QUEUE, body=body)


def _status_event(
    now: datetime, cumulative: int, c: float, rela: str, status: tuple[str, str]
) -> Event:
    """A timed MT548 status advice carrying one ``:25D:`` ``status`` echoing ``rela``."""
    return _event(now, cumulative, c, corpus.render_status(rela=rela, statuses=[status]))


def plan(instruction: SwiftMessage, directive: Directive, now: datetime, c: float) -> list[Event]:
    """Compute ``instruction``'s ordered lifecycle response plan under ``directive``.

    Maps the directive outcome to a response sequence, each event timed at the
    cumulative per-stage delay divided by the compression factor ``c``:

    - ``clean``  -> ``IPRC//PACK`` @ ``d_ack``, ``MTCH//MACH`` @ ``d_ack+d_match``,
      the matching confirmation @ ``d_ack+d_match+d_settle``;
    - ``reject`` -> ``IPRC//REJT`` @ ``d_ack``;
    - ``fail``   -> ``IPRC//PACK`` @ ``d_ack``, ``MTCH//MACH`` @ ``d_ack+d_match``,
      ``SETT//PENF`` @ ``d_ack+d_match+d_settle``.

    Every response's ``:20C::RELA//`` echoes ``instruction.seme``. Raises
    :class:`ResponderError` if the instruction has no ``SEME`` to echo, if a clean
    settlement's instruction MT has no matching confirmation, or on an unknown
    outcome — no silent mismatch.
    """
    rela = instruction.seme
    if rela is None:
        message = "instruction has no SEME for responses to echo via RELA"
        raise ResponderError(message)
    d_ack, d_match, d_settle = directive.delay_vector
    outcome = directive.outcome

    if outcome == "clean":
        confirmation_mt = _CONFIRMATION_FOR.get(instruction.mt)
        if confirmation_mt is None:
            message = f"no confirmation MT for instruction MT {instruction.mt!r}"
            raise ResponderError(message)
        confirmation = corpus.render_confirmation(mt=confirmation_mt, rela=rela)
        return [
            _status_event(now, d_ack, c, rela, ("IPRC", "PACK")),
            _status_event(now, d_ack + d_match, c, rela, ("MTCH", "MACH")),
            _event(now, d_ack + d_match + d_settle, c, confirmation),
        ]
    if outcome == "reject":
        return [_status_event(now, d_ack, c, rela, ("IPRC", "REJT"))]
    if outcome == "fail":
        return [
            _status_event(now, d_ack, c, rela, ("IPRC", "PACK")),
            _status_event(now, d_ack + d_match, c, rela, ("MTCH", "MACH")),
            _status_event(now, d_ack + d_match + d_settle, c, rela, ("SETT", "PENF")),
        ]
    message = f"unknown directive outcome {outcome!r}"
    raise ResponderError(message)
