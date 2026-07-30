"""Drive a realistic multi-day settlement window through the audit queues.

This is the end-to-end driver that ties the corpus, the responder and the
collector together: it instructs a batch of MT540-543 messages across simulated
settlement days, plays the counterparty's response streams for each (including
late T+1/T+2 confirmations, rejects and fails), leaves a handful in-flight to age
out, optionally injects an orphaned status, and returns the collected
:class:`~gwlab.swift.collect.DayReport`.

Everything is **deterministic**: every put-time is derived from the caller's
``start`` / ``now`` plus fixed offsets (via :class:`~datetime.timedelta`); nothing
here reads the wall clock or draws a random number, so a simulated window is fully
reproducible — the property Task 11 (epic #65) exists to demonstrate.

A :class:`Scenario` is a list of :class:`InstructionSpec`s. Each spec names a
lifecycle ``path``: either a responder recipe (one of
:data:`gwlab.swift.responder._RECIPES` — clean-settle, rejected-at-validation,
matched-then-failing, acknowledged-aging) whose responses are played onto the
status audit, or :data:`SILENT_PATH` for an instruction that draws no response at
all and is left to age out. The orphan anomaly is injected once per scenario, via
:attr:`Scenario.inject_orphan`, as a status whose ``:20C::RELA//`` echoes no sent
instruction.

All references are invented and anonymised (``GWSEC…``); nothing here is
client-identifiable (epic #65, anonymization rule).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from gwlab.messaging import Message
from gwlab.swift import corpus, model, responder
from gwlab.swift.collect import collect

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.messaging import Messaging
    from gwlab.swift.collect import DayReport
    from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

#: :attr:`InstructionSpec.path` value marking an instruction that draws no
#: response — put and then left in-flight, so it ages out once older than the
#: horizon. Any other ``path`` is a responder recipe played by :func:`respond`.
SILENT_PATH = "silent"

#: The MT put for every simulated instruction (an anonymised receive-free-of-
#: payment). The lifecycle the collector folds is driven by the responses, not by
#: the instruction sub-type, so one representative instruction MT is sufficient.
_INSTRUCTION_MT = "541"

#: The ``:20C::RELA//`` carried by the injected orphan status: a 16x reference that
#: matches no instruction ``SEME`` in any realistic scenario, so the collector
#: surfaces it as an ``orphan`` anomaly.
_ORPHAN_RELA = "GWSECORPHAN00000"

#: The status the injected orphan carries. Any catalog-valid status works; an
#: acknowledged (``IPRC//PACK``) advice is the plainest.
_ORPHAN_STATUS: tuple[tuple[str, str], ...] = (("IPRC", "PACK"),)


@dataclass(frozen=True)
class InstructionSpec:
    """One instruction to put, and how the counterparty responds to it.

    ``seme`` is the instruction's own reference (invented, anonymised). ``path``
    is either a responder recipe name or :data:`SILENT_PATH`. ``instructed_offset_days``
    puts the instruction at ``start + offset`` days (T, T+1, …). ``response_delays``
    is handed to :class:`gwlab.swift.responder.Plan`: one inter-response gap per
    step of the recipe (large gaps push a confirmation into T+1/T+2); it is unused
    for a silent spec.
    """

    seme: str
    path: str
    instructed_offset_days: int = 0
    response_delays: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class Scenario:
    """A batch of instruction specs plus whether to inject an orphan status."""

    specs: list[InstructionSpec]
    inject_orphan: bool = True


def _drive_instruction(
    messaging: Messaging,
    queues: SwiftQueueSet,
    spec: InstructionSpec,
    start: datetime,
) -> None:
    """Put ``spec``'s instruction and, unless silent, play its response stream."""
    put_time = start + timedelta(days=spec.instructed_offset_days)
    text = corpus.render_instruction(_INSTRUCTION_MT, spec.seme)
    messaging.put(queues.instruct_audit, Message(body=text.encode(), put_time=put_time))
    if spec.path == SILENT_PATH:
        return
    instruction = model.parse_swift(text, put_time)
    plan = responder.Plan(path=spec.path, delays_seconds=spec.response_delays)
    responder.respond(messaging, queues, instruction, plan, put_time)


def _inject_orphan(messaging: Messaging, queues: SwiftQueueSet, now: datetime) -> None:
    """Put one status whose ``RELA`` echoes no instruction — an orphan anomaly."""
    text = corpus.render_status(rela=_ORPHAN_RELA, statuses=list(_ORPHAN_STATUS))
    messaging.put(queues.status_audit, Message(body=text.encode(), put_time=now))


def run_window(
    messaging: Messaging,
    queues: SwiftQueueSet,
    horizon: Horizon,
    scenario: Scenario,
    start: datetime,
    now: datetime,
) -> DayReport:
    """Drive ``scenario`` through the audit queues and return the collected report.

    Each spec's instruction is put at ``start + offset`` days and, unless silent,
    its responder recipe is played (late confirmations land via large delays). If
    ``scenario.inject_orphan`` an extra unmatched status is put. Everything is
    committed, then the read-only collector browses both queues into the
    :class:`~gwlab.swift.collect.DayReport` for the window ending at ``now``.
    """
    for spec in scenario.specs:
        _drive_instruction(messaging, queues, spec, start)
    if scenario.inject_orphan:
        _inject_orphan(messaging, queues, now)
    messaging.commit()
    return collect(messaging, queues, now, horizon)
