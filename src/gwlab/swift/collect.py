"""The read-only collector: browse both audit queues into one :class:`DayReport`.

This is the graceful-degradation boundary (spec §12). It browses the two audit
queues this repo owns — the retained MT540-543 instructions and the streamed
MT548 / MT544-547 responses — and folds them into the day's settlement picture.
Every message is parsed inside a guard so that **one bad message never aborts the
run**: a structurally unparseable body (a ``TokenizeError``, which is a
``ValueError``) or a body the model rejects (an unqualified ``:25D:`` raises
``ValueError``) becomes a ``malformed`` anomaly and the scan continues.

All four anomaly kinds land in one unified channel (spec §12 / §13 — anomalies
are surfaced, never swallowed):

- ``malformed`` — the body could not be tokenized or modelled.
- ``field-violation`` — a field value breaks its catalog spec (the message is
  still kept and folded; a bad value is not a lost message).
- ``orphan`` — a response references no sent instruction.
- ``transition`` — a folded response asserted a backward/unreachable state.

Direction is tagged by queue of origin, not by the parsed MT: instructions come
from ``instruct_audit``, responses from ``status_audit``. The collector is
strictly read-only — it browses; it never puts, gets, or commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift import correlate, fields, lifecycle, model, tokenizer, window

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.messaging import Message, Messaging
    from gwlab.swift.model import SwiftMessage
    from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet


@dataclass(frozen=True)
class Anomaly:
    """One surfaced anomaly, whatever its kind — the unified triage record.

    ``kind`` is ``"malformed"`` / ``"field-violation"`` / ``"orphan"`` /
    ``"transition"``; ``reference`` is the SEME/RELA/offending id (always
    populated so every anomaly can be chased); ``detail`` is human-readable.
    """

    kind: str
    reference: str
    detail: str


@dataclass(frozen=True)
class DayReport:
    """The collected settlement picture for the window.

    ``states`` is every folded instruction; ``aged_out`` the non-terminal subset
    at the edge of retention; ``anomalies`` the unified channel of all four kinds;
    ``scanned`` the count of messages browsed (the scan-cost thread, spec §11).
    """

    states: list[lifecycle.InstructionState]
    aged_out: list[lifecycle.InstructionState]
    anomalies: list[Anomaly]
    scanned: int


def _decode_and_parse(message: Message, queue: str) -> tuple[SwiftMessage | None, list[Anomaly]]:
    """Parse one browsed message; return ``(parsed_or_none, anomalies_found)``.

    Field violations are surfaced as anomalies but do not discard the message. A
    tokenize/model failure returns ``(None, [malformed])`` so the caller skips the
    message and carries on — ``TokenizeError`` is a ``ValueError`` subclass, so the
    one ``except`` catches both the structural and the model-level failure.
    """
    anomalies: list[Anomaly] = []
    try:
        text = message.body.decode()
        mt, block4 = tokenizer.parse_envelope(text)
        tree = tokenizer.tokenize(block4)
        for violation in fields.validate_tree(tree):
            detail = f"{violation.value!r} fails {violation.reason}"
            anomalies.append(Anomaly("field-violation", violation.key, detail))
        put_time = message.put_time
        if put_time is None:
            error = "audited message carries no put-time"
            raise ValueError(error)
        parsed = model.parse_message(mt, tree, put_time)
        return parsed, anomalies
    except ValueError as exc:
        reference = message.msg_id.hex() or queue
        return None, [Anomaly("malformed", reference, str(exc))]


def collect(
    messaging: Messaging,
    queues: SwiftQueueSet,
    now: datetime,
    horizon: Horizon,
) -> DayReport:
    """Browse both audit queues and fold them into a :class:`DayReport` (read-only)."""
    anomalies: list[Anomaly] = []
    instructions: list[SwiftMessage] = []
    responses: list[SwiftMessage] = []
    scanned = 0
    for queue, sink in ((queues.instruct_audit, instructions), (queues.status_audit, responses)):
        for message in messaging.browse(queue):
            scanned += 1
            parsed, found = _decode_and_parse(message, queue)
            anomalies.extend(found)
            if parsed is None:
                continue
            if not window.in_window(parsed.put_time, now, horizon):
                continue
            sink.append(parsed)

    correlated, orphans = correlate.correlate(instructions, responses)
    for orphan in orphans:
        reference = orphan.rela or orphan.message.seme or ""
        detail = f"response references no sent instruction ({orphan.reason})"
        anomalies.append(Anomaly("orphan", reference, detail))

    states: list[lifecycle.InstructionState] = []
    for item in correlated:
        state = lifecycle.fold(item)
        for transition in state.anomalies:
            detail = f"{transition.attempted} from {transition.from_state} ({transition.reason})"
            anomalies.append(Anomaly("transition", transition.seme, detail))
        states.append(state)

    aged_out = [state for state in states if window.is_aged_out(state, now, horizon)]
    return DayReport(states=states, aged_out=aged_out, anomalies=anomalies, scanned=scanned)
