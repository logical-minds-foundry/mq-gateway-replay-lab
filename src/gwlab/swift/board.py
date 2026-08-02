"""Code-generate the v2 SWIFT settlement Grafana board (JSON).

The v2 analog of v1's :mod:`gwlab.dashboard`: it assembles a Grafana dashboard
model whose panels query the ``swift_settlement_*``, ``swift_audit_*``, and
``swift_collector_*`` Prometheus series the streaming collector
(:mod:`gwlab.swift.stream.serve_collector`) publishes, plus the standard IBM MQ
exporter's ``ibmmq_qmgr_*`` filesystem series. :mod:`gwlab.swift.deploy_http`
upserts the returned model into Grafana.

Design notes — the board reads as **registers**, each answering one question:

- **Register A (normal day):** a settlement-*efficiency* headline (settled / all,
  a ratio) plus fails broken out per macro dimension — ``reason_family`` and
  ``msg_type`` — EACH on its own panel and its own scale, never co-plotted with
  settled volume. This replaces the retired settled-dominated state-distribution
  bar, where the tall ``settled`` bar buried every signal beside it.
- **Register C (infra health):** the audit copy watching its own plumbing — audit
  queue depth and fill ratio, the oldest-open-instruction age (the expiry-window
  signal), an expiry-window-stability timeseries of depth over time, and the audit
  copy's disk/log *footprint* from the MQ exporter's filesystem-in-use series.
- The **top KPI row is operational** (volume, replay/attention, anomalies,
  population, and two collector-health headlines).
- All value queries aggregate away ``instance`` (``max``/``sum``), so a Native-HA
  failover that swaps the scraped instance never doubles a value.
- The old per-pass scan-cost gauge (``swift_settlement_collect_seconds``) is
  retired; the streaming collector's ``swift_collector_cycle_seconds`` (fold cost,
  off the scrape path) and ``swift_collector_bootstrap_seconds`` (recovery time)
  carry the cost story in the collector-health section.

(Register B — per-dimension deviation scoring — arrives in Phase 2.)

Pure dict-building, no I/O — so it is fully deterministic and unit-tested; the
network deploy lives in ``deploy_http.py``.
"""

from __future__ import annotations

from typing import Any

_ANOMALY_DIST = "max by (kind) (swift_settlement_anomalies)"
_TOTAL = "sum(max by (state) (swift_settlement_state))"
_AGING = "max(swift_settlement_aging)"
_ANOMALIES_TOTAL = "max(swift_settlement_anomalies_total)"
_POPULATION = "max(swift_settlement_scanned)"

# --- Register A — normal-day settlement efficiency ----------------------------
# The headline is a *ratio* (settled / all), not a settled count, so a mostly-green
# day reads as "97% efficient" rather than a tall settled bar that buries the fails.
# Fails are then broken out per macro dimension, EACH on its own panel and its own
# scale — never co-plotted with settled volume, which would dwarf them. Queries use
# the Task-6 segmented series ``swift_settlement_segment_total{state,reason_family,
# msg_type}``; ``clamp_min`` guards the empty-population divide-by-zero.
_EFFICIENCY = (
    '100 * sum(swift_settlement_segment_total{state="settled"})'
    " / clamp_min(sum(swift_settlement_segment_total), 1)"
)
_FAILS_BY_FAMILY = (
    'sum by (reason_family) (swift_settlement_segment_total{state=~"failed|rejected"})'
)
_FAILS_BY_MSGTYPE = 'sum by (msg_type) (swift_settlement_segment_total{state=~"failed|rejected"})'

# --- Register C — audit-queue infra health ------------------------------------
# The MQ-native audit copy's own health: how deep the audit queues sit, how close
# to full, the age of the oldest still-open instruction (the expiry-window signal),
# and the disk/log *footprint* of the audit copy. All ``max by (queue)`` so a
# Native-HA failover instance-swap never doubles a value.
_AUDIT_DEPTH = "max by (queue) (swift_audit_queue_depth)"
_AUDIT_FILL = "max by (queue) (swift_audit_queue_fill_ratio)"
_OLDEST_AGE = "max(swift_audit_oldest_age_seconds)"
# Expiry-window stability: the SAME depth series over time — a steady band is a
# healthy rolling window (puts age out and are reaped in step); a sustained climb
# is a backup or a retention leak in the audit copy.
_AUDIT_DEPTH_OVER_TIME = "max by (queue) (swift_audit_queue_depth)"
# Disk/log consumption from the standard IBM MQ Prometheus exporter's
# filesystem-in-use series — the audit copy's *footprint* on the queue manager's
# data and recovery-log stores. This is declare-and-verify against the lab-owned
# MQ exporter (the endorsed boundary crossing), NOT the node-exporter disk-IO
# contention series (that lives in the collector-health section below).
_QMGR_FS_IN_USE = 'max(ibmmq_qmgr_queue_manager_file_system_bytes_in_use{{qmgr="{qmgr}"}})'
_LOG_FS_IN_USE = 'max(ibmmq_qmgr_log_file_system_bytes_in_use{{qmgr="{qmgr}"}})'

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
    # repeat the per-state counts (those belong to Register A below), so the tiles
    # and the registers never duplicate each other.
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

    # Register A — normal-day settlement efficiency. The headline is a RATIO
    # (settled / all); fails are broken out per macro dimension, each on its own
    # panel and its own scale, never co-plotted with the settled majority. This is
    # what replaces the retired settled-dominated state-distribution bar.
    register_a_header = (
        "## Register A — normal-day settlement efficiency\n"
        "A settled/all efficiency headline, then fails broken out by reason family "
        "and by message type — each on its own scale, never buried under settled volume."
    )
    panels += [
        _text(8, _grid(0, 7, 24, 2), register_a_header),
        _stat(
            9,
            "Settlement Efficiency (settled / all)",
            _grid(0, 9, 8, 7),
            _EFFICIENCY,
            ds_uid,
            unit="percent",
            color="green",
        ),
        _bargauge(
            10,
            "Fails by reason family",
            _grid(8, 9, 8, 7),
            _FAILS_BY_FAMILY,
            "{{reason_family}}",
            ds_uid,
        ),
        _bargauge(
            11,
            "Fails by message type",
            _grid(16, 9, 8, 7),
            _FAILS_BY_MSGTYPE,
            "{{msg_type}}",
            ds_uid,
        ),
        # Anomalies by kind — kept from the operational hero (the state bar is gone).
        _bargauge(12, "Anomalies by kind", _grid(0, 16, 24, 9), _ANOMALY_DIST, "{{kind}}", ds_uid),
        # Lifecycle latencies — full width and taller so the per-hop legend table fits.
        _timeseries(13, "Lifecycle latency (mean)", _grid(0, 25, 24, 9), [mean_target], ds_uid),
        _timeseries(14, "Lifecycle latency (p95)", _grid(0, 34, 24, 9), [p95_target], ds_uid),
    ]

    # Register C — audit-queue infra health: the audit copy's own plumbing. Depth,
    # fill, oldest-open age, the expiry-window-stability trend, and the disk/log
    # footprint from the MQ exporter's filesystem-in-use series (declare-and-verify;
    # NOT node-disk-IO contention, which is the collector-health concern below).
    register_c_header = (
        "## Register C — audit-queue infra health\n"
        "Audit-queue depth and fill, the oldest-open-instruction age (expiry window), "
        "the depth-over-time stability trend, and the audit copy's disk/log footprint."
    )
    fs_targets = [
        _target(_QMGR_FS_IN_USE.format(qmgr=qmgr), "qmgr data filesystem", ds_uid),
        _target(_LOG_FS_IN_USE.format(qmgr=qmgr), "recovery log filesystem", ds_uid),
    ]
    panels += [
        _text(15, _grid(0, 43, 24, 2), register_c_header),
        _bargauge(16, "Audit queue depth", _grid(0, 45, 8, 7), _AUDIT_DEPTH, "{{queue}}", ds_uid),
        _bargauge(
            17, "Audit queue fill ratio", _grid(8, 45, 8, 7), _AUDIT_FILL, "{{queue}}", ds_uid
        ),
        _stat(
            18,
            "Oldest open instruction age",
            _grid(16, 45, 8, 7),
            _OLDEST_AGE,
            ds_uid,
            unit="s",
            color="yellow",
        ),
        _timeseries(
            19,
            "Audit queue depth over time (expiry-window stability)",
            _grid(0, 52, 12, 8),
            [_target(_AUDIT_DEPTH_OVER_TIME, "{{queue}}", ds_uid)],
            ds_uid,
            unit="short",
        ),
        _timeseries(
            20,
            "Audit disk & log footprint (MQ exporter)",
            _grid(12, 52, 12, 8),
            fs_targets,
            ds_uid,
            unit="decbytes",
        ),
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
        _text(21, _grid(0, 60, 24, 2), health_header),
        _stat(
            22,
            "Recovery time (last cold-reload)",
            _grid(0, 62, 6, 7),
            _BOOTSTRAP,
            ds_uid,
            unit="s",
            color="blue",
        ),
        _stat(23, "Sweep backlog", _grid(6, 62, 6, 7), _BACKLOG, ds_uid, color="text"),
        _timeseries(
            24,
            "Fold cycle time (p95, off the scrape path)",
            _grid(12, 62, 12, 7),
            [_target(_CYCLE_P95, "cycle p95", ds_uid)],
            ds_uid,
        ),
        _timeseries(
            25,
            "Messages ingested (rate)",
            _grid(0, 69, 12, 7),
            [_target(_INGEST_RATE, "ingest/s", ds_uid)],
            ds_uid,
            unit="cps",
        ),
        _timeseries(
            26,
            "Disk IO utilisation",
            _grid(12, 69, 12, 7),
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
