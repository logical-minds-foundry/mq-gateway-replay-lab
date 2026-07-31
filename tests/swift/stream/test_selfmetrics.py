"""Tests for the streaming collector's self-observability exposition (design §7).

Pure logic: :class:`CollectorStats` is folded per-sweep by ``observe`` and
rendered by ``render_collector_metrics`` into the five ``swift_collector_*``
series, mirroring the batch exposition's line-builder style. No MQ boundary and
no clock read — every quantity is supplied.
"""

from __future__ import annotations

from gwlab.swift.stream.selfmetrics import CollectorStats, render_collector_metrics


def test_render_collector_metrics_emits_the_five_series() -> None:
    out = render_collector_metrics(
        CollectorStats(
            bootstrap_seconds=42.0,
            cycle_seconds=0.18,
            messages_ingested_total=1000,
            cycle_backlog=7,
            last_publish_timestamp=1_700_000_000.0,
        )
    )

    assert "swift_collector_bootstrap_seconds 42.0" in out
    assert "# TYPE swift_collector_cycle_seconds histogram" in out
    assert "swift_collector_messages_ingested_total 1000" in out
    assert "swift_collector_cycle_backlog 7" in out
    assert "swift_collector_last_publish_timestamp_seconds 1700000000.0" in out


def test_render_collector_metrics_has_help_and_type_for_every_series() -> None:
    out = render_collector_metrics(CollectorStats())

    for name in (
        "swift_collector_bootstrap_seconds",
        "swift_collector_cycle_seconds",
        "swift_collector_messages_ingested_total",
        "swift_collector_cycle_backlog",
        "swift_collector_last_publish_timestamp_seconds",
    ):
        assert f"# HELP {name} " in out
    assert "# TYPE swift_collector_bootstrap_seconds gauge" in out
    assert "# TYPE swift_collector_cycle_seconds histogram" in out
    assert "# TYPE swift_collector_messages_ingested_total counter" in out
    assert "# TYPE swift_collector_cycle_backlog gauge" in out
    assert "# TYPE swift_collector_last_publish_timestamp_seconds gauge" in out
    assert out.endswith("\n")


def test_observe_records_the_first_sweep_as_the_bootstrap_time() -> None:
    stats = CollectorStats()

    # The first lap is the cold rebuild — its duration is the recovery-time metric.
    stats.observe(cycle_seconds=90.0, ingested=250_000, now=1_700_000_000.0)
    assert stats.bootstrap_seconds == 90.0

    # Later sweeps update the incremental stats but never the bootstrap number.
    stats.observe(cycle_seconds=0.2, ingested=5, now=1_700_000_060.0)
    assert stats.bootstrap_seconds == 90.0
    assert stats.cycle_seconds == 0.2
    assert stats.messages_ingested_total == 250_005
    assert stats.cycle_backlog == 5  # the last sweep only, not the cumulative total
    assert stats.last_publish_timestamp == 1_700_000_060.0


def test_observe_folds_cycle_durations_into_cumulative_histogram_buckets() -> None:
    stats = CollectorStats()
    stats.observe(cycle_seconds=0.5, ingested=1, now=1.0)  # <= 0.5, > 0.25
    stats.observe(cycle_seconds=2.0, ingested=1, now=2.0)  # > 1.0, <= 2.5

    out = render_collector_metrics(stats)

    assert 'swift_collector_cycle_seconds_bucket{le="0.25"} 0' in out  # neither sample
    assert 'swift_collector_cycle_seconds_bucket{le="0.5"} 1' in out  # the 0.5s sweep
    assert 'swift_collector_cycle_seconds_bucket{le="2.5"} 2' in out  # both sweeps
    assert 'swift_collector_cycle_seconds_bucket{le="+Inf"} 2' in out
    assert "swift_collector_cycle_seconds_count 2" in out
    assert "swift_collector_cycle_seconds_sum 2.5" in out
