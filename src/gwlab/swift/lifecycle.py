"""The lifecycle fold: replay an instruction's responses into a current state.

Given a :class:`~gwlab.swift.correlate.Correlated` (an instruction plus its
put-time-ordered responses), :func:`fold` walks the responses from
``INSTRUCTED`` and advances **monotonically along the reachability graph**
(:data:`gwlab.swift.reference.REACHABILITY`). This is what makes it robust to the
untidy shape of real traffic (spec §4.2):

- **A message may assert several states.** One MT548 can carry multiple ``:25D:``
  statuses (e.g. ``IPRC//PACK`` and ``MTCH//MACH``); each is applied, in
  documented precedence (progressive rank), so one message can advance more than
  one hop.
- **Advancement is monotonic.** A state is applied only if it is reachable —
  forward — from the current state.
- **Repeats are no-ops, not anomalies.** Re-asserting the current state (a re-sent
  advice) changes nothing and is not flagged.
- **Backward / unreachable assertions are flagged**, never silently applied — the
  v2 analog of v1's Algorithm-3 anomaly.

The reachability graph does double duty here: it is both the state model and the
validity check every asserted state is tested against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gwlab.swift import reference
from gwlab.swift.reference import State

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.correlate import Correlated
    from gwlab.swift.model import SwiftMessage

REASON_BACKWARD = "backward"
REASON_UNREACHABLE = "unreachable"

# Rank orders the progressive states; the terminal-negatives sit after SETTLED so
# a lower-ranked assertion from an in-flight state reads as "backward".
_RANK: dict[State, int] = {
    State.INSTRUCTED: 0,
    State.ACKNOWLEDGED: 1,
    State.MATCHED: 2,
    State.SETTLED: 3,
    State.FAILED: 4,
    State.REJECTED: 4,
}


@dataclass(frozen=True)
class Hop:
    """One accepted, advancing transition and when it happened."""

    state: State
    at: datetime


@dataclass(frozen=True)
class TransitionAnomaly:
    """An asserted transition that was refused (backward or unreachable)."""

    seme: str
    attempted: State
    from_state: State
    reason: str


@dataclass(frozen=True)
class InstructionState:
    """The folded lifecycle of one instruction."""

    seme: str
    current: State
    hops: list[Hop] = field(default_factory=list)
    anomalies: list[TransitionAnomaly] = field(default_factory=list)


def triggers_for(message: SwiftMessage) -> list[State]:
    """Return the states a message asserts, via the reference tables.

    A confirmation (MT544-547) asserts SETTLED. A status advice asserts the state
    each of its ``:25D:`` statuses maps to in :data:`reference.STATUS_TRIGGERS`;
    documented-but-non-advancing statuses (e.g. ``SETT//PEND``) map to nothing.
    An instruction asserts nothing (INSTRUCTED is the fold's start).
    """
    if message.is_confirmation:
        return [State.SETTLED]
    asserted: list[State] = []
    for status in message.statuses:
        state = reference.STATUS_TRIGGERS.get((status.qualifier, status.value))
        if state is not None:
            asserted.append(state)
    return asserted


def fold(correlated: Correlated) -> InstructionState:
    """Replay an instruction's responses into its current state, hops, anomalies."""
    instruction = correlated.instruction
    seme = instruction.seme or ""
    current = State.INSTRUCTED
    hops: list[Hop] = [Hop(State.INSTRUCTED, instruction.put_time)]
    anomalies: list[TransitionAnomaly] = []

    for response in correlated.responses:
        for asserted in sorted(triggers_for(response), key=lambda state: _RANK[state]):
            if asserted == current:
                continue  # re-asserting the current state: a benign no-op
            if asserted in reference.REACHABILITY[current]:
                current = asserted
                hops.append(Hop(asserted, response.put_time))
                continue
            reason = (
                REASON_BACKWARD
                if reference.REACHABILITY[current] and _RANK[asserted] < _RANK[current]
                else REASON_UNREACHABLE
            )
            anomalies.append(TransitionAnomaly(seme, asserted, current, reason))

    return InstructionState(seme, current, hops, anomalies)
