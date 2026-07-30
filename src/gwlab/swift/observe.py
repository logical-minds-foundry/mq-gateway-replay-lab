"""Observability metrics: fold a :class:`DayReport` into the day's settlement view.

This is the reporting head of the pipeline (spec §11 / §13). It consumes the
read-only collector's :class:`~gwlab.swift.collect.DayReport` — every folded
instruction, the aged-out subset, and the unified anomaly channel — and derives
the operational picture a human (or a dashboard) reads each day:

- **distribution** — the hero number: how many instructions sit in each of the
  six lifecycle states. Every state is a key, zero when none, so the shape of the
  day is always fully populated (no missing buckets to misread).
- **latencies** — how long each inter-state hop took, aggregated across all
  instructions. Each accepted advancing transition is a :class:`~gwlab.swift.lifecycle.Hop`
  with a timestamp; the delta between adjacent hops of one instruction is one
  latency sample for that hop label. Mean and p95 summarise each label's samples.
- **aging / aged_out / rejected / failed** — the attention sets. ``aging`` is
  every non-terminal instruction sorted oldest-first (the "chase these" list);
  ``aged_out`` is passed through from the collector (non-terminal *and* past the
  retention horizon — the replay candidates); ``rejected`` / ``failed`` are the
  two terminal-negative buckets.
- **anomalies / anomalies_by_kind** — the surfaced-not-swallowed total and its
  per-kind breakdown (all four kinds keyed, zero when none).
- **scanned** — the browse cost, passed through (the scan-cost thread, spec §11).

Everything here is a pure fold over the report plus an explicit ``now``; there is
no clock read and no I/O, so the whole view is deterministic and testable.

**p95 definition.** p95 is the *nearest-rank* percentile: over the samples sorted
ascending, the value at 1-based rank ``ceil(0.95 * n)``. For a single sample the
rank is 1, so p95 equals that sample; there is never a division or interpolation
that could fault on a small or empty set — a label with no samples simply yields
no :class:`LatencyStats`.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift.reference import TERMINAL_NEGATIVE, State

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.collect import DayReport
    from gwlab.swift.lifecycle import InstructionState

#: Non-terminal states — the in-flight lifecycle whose instructions are still
#: "aging". SETTLED plus the two terminal-negatives resolve an instruction.
_NON_TERMINAL: frozenset[State] = frozenset(State) - TERMINAL_NEGATIVE - {State.SETTLED}

#: The four anomaly kinds the collector emits, in reporting order. Every kind is
#: always keyed in ``anomalies_by_kind`` (zero when none) so no bucket is missing.
_ANOMALY_KINDS: tuple[str, ...] = ("malformed", "field-violation", "orphan", "transition")


@dataclass(frozen=True)
class LatencyStats:
    """Mean and p95 of one inter-state hop, in seconds, over all instructions."""

    hop: str
    mean_seconds: float
    p95_seconds: float


@dataclass(frozen=True)
class Metrics:
    """The day's settlement observability view, folded from a :class:`DayReport`."""

    distribution: dict[State, int]
    latencies: list[LatencyStats]
    aging: list[InstructionState]
    aged_out: list[InstructionState]
    rejected: list[InstructionState]
    failed: list[InstructionState]
    anomalies: int
    anomalies_by_kind: dict[str, int]
    scanned: int


def _nearest_rank_p95(samples: list[float]) -> float:
    """Return the nearest-rank p95 of a non-empty ascending-sortable sample list."""
    ordered = sorted(samples)
    rank = math.ceil(0.95 * len(ordered))  # 1-based; >= 1 for any non-empty list
    return ordered[rank - 1]


def _latencies(report: DayReport) -> list[LatencyStats]:
    """Aggregate adjacent-hop timestamp deltas into per-label mean/p95 stats."""
    samples: dict[str, list[float]] = {}
    for state in report.states:
        for earlier, later in zip(state.hops, state.hops[1:], strict=False):
            label = f"{earlier.state}->{later.state}"
            delta = (later.at - earlier.at).total_seconds()
            samples.setdefault(label, []).append(delta)
    return [
        LatencyStats(label, sum(values) / len(values), _nearest_rank_p95(values))
        for label, values in samples.items()
    ]


def metrics(report: DayReport, now: datetime) -> Metrics:
    """Fold a :class:`DayReport` into the day's observability :class:`Metrics`."""
    distribution: dict[State, int] = dict.fromkeys(State, 0)
    for instruction in report.states:
        distribution[instruction.current] += 1

    aging = sorted(
        (state for state in report.states if state.current in _NON_TERMINAL),
        key=lambda state: now - state.hops[0].at,
        reverse=True,  # oldest (largest age) first
    )
    rejected = [state for state in report.states if state.current is State.REJECTED]
    failed = [state for state in report.states if state.current is State.FAILED]

    kind_counts = Counter(anomaly.kind for anomaly in report.anomalies)
    anomalies_by_kind = {kind: kind_counts.get(kind, 0) for kind in _ANOMALY_KINDS}

    return Metrics(
        distribution=distribution,
        latencies=_latencies(report),
        aging=aging,
        aged_out=report.aged_out,
        rejected=rejected,
        failed=failed,
        anomalies=len(report.anomalies),
        anomalies_by_kind=anomalies_by_kind,
        scanned=report.scanned,
    )
