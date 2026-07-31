"""The collector watching itself — self-observability metrics (design §7).

The streaming collector publishes metrics about **its own** health alongside the
settlement metrics, extending the ``swift_settlement_collect_seconds`` seed (#121).
:class:`CollectorStats` folds one sweep's timing and throughput via
:meth:`~CollectorStats.observe`; :func:`render_collector_metrics` renders the five
``swift_collector_*`` series in the same line-builder style as
:func:`gwlab.swift.cliswift.render_exposition` (both share
:func:`gwlab.swift.exposition.append_gauge`):

- ``swift_collector_bootstrap_seconds`` — first-lap / post-restart rebuild time;
  the recovery-time number, now a first-class alertable gauge.
- ``swift_collector_cycle_seconds`` — incremental-sweep duration as a **histogram**,
  so the *distribution* of sweep costs is visible, not just the last value.
- ``swift_collector_messages_ingested_total`` — a throughput counter.
- ``swift_collector_cycle_backlog`` — new messages seen in the last sweep; a proxy
  for whether the single-threaded loop is keeping up.
- ``swift_collector_last_publish_timestamp_seconds`` — so a stalled or
  mid-bootstrap collector is visible as snapshot staleness rather than silence.

These supersede the batch collector's per-pass scan-cost gauge, which has no
per-scrape meaning once the fold is decoupled from the scrape (design §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from gwlab.swift.exposition import append_gauge

#: Cumulative histogram upper bounds (``le``) for the sweep-duration distribution,
#: in seconds. Chosen to straddle the sub-second steady-state sweep (design §4) and
#: the minutes-long first-lap rebuild (§10), so both regimes land in distinct buckets.
_CYCLE_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)

_BOOTSTRAP_METRIC = "swift_collector_bootstrap_seconds"
_BOOTSTRAP_HELP = "First-lap / post-restart in-memory rebuild time (the recovery-time metric)."
_CYCLE_METRIC = "swift_collector_cycle_seconds"
_CYCLE_HELP = "Incremental-sweep duration distribution, seconds (fold cost, off the scrape path)."
_INGESTED_METRIC = "swift_collector_messages_ingested_total"
_INGESTED_HELP = "Messages ingested across all sweeps since start (the throughput counter)."
_BACKLOG_METRIC = "swift_collector_cycle_backlog"
_BACKLOG_HELP = "New messages seen in the last sweep (is the single-threaded loop keeping up?)."
_PUBLISH_TS_METRIC = "swift_collector_last_publish_timestamp_seconds"
_PUBLISH_TS_HELP = "Unix time of the last snapshot publish (staleness, not silence, on a stall)."


@dataclass
class CollectorStats:
    """The collector's own health, folded one sweep at a time by :meth:`observe`."""

    bootstrap_seconds: float = 0.0
    cycle_seconds: float = 0.0
    messages_ingested_total: int = 0
    cycle_backlog: int = 0
    last_publish_timestamp: float = 0.0
    #: Sweep count == the histogram's ``_count`` and the "have we bootstrapped yet" flag.
    cycle_count: int = 0
    cycle_sum_seconds: float = 0.0
    cycle_buckets: dict[float, int] = field(
        default_factory=lambda: dict.fromkeys(_CYCLE_BUCKETS, 0)
    )

    def observe(self, *, cycle_seconds: float, ingested: int, now: float) -> None:
        """Fold one sweep's timing and throughput into the running stats.

        The very first sweep is the cold rebuild, so its duration is recorded as
        ``bootstrap_seconds`` (the recovery-time metric); later sweeps leave the
        bootstrap number untouched. ``ingested`` becomes the last sweep's backlog
        and accumulates into the throughput counter, and ``cycle_seconds`` folds
        into both the running histogram and its sum.
        """
        if self.cycle_count == 0:
            self.bootstrap_seconds = cycle_seconds
        self.cycle_seconds = cycle_seconds
        self.messages_ingested_total += ingested
        self.cycle_backlog = ingested
        self.last_publish_timestamp = now
        self.cycle_count += 1
        self.cycle_sum_seconds += cycle_seconds
        for upper_bound in _CYCLE_BUCKETS:
            if cycle_seconds <= upper_bound:
                self.cycle_buckets[upper_bound] += 1


def render_collector_metrics(stats: CollectorStats) -> str:
    """Render :class:`CollectorStats` as the five ``swift_collector_*`` series.

    Pure function of the stats — no clock read, no I/O — so it is fully
    deterministic and unit-tested. The gauge series reuse
    :func:`gwlab.swift.exposition.append_gauge` (shared with
    :func:`gwlab.swift.cliswift.render_exposition`); ``cycle_seconds`` is a
    cumulative histogram and ``messages_ingested_total`` a counter.
    """
    lines: list[str] = []

    append_gauge(lines, _BOOTSTRAP_METRIC, _BOOTSTRAP_HELP, stats.bootstrap_seconds)

    lines.append(f"# HELP {_CYCLE_METRIC} {_CYCLE_HELP}")
    lines.append(f"# TYPE {_CYCLE_METRIC} histogram")
    for upper_bound in _CYCLE_BUCKETS:
        count = stats.cycle_buckets[upper_bound]
        lines.append(f'{_CYCLE_METRIC}_bucket{{le="{upper_bound}"}} {count}')
    lines.append(f'{_CYCLE_METRIC}_bucket{{le="+Inf"}} {stats.cycle_count}')
    lines.append(f"{_CYCLE_METRIC}_sum {stats.cycle_sum_seconds}")
    lines.append(f"{_CYCLE_METRIC}_count {stats.cycle_count}")

    lines.append(f"# HELP {_INGESTED_METRIC} {_INGESTED_HELP}")
    lines.append(f"# TYPE {_INGESTED_METRIC} counter")
    lines.append(f"{_INGESTED_METRIC} {stats.messages_ingested_total}")

    append_gauge(lines, _BACKLOG_METRIC, _BACKLOG_HELP, stats.cycle_backlog)
    append_gauge(lines, _PUBLISH_TS_METRIC, _PUBLISH_TS_HELP, stats.last_publish_timestamp)

    return "\n".join(lines) + "\n"
