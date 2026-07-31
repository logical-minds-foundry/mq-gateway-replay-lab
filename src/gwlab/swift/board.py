"""Code-generate the v2 SWIFT settlement Grafana board (JSON).

The v2 analog of v1's :mod:`gwlab.dashboard`: it assembles a Grafana dashboard
model — the same *display* approach as the v1 boards (stat tiles, a hero
distribution, latency timeseries) rather than the standalone HTML render — whose
panels query the ``swift_settlement_*`` Prometheus series the exporter
(:mod:`gwlab.swift.serve_http`) publishes. :mod:`gwlab.swift.deploy_http` upserts
the returned model into Grafana.

Pure dict-building, no I/O — so it is fully deterministic and unit-tested; the
network deploy lives in ``deploy_http.py``.
"""

from __future__ import annotations

from typing import Any

# The six lifecycle states, in display order (the funnel + the two negatives).
_STATES = ("instructed", "acknowledged", "matched", "settled", "rejected", "failed")
_IN_FLIGHT = "instructed|acknowledged|matched"


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

    Under a time-compressed run (load-sim §7) the exporter records *real*
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
        "MQ-native audit of MT540–548: instructions folded through the "
        "settlement state machine, read live off the streaming audit queues."
    )
    # KPI tiles, laid out left-to-right across the row (each 4 columns wide).
    kpis = [
        ("Settled", 'swift_settlement_state{state="settled"}', "green"),
        ("In flight", f'sum(swift_settlement_state{{state=~"{_IN_FLIGHT}"}})', "blue"),
        ("Rejected", 'swift_settlement_state{state="rejected"}', "orange"),
        ("Failed", 'swift_settlement_state{state="failed"}', "red"),
        ("Anomalies", "swift_settlement_anomalies_total", "purple"),
        ("Scanned", "swift_settlement_scanned", "text"),
    ]
    # Duration panels are scaled by $compression so the operator recovers sim-time
    # latencies from the real (compressed) durations the exporter records (§7).
    mean_target = _target("swift_settlement_hop_mean_seconds * $compression", "{{hop}}", ds_uid)
    p95_target = _target("swift_settlement_hop_p95_seconds * $compression", "{{hop}}", ds_uid)

    panels: list[dict[str, Any]] = [_text(1, _grid(0, 0, 24, 3), header)]
    for index, (title, expr, color) in enumerate(kpis):
        panels.append(_stat(2 + index, title, _grid(index * 4, 3, 4, 4), expr, ds_uid, color=color))
    panels += [
        # Hero: the state-distribution funnel, and anomalies by kind beside it.
        _bargauge(
            8,
            "State distribution",
            _grid(0, 7, 12, 9),
            "swift_settlement_state",
            "{{state}}",
            ds_uid,
        ),
        _bargauge(
            9,
            "Anomalies by kind",
            _grid(12, 7, 12, 9),
            "swift_settlement_anomalies",
            "{{kind}}",
            ds_uid,
        ),
        # Lifecycle latencies.
        _timeseries(10, "Lifecycle latency (mean)", _grid(0, 16, 12, 8), [mean_target], ds_uid),
        _timeseries(11, "Lifecycle latency (p95)", _grid(12, 16, 12, 8), [p95_target], ds_uid),
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
