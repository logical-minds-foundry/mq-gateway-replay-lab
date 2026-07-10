"""Code-templated Grafana board for one counterparty's gateway flow.

Built the lab's way: the queue manager is a **parameter, never a literal** (pass
whichever QM is up, from one source), and the queue names come from
``QueueSet`` — so re-generating against a different arm simply produces the board
for that arm's QM.

Every panel comes from the **standard IBM MQ Prometheus exporter** (the archives
are ordinary MQ queues it already scrapes), so most of the value needs no
bespoke exporter: sent-vs-confirmed depth, send/confirm rate (the first
derivative), unconfirmed backlog, oldest-unconfirmed age, average time on the
reply queue, and CAPEXPRY expiry. The *which specific trades are unconfirmed*
table needs the gwlab reconcile exporter and is a follow-on.
"""

from __future__ import annotations

from typing import Any

from gwlab.config import QueueSet

_SCHEMA_VERSION = 39


def _ds(ds_uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": ds_uid}


def _q(metric: str, qmgr: str, queue: str) -> str:
    return f'max(ibmmq_queue_{metric}{{qmgr="{qmgr}",queue="{queue}"}})'


def _rate(metric: str, qmgr: str, queue: str) -> str:
    return f'sum(rate(ibmmq_queue_{metric}{{qmgr="{qmgr}",queue="{queue}"}}[5m]))'


def _target(ref: str, expr: str, legend: str, ds_uid: str) -> dict[str, Any]:
    return {"refId": ref, "datasource": _ds(ds_uid), "expr": expr, "legendFormat": legend}


def _panel(
    pid: int,
    title: str,
    ptype: str,
    grid: tuple[int, int, int, int],
    targets: list[dict[str, Any]],
    ds_uid: str,
    description: str,
    unit: str | None,
) -> dict[str, Any]:
    x, y, w, h = grid
    defaults: dict[str, Any] = {"custom": {"drawStyle": "line", "fillOpacity": 10, "lineWidth": 2}}
    if unit is not None:
        defaults["unit"] = unit
    return {
        "id": pid,
        "type": ptype,
        "title": title,
        "description": description,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "datasource": _ds(ds_uid),
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
        "targets": targets,
    }


def build_dashboard(qmgr: str, counterparty: str, ds_uid: str = "prometheus") -> dict[str, Any]:
    """Return the Grafana dashboard dict for one counterparty on ``qmgr``.

    ``qmgr`` is a parameter, never hardcoded — pass whichever queue manager is up.
    """
    qs = QueueSet.for_counterparty(counterparty)
    backlog = f"{_q('depth', qmgr, qs.send_archive)} - {_q('depth', qmgr, qs.reply_archive)}"
    panels = [
        _panel(
            1,
            "Sent vs Confirmed (archive depth)",
            "timeseries",
            (0, 0, 12, 8),
            [
                _target("A", _q("depth", qmgr, qs.send_archive), "Sent (archive)", ds_uid),
                _target("B", _q("depth", qmgr, qs.reply_archive), "Confirmed (archive)", ds_uid),
            ],
            ds_uid,
            "Both archives are ordinary MQ queues the standard exporter scrapes.",
            None,
        ),
        _panel(
            2,
            "Send vs Confirm rate",
            "timeseries",
            (12, 0, 12, 8),
            [
                _target("A", _rate("mqput_mqput1_count", qmgr, qs.send_archive), "Sent/s", ds_uid),
                _target(
                    "B", _rate("mqput_mqput1_count", qmgr, qs.reply_archive), "Confirmed/s", ds_uid
                ),
            ],
            ds_uid,
            "First derivative: the rate of puts to each archive.",
            "reqps",
        ),
        _panel(
            3,
            "Unconfirmed backlog (sent - confirmed)",
            "timeseries",
            (0, 8, 12, 8),
            [_target("A", backlog, "Unconfirmed", ds_uid)],
            ds_uid,
            "The gap: climbs when confirmations are missing, drains on replay.",
            None,
        ),
        _panel(
            4,
            "Oldest unconfirmed age",
            "stat",
            (12, 8, 6, 4),
            [_target("A", _q("oldest_message_age", qmgr, qs.send_archive), "Oldest", ds_uid)],
            ds_uid,
            "Age of the oldest message still in the outbound archive.",
            "s",
        ),
        _panel(
            5,
            "Avg time on reply queue",
            "stat",
            (18, 8, 6, 4),
            [_target("A", _q("average_queue_time_seconds", qmgr, qs.reply), "Avg", ds_uid)],
            ds_uid,
            "Mean time a confirmation waits before the gateway consumes it.",
            "s",
        ),
        _panel(
            6,
            "Expired copies (CAPEXPRY retention)",
            "stat",
            (12, 12, 12, 4),
            [_target("A", _q("expired_messages", qmgr, qs.send_archive), "Expired", ds_uid)],
            ds_uid,
            "Streamed copies discarded by the rolling CAPEXPRY TTL.",
            None,
        ),
    ]
    return {
        "uid": f"gw-{counterparty.lower()}",
        "title": f"MQ Gateway - {counterparty} (archive reconciliation)",
        "tags": ["mq-gateway", "gwlab", counterparty],
        "timezone": "browser",
        "schemaVersion": _SCHEMA_VERSION,
        "refresh": "10s",
        "time": {"from": "now-30m", "to": "now"},
        "panels": panels,
    }
