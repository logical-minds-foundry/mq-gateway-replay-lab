"""Deterministic encode/decode for the responder's processing directive.

A directive tells the SVC responder how to handle one instruction: the settlement
``outcome``, an optional ISO 15022 ``reason`` code, and a per-stage
``delay_vector`` (sim-time seconds). The generator (:mod:`gwlab.swift.generator`)
stamps it into a ``:70E:`` narrative field; the responder (epic #65, task #120)
reads it back. Both share this one codec so the write and the read cannot drift.

The wire form is one compact line::

    GWDIR clean  0 0 0
    GWDIR fail LACK 30 60 0

``GWDIR`` tag, the ``outcome``, the ``reason`` (empty for ``clean`` — hence the
double space), then the three delays ``Δ_ack Δ_match Δ_settle``. It round-trips
exactly. The line rides inside a metadata region the collector never reads (it
correlates on ``SEME``/``RELA`` only), so the directive is inert from its view.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The three settlement outcomes the responder can enact.
OUTCOMES = frozenset({"clean", "reject", "fail"})

#: The line tag that marks a directive inside the narrative field.
_TAG = "GWDIR"


class DirectiveError(ValueError):
    """Raised when a line cannot be decoded as a directive (never a silent guess)."""


@dataclass(frozen=True)
class Directive:
    """How the responder must handle one instruction.

    ``outcome`` is one of :data:`OUTCOMES`; ``reason`` is an ISO 15022 reason code
    (e.g. ``LACK``/``MONY``) or ``""`` for a clean settlement; ``delay_vector`` is
    the per-stage sim-time delay ``(Δ_ack, Δ_match, Δ_settle)`` in seconds.
    """

    outcome: str
    reason: str
    delay_vector: tuple[int, int, int]


def encode_directive(directive: Directive) -> str:
    """Render a :class:`Directive` to its one-line wire form."""
    d_ack, d_match, d_settle = directive.delay_vector
    return f"{_TAG} {directive.outcome} {directive.reason} {d_ack} {d_match} {d_settle}"


def decode_directive(line: str) -> Directive:
    """Parse a directive line back into a :class:`Directive`.

    Raises :class:`DirectiveError` on any malformed line — an unknown tag, an
    unknown outcome, the wrong field count, or a non-integer delay.
    """
    tokens = line.split()
    if not tokens or tokens[0] != _TAG:
        message = f"not a directive line: {line!r}"
        raise DirectiveError(message)
    body = tokens[1:]
    if len(body) == 4:  # clean: the reason field is empty, so it collapses on split
        outcome, reason, delays = body[0], "", body[1:]
    elif len(body) == 5:  # reason present
        outcome, reason, delays = body[0], body[1], body[2:]
    else:
        message = f"directive has {len(body)} fields, expected 4 or 5: {line!r}"
        raise DirectiveError(message)
    if outcome not in OUTCOMES:
        message = f"unknown outcome {outcome!r}: {line!r}"
        raise DirectiveError(message)
    try:
        numbers = [int(value) for value in delays]
    except ValueError as exc:
        message = f"non-integer delay in directive: {line!r}"
        raise DirectiveError(message) from exc
    d_ack, d_match, d_settle = numbers
    return Directive(outcome, reason, (d_ack, d_match, d_settle))
