"""The streaming collector's in-memory settlement picture (design §5).

:class:`SettlementState` is the incremental analog of the batch
:mod:`gwlab.swift.collect`: instead of browsing both audit queues end to end on
every pass and folding the whole retained window, it folds each message **once**
as it arrives and maintains a per-SEME picture in memory. The single
state->metrics seam is :meth:`SettlementState.report`, which produces the same
:class:`~gwlab.swift.collect.DayReport` the batch collector does, so the existing
exposition renders it unchanged.

Four design rules from §5 shape it:

- **Incremental == batch.** Each SEME's *folded* lifecycle is kept in memory and
  advanced one message at a time via :func:`gwlab.swift.lifecycle.apply_response`
  — the very transition rule the batch :func:`gwlab.swift.lifecycle.fold` drives.
  Applying responses in arrival order through that one shared rule yields the same
  ``current`` / ``hops`` / ``anomalies`` a full re-fold of the correlated set
  would, so :meth:`report` reads the cached state directly and never re-folds
  (the O(state)-per-sweep cost that dominated at scale is gone).
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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift import lifecycle, window
from gwlab.swift.collect import Anomaly, DayReport
from gwlab.swift.correlate import REASON_ORPHAN
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
    """One instruction and its incrementally folded lifecycle.

    An entry is only ever created *from* an instruction, so ``instruction`` is
    always present (never ``None``) — responses advance an existing entry's cached
    ``state`` or become orphan candidates, they never create one. ``state`` is the
    live fold, maintained in place by :func:`gwlab.swift.lifecycle.apply_response`;
    ``response_count`` tracks how many responses have been folded in (for the
    ``scanned`` population gauge) without retaining the messages themselves.
    """

    instruction: SwiftMessage
    state: InstructionState
    response_count: int = 0


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

        An instruction initialises (or refreshes) its SEME's entry — creating the
        cached ``INSTRUCTED`` lifecycle on first sight and draining any responses
        that arrived ahead of it — while a duplicate audit copy refreshes the
        instruction without resetting the fold. A response advances the cached
        lifecycle of the entry its ``RELA`` names, or — when no such instruction is
        in state — is held as an orphan candidate for the window-gated check in
        :meth:`report`. A SEME-less instruction cannot be keyed or correlated and
        is skipped (the parser/sweep surfaces it as a decode anomaly, not here).
        """
        if message.is_instruction:
            seme = message.seme
            if seme is None:
                return
            entry = self._by_seme.get(seme)
            if entry is None:
                entry = _Entry(instruction=message, state=lifecycle.initial_state(message))
                self._by_seme[seme] = entry
                self._drain_early_responses(seme, entry)
            else:
                entry.instruction = message  # duplicate audit copy: keep the fold
            return

        rela = message.rela
        if rela is None:
            self._orphan_candidates.append(message)
            return
        entry = self._by_seme.get(rela)
        if entry is None:
            self._orphan_candidates.append(message)
            return
        self._apply(entry, message)

    def _apply(self, entry: _Entry, response: SwiftMessage) -> None:
        """Advance an entry's cached lifecycle by one response and count it."""
        lifecycle.apply_response(entry.state, response)
        entry.response_count += 1

    def _drain_early_responses(self, seme: str, entry: _Entry) -> None:
        """Fold in any responses that were held before this instruction arrived.

        Rare per the correlation invariant, but when it happens the held responses
        must land on the now-present entry so the incremental result matches a
        batch fold of the same set; the rest stay orphan candidates.
        """
        held = [message for message in self._orphan_candidates if message.rela == seme]
        if not held:
            return
        self._orphan_candidates = [
            message for message in self._orphan_candidates if message.rela != seme
        ]
        for response in held:
            self._apply(entry, response)

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
            if entry.state.current in _TERMINAL and not window.in_window(
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

        Reads each entry's **cached** folded lifecycle directly — no re-fold, no
        re-correlate — and collects transition + window-gated orphan + recorded
        decode anomalies, then computes the aged-out attention set. ``scanned`` is
        the current in-state message **population** (instructions + folded
        responses + held orphan candidates), not a per-pass browse count — the
        scan-cost gauge is superseded by the §7 self-metrics.
        """
        states: list[InstructionState] = []
        anomalies: list[Anomaly] = []
        for entry in self._by_seme.values():
            state = entry.state
            anomalies.extend(_transition_anomalies(state))
            states.append(state)

        for message in self._orphan_candidates:
            if window.in_window(message.put_time, now, horizon):
                detail = f"response references no sent instruction ({REASON_ORPHAN})"
                anomalies.append(Anomaly("orphan", message.rela or "", detail))

        anomalies.extend(self._recorded)
        aged_out = [state for state in states if window.is_aged_out(state, now, horizon)]
        scanned = sum(1 + entry.response_count for entry in self._by_seme.values()) + len(
            self._orphan_candidates
        )
        return DayReport(states=states, aged_out=aged_out, anomalies=anomalies, scanned=scanned)
