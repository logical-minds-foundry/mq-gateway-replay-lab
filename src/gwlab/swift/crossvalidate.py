"""The cross-validation report: diff the two independent lifecycle views per SEME.

The collector derives each instruction's settlement lifecycle from the two audit
queues (source #1); the gateway app derives the same lifecycle from its own live
put/get path (source #2). Both emit their result in the common
:class:`~gwlab.swift.endstate.EndState` shape, keyed by ``SEME`` — and "two views"
only validates anything if they are actually **compared** (load-simulation design
§2.1).

:func:`cross_validate` keys both sets by ``SEME`` and diffs them **per
instruction**: where both views agree on the end state it counts an agreement;
every other case is a named :class:`Divergence` — a state mismatch, or an
instruction present in one view but absent from the other. Agreement is the
success signal; each divergence is a named, investigable defect (the same "named
sets, not just counts" discipline as the attention sets). An aggregate-only match
would let per-instruction disagreements cancel out, so the diff is per
instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift.endstate import end_state

if TYPE_CHECKING:
    from gwlab.swift.endstate import EndState
    from gwlab.swift.lifecycle import InstructionState
    from gwlab.swift.reference import State

#: An instruction the collector saw but the gateway app never booked.
KIND_MISSING_IN_APP = "missing-in-app"
#: An instruction the gateway app booked but the collector never saw.
KIND_MISSING_IN_COLLECTOR = "missing-in-collector"
#: An instruction both views saw, but at different end states.
KIND_STATE_MISMATCH = "state-mismatch"


@dataclass(frozen=True)
class Divergence:
    """One instruction on which the two lifecycle views disagree.

    ``seme`` names the instruction; ``collector`` / ``app`` are that view's end
    state, or ``None`` when the view never saw the instruction. ``kind`` is one of
    ``"missing-in-app"`` (collector-only), ``"missing-in-collector"`` (app-only),
    or ``"state-mismatch"`` (both saw it, at different states).
    """

    seme: str
    collector: State | None
    app: State | None
    kind: str


@dataclass(frozen=True)
class CrossValidation:
    """The diff of the two lifecycle views: how many agree, and every divergence.

    ``agreeing`` is the count of SEMEs both views saw at the same end state;
    ``total`` is the number of distinct SEMEs across both views (so
    ``agreeing + len(divergences) == total``); ``divergences`` names every
    per-instruction disagreement.
    """

    agreeing: int
    total: int
    divergences: list[Divergence]


def collector_end_states(states: list[InstructionState]) -> list[EndState]:
    """Reduce the collector's folded instructions to the common end-state shape.

    Maps each :class:`~gwlab.swift.lifecycle.InstructionState` through
    :func:`~gwlab.swift.endstate.end_state`, so the collector side produces the
    same record shape the gateway app's
    :func:`~gwlab.swift.gateway_app.app_end_states` emits — the shape
    :func:`cross_validate` diffs.
    """
    return [end_state(state) for state in states]


def cross_validate(collector: list[EndState], app: list[EndState]) -> CrossValidation:
    """Diff the collector-derived and app-derived end states, keyed by ``SEME``.

    For each SEME: present in both at the same state → an agreement; present in
    both at different states → a ``"state-mismatch"`` divergence; present in the
    collector only → ``"missing-in-app"``; present in the app only →
    ``"missing-in-collector"``. Collector-known SEMEs are diffed first (in input
    order), then the app-only SEMEs, so the report order is stable.
    """
    collector_by = {record.seme: record for record in collector}
    app_by = {record.seme: record for record in app}

    agreeing = 0
    divergences: list[Divergence] = []
    for seme, collector_record in collector_by.items():
        app_record = app_by.get(seme)
        if app_record is None:
            divergences.append(Divergence(seme, collector_record.state, None, KIND_MISSING_IN_APP))
        elif collector_record.state == app_record.state:
            agreeing += 1
        else:
            divergences.append(
                Divergence(
                    seme,
                    collector_record.state,
                    app_record.state,
                    KIND_STATE_MISMATCH,
                )
            )
    for seme, app_record in app_by.items():
        if seme not in collector_by:
            divergences.append(Divergence(seme, None, app_record.state, KIND_MISSING_IN_COLLECTOR))

    total = agreeing + len(divergences)
    return CrossValidation(agreeing=agreeing, total=total, divergences=divergences)


def format_report(cv: CrossValidation) -> str:
    """Render a short human-readable cross-validation report.

    The headline is ``agreeing/total`` agreements; each divergence is then named
    by its SEME, with the two views' states (or ``-`` where a view never saw it).
    """
    header = f"cross-validation: {cv.agreeing}/{cv.total} agree"
    if not cv.divergences:
        return f"{header}, 0 divergences"
    lines = [f"{header}, {len(cv.divergences)} divergences:"]
    for divergence in cv.divergences:
        collector = divergence.collector if divergence.collector is not None else "-"
        app = divergence.app if divergence.app is not None else "-"
        lines.append(f"  {divergence.seme}: {divergence.kind} (collector={collector} app={app})")
    return "\n".join(lines)
