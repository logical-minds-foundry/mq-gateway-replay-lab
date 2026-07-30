"""The common per-instruction end-state record both lifecycle views emit.

The collector derives each instruction's settlement lifecycle from the two audit
queues (source #1); the gateway app derives the same lifecycle from its own live
put/get path (:mod:`gwlab.swift.gateway_app`, source #2). For the two independent
derivations to be **cross-validated** (load-simulation design §2.1) they must emit
their result in one common, comparable shape keyed by ``SEME`` — that shape is
:class:`EndState`.

A comparison step (#122) diffs the two sets of :class:`EndState` records **per
instruction** and names every divergence, so the two code paths become an
automatable correctness assertion for the architecture. The record is deliberately
minimal — the final state and the timestamp it became terminal — because that is
exactly what a per-``SEME`` diff needs; an aggregate-only shape would let
per-instruction disagreements cancel out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift.reference import TERMINAL_NEGATIVE, State

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.lifecycle import InstructionState

#: The states at which a lifecycle has resolved: SETTLED plus the two
#: terminal-negatives (mirrors :data:`gwlab.swift.window._TERMINAL`). Only these
#: carry a ``terminal_at``; every other (in-flight) state leaves it ``None``.
TERMINAL: frozenset[State] = TERMINAL_NEGATIVE | {State.SETTLED}


@dataclass(frozen=True)
class EndState:
    """One instruction's final lifecycle state, in the common cross-validation shape.

    ``seme`` keys the record; ``state`` is the folded ``current`` state;
    ``terminal_at`` is the put-time of the hop that reached a terminal state, or
    ``None`` when the instruction is still in flight.
    """

    seme: str
    state: State
    terminal_at: datetime | None


def end_state(instruction_state: InstructionState) -> EndState:
    """Reduce a folded :class:`~gwlab.swift.lifecycle.InstructionState` to an :class:`EndState`.

    ``state`` is ``instruction_state.current``. ``terminal_at`` is the time of the
    hop that reached that state when it is terminal (SETTLED / REJECTED / FAILED),
    else ``None`` — an in-flight instruction has no terminal timestamp.
    """
    current = instruction_state.current
    if current not in TERMINAL:
        return EndState(seme=instruction_state.seme, state=current, terminal_at=None)
    terminal_at = next(hop.at for hop in reversed(instruction_state.hops) if hop.state == current)
    return EndState(seme=instruction_state.seme, state=current, terminal_at=terminal_at)
