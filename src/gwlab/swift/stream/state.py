"""The streaming collector's in-memory settlement picture (design §5).

:class:`SettlementState` is the incremental analog of the batch
:mod:`gwlab.swift.collect`: instead of browsing both audit queues end to end on
every pass and folding the whole retained window, it folds each message **once**
as it arrives and maintains a per-SEME picture in memory. The single
state->metrics seam is :meth:`SettlementState.report`, which produces the same
:class:`~gwlab.swift.collect.DayReport` the batch collector does, so the existing
exposition renders it unchanged.

Four design rules from §5 shape it:

- **Incremental == batch.** The lifecycle fold is monotonic along the
  reachability graph, so re-folding one instruction's small hop-set on each new
  message yields the same terminal state as folding a full correlated set. Only
  the *driver* changes; :func:`gwlab.swift.lifecycle.fold` is reused verbatim.
- **No pending buffer (the correlation invariant).** When a response is browsed,
  its instruction's audit copy is already in state — unless that instruction has
  expired off the queue. So an unmatched response is never *pending*: it is
  either an orphan (its instruction should still be in-window) or a trailing-edge
  artifact (its instruction has legitimately expired). Unmatched responses are
  therefore held only as *orphan candidates* — a report accumulator, never
  re-correlated against a later instruction.
- **Window-gated orphan.** An unmatched response is flagged ``orphan`` only if it
  is still in-window; an old one is dropped, silently, as a trailing-edge
  artifact (a naive rule would flood false orphans on every cold-reload).
- **Lifecycle-aware retention.** :meth:`age` keeps an instruction while its
  lifecycle is *open*, regardless of age, and purges it only once it is both
  terminal (settled / rejected / failed) *and* past the window.

All four anomaly kinds flow through the one unified channel the batch collector
uses: ``malformed`` / ``field-violation`` (recorded per-message by the sweep via
:func:`gwlab.swift.collect._decode_and_parse` and handed to
:meth:`record_anomalies`), ``orphan`` (window-gated here), and ``transition``
(from the fold). No kind is dropped — a malformed body is a counted anomaly, not
a silent failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from gwlab.swift import lifecycle, window
from gwlab.swift.collect import Anomaly, DayReport
from gwlab.swift.correlate import REASON_ORPHAN, Correlated
from gwlab.swift.reference import TERMINAL_NEGATIVE, State

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.lifecycle import InstructionState
    from gwlab.swift.model import SwiftMessage
    from gwlab.swift.swiftconfig import Horizon

#: A lifecycle is complete once it settles or ends negatively — the retention
#: gate: a complete lifecycle is a candidate for purge once it is also expired.
_TERMINAL: frozenset[State] = TERMINAL_NEGATIVE | {State.SETTLED}


@dataclass
class _Entry:
    """One instruction and the responses folded into its lifecycle.

    An entry is only ever created *from* an instruction, so ``instruction`` is
    always present (never ``None``) — responses attach to an existing entry or
    become orphan candidates, they never create one.
    """

    instruction: SwiftMessage
    responses: list[SwiftMessage] = field(default_factory=list)


def _fold(entry: _Entry) -> InstructionState:
    """Fold one entry's put-time-ordered responses into its current lifecycle."""
    responses = sorted(entry.responses, key=lambda message: message.put_time)
    return lifecycle.fold(Correlated(entry.instruction, responses))


def _transition_anomalies(state: InstructionState) -> list[Anomaly]:
    """Render a folded lifecycle's refused transitions as unified anomalies."""
    return [
        Anomaly(
            "transition",
            transition.seme,
            f"{transition.attempted} from {transition.from_state} ({transition.reason})",
        )
        for transition in state.anomalies
    ]


class SettlementState:
    """The maintained per-SEME settlement picture, folded incrementally."""

    def __init__(self) -> None:
        self._by_seme: dict[str, _Entry] = {}
        self._orphan_candidates: list[SwiftMessage] = []
        self._recorded: list[Anomaly] = []

    def ingest(self, message: SwiftMessage) -> None:
        """Fold one parsed message into the picture (design §5).

        An instruction sets (or refreshes) its SEME's entry; a response attaches
        to the entry its ``RELA`` names, or — when no such instruction is in
        state — is held as an orphan candidate for the window-gated check in
        :meth:`report`. A SEME-less instruction cannot be keyed or correlated and
        is skipped (the parser/sweep surfaces it as a decode anomaly, not here).
        """
        if message.is_instruction:
            seme = message.seme
            if seme is None:
                return
            self._by_seme.setdefault(seme, _Entry(instruction=message)).instruction = message
            return

        rela = message.rela
        if rela is None:
            self._orphan_candidates.append(message)
            return
        entry = self._by_seme.get(rela)
        if entry is None:
            self._orphan_candidates.append(message)
            return
        entry.responses.append(message)

    def record_anomalies(self, anomalies: list[Anomaly]) -> None:
        """Accumulate per-message decode anomalies (malformed / field-violation).

        The sweep decodes each browsed message with
        :func:`gwlab.swift.collect._decode_and_parse` and hands the anomalies it
        finds here, so the streaming report carries the same four-kind unified
        channel as the batch collector.
        """
        self._recorded.extend(anomalies)

    def age(self, now: datetime, horizon: Horizon) -> None:
        """Purge what has left the picture; keep everything still relevant (§5).

        An instruction is purged only when its lifecycle is both terminal *and*
        past the window — an *open* lifecycle is retained regardless of age (a
        matched-but-never-confirmed trade is exactly the attention set). Orphan
        candidates that have aged past the window are dropped as trailing-edge
        artifacts, bounding memory.
        """
        for seme in list(self._by_seme):
            entry = self._by_seme[seme]
            state = _fold(entry)
            if state.current in _TERMINAL and not window.in_window(
                entry.instruction.put_time, now, horizon
            ):
                del self._by_seme[seme]
        self._orphan_candidates = [
            message
            for message in self._orphan_candidates
            if window.in_window(message.put_time, now, horizon)
        ]

    def report(self, now: datetime, horizon: Horizon) -> DayReport:
        """Render the maintained picture into a :class:`DayReport` (the one seam).

        Re-folds each entry, collects transition + window-gated orphan + recorded
        decode anomalies, and computes the aged-out attention set. ``scanned`` is
        the current in-state message **population** (instructions + folded
        responses + held orphan candidates), not a per-pass browse count — the
        scan-cost gauge is superseded by the §7 self-metrics.
        """
        states: list[InstructionState] = []
        anomalies: list[Anomaly] = []
        for entry in self._by_seme.values():
            state = _fold(entry)
            anomalies.extend(_transition_anomalies(state))
            states.append(state)

        for message in self._orphan_candidates:
            if window.in_window(message.put_time, now, horizon):
                detail = f"response references no sent instruction ({REASON_ORPHAN})"
                anomalies.append(Anomaly("orphan", message.rela or "", detail))

        anomalies.extend(self._recorded)
        aged_out = [state for state in states if window.is_aged_out(state, now, horizon)]
        scanned = sum(1 + len(entry.responses) for entry in self._by_seme.values()) + len(
            self._orphan_candidates
        )
        return DayReport(states=states, aged_out=aged_out, anomalies=anomalies, scanned=scanned)
