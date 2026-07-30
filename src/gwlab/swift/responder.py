"""Play the counterparty: emit an instruction's realistic response sequence.

Given one **driven** instruction, :func:`respond` puts that instruction's
ordered response stream — a run of MT548 status advices, optionally ending in an
MT544-547 settlement confirmation — onto the status-audit queue, exactly as the
account servicer / CSD would. Each response is rendered by the Task 9 corpus
renderers so its Block 4 is catalog-valid, and each carries a ``:20C::RELA//``
that echoes the **driven** instruction's own ``SEME`` (not the corpus's canned
constant), so the collector correlates the responses back to this instruction.

The response *recipe* per lifecycle path is a single table, :data:`_RECIPES`,
whose entries mirror the reachable flows in :func:`gwlab.swift.corpus.all_flows`.
Pacing and timestamps are deterministic: the caller injects a ``start`` datetime
and per-response ``delays_seconds``; put-times advance by the running sum of
those delays, so a simulation over a settlement window is fully reproducible and
never touches the wall clock.

All references are invented and anonymised (``GWSEC…``); nothing here is
client-identifiable (epic #65, anonymization rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from gwlab.messaging import Message
from gwlab.swift import corpus

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.messaging import Messaging
    from gwlab.swift.model import SwiftMessage
    from gwlab.swift.swiftconfig import SwiftQueueSet

#: Discriminators for :attr:`Step.kind`.
_STATUS = "status"
_CONFIRMATION = "confirmation"


@dataclass(frozen=True)
class Step:
    """One response to emit.

    A ``kind == "status"`` step renders an MT548 carrying ``statuses`` (a run of
    ``(qualifier, code)`` pairs, one ``:25D:`` each). A ``kind == "confirmation"``
    step renders the settlement confirmation ``mt`` (one of MT544-547). Only the
    field relevant to ``kind`` is populated; the other keeps its empty default.
    """

    kind: str
    statuses: tuple[tuple[str, str], ...] = ()
    mt: str = ""


@dataclass(frozen=True)
class Plan:
    """Which lifecycle path to drive, and the inter-response pacing.

    ``path`` is a :attr:`gwlab.swift.corpus.Flow.name` — one of the reachable
    paths in :data:`_RECIPES`. ``delays_seconds[i]`` is the gap before the
    ``i``-th response; its length must equal the path's step count (a mismatch is
    a loud :class:`ValueError`, never a silent truncation).
    """

    path: str
    delays_seconds: list[float] = field(default_factory=list)


#: The ordered response recipe per reachable lifecycle path. These mirror the
#: well-formed flows in :func:`gwlab.swift.corpus.all_flows` exactly:
#: clean-settle accepts, matches, then confirms (MT545); rejected-at-validation
#: rejects at input; matched-then-failing accepts, matches, then fails to settle;
#: acknowledged-aging is accepted and then left in-flight.
_RECIPES: dict[str, tuple[Step, ...]] = {
    "clean-settle": (
        Step(kind=_STATUS, statuses=(("IPRC", "PACK"),)),
        Step(kind=_STATUS, statuses=(("MTCH", "MACH"),)),
        Step(kind=_CONFIRMATION, mt="545"),
    ),
    "rejected-at-validation": (Step(kind=_STATUS, statuses=(("IPRC", "REJT"),)),),
    "matched-then-failing": (
        Step(kind=_STATUS, statuses=(("IPRC", "PACK"),)),
        Step(kind=_STATUS, statuses=(("MTCH", "MACH"),)),
        Step(kind=_STATUS, statuses=(("SETT", "PENF"),)),
    ),
    "acknowledged-aging": (Step(kind=_STATUS, statuses=(("IPRC", "PACK"),)),),
}


def _render(step: Step, rela: str) -> str:
    """Render ``step`` to a full envelope whose ``:20C::RELA//`` echoes ``rela``."""
    if step.kind == _STATUS:
        return corpus.render_status(rela=rela, statuses=list(step.statuses))
    return corpus.render_confirmation(mt=step.mt, rela=rela)


def respond(
    messaging: Messaging,
    queues: SwiftQueueSet,
    instruction: SwiftMessage,
    plan: Plan,
    start: datetime,
) -> None:
    """Emit ``plan``'s response sequence for ``instruction`` onto the status audit.

    Each response's ``RELA`` echoes ``instruction.seme``; the ``i``-th response is
    stamped ``start + sum(plan.delays_seconds[: i + 1])`` (via ``timedelta``), so
    put-times advance strictly and deterministically. Every response is put under
    one unit of work, committed once at the end.

    Raises :class:`ValueError` if ``plan.path`` is unknown, if
    ``plan.delays_seconds`` does not match the path's step count, or if
    ``instruction`` carries no ``SEME`` to echo — no silent mismatch.
    """
    recipe = _RECIPES.get(plan.path)
    if recipe is None:
        known = ", ".join(sorted(_RECIPES))
        message = f"unknown responder path {plan.path!r}; known paths: {known}"
        raise ValueError(message)
    if len(plan.delays_seconds) != len(recipe):
        message = (
            f"path {plan.path!r} has {len(recipe)} response(s) but "
            f"{len(plan.delays_seconds)} delay(s) were given"
        )
        raise ValueError(message)
    rela = instruction.seme
    if rela is None:
        message = "driven instruction has no SEME for responses to echo via RELA"
        raise ValueError(message)

    cumulative = 0.0
    for step, delay in zip(recipe, plan.delays_seconds, strict=True):
        cumulative += delay
        put_time = start + timedelta(seconds=cumulative)
        text = _render(step, rela)
        messaging.put(queues.status_audit, Message(body=text.encode(), put_time=put_time))
    messaging.commit()
