"""Code-generate the v2 SWIFT settlement Grafana board (JSON).

The v2 analog of v1's :mod:`gwlab.dashboard`: it assembles a Grafana dashboard
model — stat tiles, a hero distribution, latency timeseries, and a collector-health
section — whose panels query the ``swift_settlement_*`` and ``swift_collector_*``
Prometheus series the streaming collector (:mod:`gwlab.swift.stream.serve_collector`)
publishes. :mod:`gwlab.swift.deploy_http` upserts the returned model into Grafana.

Design notes:

- The **top KPI row is operational** (volume, replay/attention, anomalies,
  population, and two collector-health headlines) — it deliberately does *not*
  repeat the per-state counts, which live in the state-distribution hero, so the
  tiles and the distribution never duplicate each other.
- All value queries aggregate away ``instance`` (``max``/``sum``), so a Native-HA
  failover that swaps the scraped instance never doubles a tile.
- The old per-pass scan-cost gauge (``swift_settlement_collect_seconds``) is
  retired; the streaming collector's ``swift_collector_cycle_seconds`` (fold cost,
  off the scrape path) and ``swift_collector_bootstrap_seconds`` (recovery time)
  carry the cost story in the collector-health section.

Pure dict-building, no I/O — so it is fully deterministic and unit-tested; the
network deploy lives in ``deploy_http.py``.
"""

from __future__ import annotations

from typing import Any

# The six lifecycle states, in display order (the funnel + the two negatives).
_STATES = ("instructed", "acknowledged", "matched", "settled", "rejected", "failed")

# State distribution, deduped across instances (a failover swaps the scraped
# instance; ``max by (state)`` keeps one value per state either way).
_STATE_DIST = "max by (state) (swift_settlement_state)"
_ANOMALY_DIST = "max by (kind) (swift_settlement_anomalies)"
_TOTAL = "sum(max by (state) (swift_settlement_state))"
_AGING = "max(swift_settlement_aging)"
_ANOMALIES_TOTAL = "max(swift_settlement_anomalies_total)"
_POPULATION = "max(swift_settlement_scanned)"

# Collector-health (the watcher watching itself, §7).
_BOOTSTRAP = "max(swift_collector_bootstrap_seconds)"
_BACKLOG = "max(swift_collector_cycle_backlog)"
_INGEST_RATE = "sum(rate(swift_collector_messages_ingested_total[5m]))"
_PUBLISH_AGE = "time() - max(swift_collector_last_publish_timestamp_seconds)"
_CYCLE_P95 = (
    "histogram_quantile(0.95, sum by (le) (rate(swift_collector_cycle_seconds_bucket[5m])))"
)
# Disk busy-time fraction per box, from the node exporter — disk contention under
# real volume (load-sim §9.2).
_DISK_IO = "rate(node_disk_io_time_seconds_total[5m])"


def _ds(ds_uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": ds_uid}


def _target(expr: str, legend: str, ds_uid: str, **extra: Any) -> dict[str, Any]:
    target: dict[str, Any] = {
        "refId": "A",
        "expr": expr,
        "legendFormat": legend,
        "datasource": _ds(ds_uid),
    }
    target.update(extra)
    return target


def _grid(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def _text(pid: int, grid: dict[str, int], markdown: str) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "text",
        "gridPos": grid,
        "options": {"mode": "markdown", "content": markdown},
        "transparent": True,
    }


def _stat(
    pid: int,
    title: str,
    grid: dict[str, int],
    expr: str,
    ds_uid: str,
    *,
    unit: str = "short",
    color: str = "text",
) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "gridPos": grid,
        "datasource": _ds(ds_uid),
        "fieldConfig": {
            "defaults": {"unit": unit, "color": {"mode": "fixed", "fixedColor": color}}
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "value",
            "graphMode": "area",
            "textMode": "auto",
        },
        "targets": [_target(expr, title, ds_uid)],
    }


def _bargauge(
    pid: int, title: str, grid: dict[str, int], expr: str, legend: str, ds_uid: str
) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "bargauge",
        "title": title,
        "gridPos": grid,
        "datasource": _ds(ds_uid),
        "fieldConfig": {"defaults": {"unit": "short", "color": {"mode": "palette-classic"}}},
        "options": {
            "orientation": "horizontal",
            "displayMode": "gradient",
            "reduceOptions": {"calcs": ["lastNotNull"]},
        },
        "targets": [_target(expr, legend, ds_uid, instant=True)],
    }


def _compression_var() -> dict[str, Any]:
    """A dashboard variable ``$compression`` so the operator recovers sim-time durations.

    Under a time-compressed run (load-sim §7) the collector records *real*
    (compressed) durations; multiplying the duration panels by this factor ``C``
    recovers the sim-time latencies. Default ``"1"`` is real time (no transform).
    """
    return {
        "name": "compression",
        "type": "textbox",
        "label": "Time-compression factor (C)",
        "query": "1",
        "current": {"text": "1", "value": "1", "selected": True},
        "options": [{"text": "1", "value": "1", "selected": True}],
    }


def _timeseries(
    pid: int,
    title: str,
    grid: dict[str, int],
    targets: list[dict[str, Any]],
    ds_uid: str,
    *,
    unit: str = "s",
) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "gridPos": grid,
        "datasource": _ds(ds_uid),
        "fieldConfig": {"defaults": {"unit": unit, "custom": {"drawStyle": "line"}}},
        "options": {"legend": {"displayMode": "table", "placement": "bottom"}},
        "targets": targets,
    }


def build_settlement_board(qmgr: str, ds_uid: str = "${DS_PROMETHEUS}") -> dict[str, Any]:
    """Return the Grafana dashboard model for the SWIFT settlement board.

    ``qmgr`` names the queue manager in the title; ``ds_uid`` is the Prometheus
    datasource uid the panels query (a Grafana variable by default).
    """
    header = (
        f"# SWIFT settlement lifecycle — {qmgr}\n"
        "MQ-native audit of MT540–548: instructions folded through the settlement "
        "state machine by the streaming collector, maintained incrementally in memory."
    )
    # Operational KPI row — volume + attention + collector health. It does NOT
    # repeat the per-state counts (those are the distribution hero below), so the
    # tiles and the distribution never duplicate each other.
    kpis = [
        ("Total instructions", _TOTAL, "blue", "short"),
        ("Aging (replay)", _AGING, "yellow", "short"),
        ("Anomalies", _ANOMALIES_TOTAL, "purple", "short"),
        ("Population", _POPULATION, "text", "short"),
        ("Ingest rate", _INGEST_RATE, "green", "cps"),
        ("Publish age", _PUBLISH_AGE, "text", "s"),
    ]

    # Duration panels are scaled by $compression so the operator recovers sim-time
    # latencies from the real (compressed) durations the collector records (§7).
    mean_target = _target("swift_settlement_hop_mean_seconds * $compression", "{{hop}}", ds_uid)
    p95_target = _target("swift_settlement_hop_p95_seconds * $compression", "{{hop}}", ds_uid)

    panels: list[dict[str, Any]] = [_text(1, _grid(0, 0, 24, 3), header)]
    for index, (title, expr, color, unit) in enumerate(kpis):
        panels.append(
            _stat(2 + index, title, _grid(index * 4, 3, 4, 4), expr, ds_uid, color=color, unit=unit)
        )
    panels += [
        # Hero: the state-distribution funnel (settled dominates at scale), and
        # anomalies by kind beside it.
        _bargauge(
            8, "Settlement state distribution", _grid(0, 7, 12, 9), _STATE_DIST, "{{state}}", ds_uid
        ),
        _bargauge(9, "Anomalies by kind", _grid(12, 7, 12, 9), _ANOMALY_DIST, "{{kind}}", ds_uid),
        # Lifecycle latencies — full width and taller so the per-hop legend table fits.
        _timeseries(10, "Lifecycle latency (mean)", _grid(0, 16, 24, 9), [mean_target], ds_uid),
        _timeseries(11, "Lifecycle latency (p95)", _grid(0, 25, 24, 9), [p95_target], ds_uid),
    ]

    # Collector-health section — the watcher watching itself (§7). Recovery time,
    # the fold-cost distribution (off the scrape path), throughput, backlog, and
    # disk contention under real volume.
    health_header = (
        "## Collector health — the watcher watching itself\n"
        "Recovery (cold-reload) time, the incremental fold cost off the scrape path, "
        "ingest throughput, sweep backlog, and disk contention under real volume."
    )
    panels += [
        _text(12, _grid(0, 34, 24, 2), health_header),
        _stat(
            13,
            "Recovery time (last cold-reload)",
            _grid(0, 36, 6, 7),
            _BOOTSTRAP,
            ds_uid,
            unit="s",
            color="blue",
        ),
        _stat(14, "Sweep backlog", _grid(6, 36, 6, 7), _BACKLOG, ds_uid, color="text"),
        _timeseries(
            15,
            "Fold cycle time (p95, off the scrape path)",
            _grid(12, 36, 12, 7),
            [_target(_CYCLE_P95, "cycle p95", ds_uid)],
            ds_uid,
        ),
        _timeseries(
            16,
            "Messages ingested (rate)",
            _grid(0, 43, 12, 7),
            [_target(_INGEST_RATE, "ingest/s", ds_uid)],
            ds_uid,
            unit="cps",
        ),
        _timeseries(
            17,
            "Disk IO utilisation",
            _grid(12, 43, 12, 7),
            [_target(_DISK_IO, "{{instance}}", ds_uid)],
            ds_uid,
            unit="percentunit",
        ),
    ]
    return {
        "uid": "gwlab-swift-settlement",
        "title": f"Gateway — SWIFT settlement ({qmgr})",
        "tags": ["gwlab", "swift", "settlement"],
        "timezone": "utc",
        "schemaVersion": 39,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "templating": {"list": [_compression_var()]},
        "panels": panels,
    }
