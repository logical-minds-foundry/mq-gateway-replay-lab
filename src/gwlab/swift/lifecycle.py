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


@dataclass
class InstructionState:
    """The folded lifecycle of one instruction.

    Mutable so the fold can be advanced **incrementally**, one response at a time
    (:func:`apply_response`), as well as replayed in a batch (:func:`fold`). Both
    drivers share the single transition implementation, so the maintained
    streaming picture and a full re-fold stay behaviourally identical.
    """

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


def initial_state(instruction: SwiftMessage) -> InstructionState:
    """The fold's starting point: ``INSTRUCTED`` with the instruction's opening hop.

    Shared by the batch :func:`fold` and the streaming
    :class:`gwlab.swift.stream.state.SettlementState`, so an incrementally
    maintained lifecycle begins exactly where a full re-fold would.
    """
    seme = instruction.seme or ""
    hops = [Hop(State.INSTRUCTED, instruction.put_time)]
    return InstructionState(seme, State.INSTRUCTED, hops, [])


def apply_response(state: InstructionState, response: SwiftMessage) -> None:
    """Advance ``state`` in place by one response — the single transition rule.

    For each state the response asserts (in progressive-rank order): a re-assertion
    of the current state is a benign no-op; a forward-reachable assertion advances
    the state and appends a :class:`Hop`; anything else is refused and appended to
    ``state.anomalies`` as a backward/unreachable :class:`TransitionAnomaly`. This
    is the one implementation :func:`fold` and the incremental streaming path both
    drive, so they stay behaviourally identical.
    """
    for asserted in sorted(triggers_for(response), key=lambda state_: _RANK[state_]):
        if asserted == state.current:
            continue  # re-asserting the current state: a benign no-op
        if asserted in reference.REACHABILITY[state.current]:
            state.current = asserted
            state.hops.append(Hop(asserted, response.put_time))
            continue
        reason = (
            REASON_BACKWARD
            if reference.REACHABILITY[state.current] and _RANK[asserted] < _RANK[state.current]
            else REASON_UNREACHABLE
        )
        state.anomalies.append(TransitionAnomaly(state.seme, asserted, state.current, reason))


def fold(correlated: Correlated) -> InstructionState:
    """Replay an instruction's responses into its current state, hops, anomalies."""
    state = initial_state(correlated.instruction)
    for response in correlated.responses:
        apply_response(state, response)
    return state
