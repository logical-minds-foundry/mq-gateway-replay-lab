"""Code-templated Grafana board for one counterparty's gateway flow on one QM.

Built the lab's way and faithful to the Phase-1 observability mockup: an
**extension of the messaging-flow board**, not a standalone page. Sections, top to
bottom, mirror the mockup:

1. a context header (which QM, which counterparty);
2. the **base layer** — queue-manager health and the live transmit/reply queue
   depths, read from the standard IBM MQ exporter (``ibmmq_*``), the same series
   the lab's per-QM board uses;
3. the **audit-reconciliation extension** — KPI tiles (sent, confirmed,
   unconfirmed, mean confirm latency), the hero *unconfirmed-backlog* chart and a
   *send-vs-confirm rate* companion, the *which-trades* unconfirmed-set table, and
   the reconciliation *scan-cost* panels — read from the gwlab reconcile exporter
   (``gwlab_*``).

The queue manager is a **parameter, never a literal** (pass whichever QM is up),
queue names come from ``QueueSet``, so re-generating against another arm produces
that arm's board. ``gwlab_*`` series are matched on both ``qmgr`` (applied to the
gwlab exporter's scrape target at deploy time) and ``counterparty``.
"""

from __future__ import annotations

from typing import Any

from gwlab.config import QueueSet

_SCHEMA_VERSION = 40


def _ds(ds_uid: str) -> dict[str, str]:
    return {"type": "prometheus", "uid": ds_uid}


def _ibmmq(metric: str, qmgr: str, queue: str) -> str:
    return f'max(ibmmq_queue_{metric}{{qmgr="{qmgr}",queue="{queue}"}})'


def _ibmmq_rate(metric: str, qmgr: str, queue: str) -> str:
    return f'sum(rate(ibmmq_queue_{metric}{{qmgr="{qmgr}",queue="{queue}"}}[5m]))'


def _gwlab(metric: str, qmgr: str, counterparty: str) -> str:
    return f'max(gwlab_{metric}{{qmgr="{qmgr}",counterparty="{counterparty}"}})'


def _target(ref: str, expr: str, legend: str, ds_uid: str, **extra: Any) -> dict[str, Any]:
    target: dict[str, Any] = {
        "refId": ref,
        "datasource": _ds(ds_uid),
        "expr": expr,
        "legendFormat": legend,
    }
    target.update(extra)
    return target


def _grid(x: int, y: int, w: int, h: int) -> dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def _text(pid: int, grid: dict[str, int], markdown: str) -> dict[str, Any]:
    return {
        "id": pid,
        "type": "text",
        "transparent": True,
        "gridPos": grid,
        "options": {"mode": "markdown", "content": markdown},
    }


def _stat(
    pid: int,
    title: str,
    grid: dict[str, int],
    expr: str,
    ds_uid: str,
    *,
    unit: str | None = None,
    mappings: list[dict[str, Any]] | None = None,
    thresholds: list[dict[str, Any]] | None = None,
    description: str = "",
) -> dict[str, Any]:
    defaults: dict[str, Any] = {"mappings": mappings or []}
    if unit is not None:
        defaults["unit"] = unit
    if thresholds is not None:
        defaults["thresholds"] = {"mode": "absolute", "steps": thresholds}
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "gridPos": grid,
        "datasource": _ds(ds_uid),
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "colorMode": "background" if (mappings or thresholds) else "value",
            "graphMode": "none",
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "textMode": "value",
        },
        "targets": [_target("A", expr, title, ds_uid)],
    }


def _timeseries(
    pid: int,
    title: str,
    grid: dict[str, int],
    targets: list[dict[str, Any]],
    ds_uid: str,
    *,
    unit: str | None = None,
    description: str = "",
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "custom": {"drawStyle": "line", "fillOpacity": 12, "lineWidth": 2},
    }
    if unit is not None:
        defaults["unit"] = unit
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "gridPos": grid,
        "datasource": _ds(ds_uid),
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
            "tooltip": {"mode": "multi"},
        },
        "targets": targets,
    }


def _table(
    pid: int, title: str, grid: dict[str, int], expr: str, ds_uid: str, description: str
) -> dict[str, Any]:
    """A table of the unconfirmed set: one row per ``gwlab_unconfirmed_message`` series.

    ``labelsToFields`` turns the per-message labels (msgid, sent, state) into
    columns and the series value (age in seconds) into ``Age (s)``; ``organize``
    drops the plumbing labels and names the columns.
    """
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": description,
        "gridPos": grid,
        "datasource": _ds(ds_uid),
        "fieldConfig": {"defaults": {"custom": {"align": "left"}}, "overrides": []},
        "options": {"showHeader": True},
        "targets": [_target("A", expr, "", ds_uid, format="table", instant=True)],
        "transformations": [
            {"id": "labelsToFields", "options": {}},
            {
                "id": "organize",
                "options": {
                    "excludeByName": {
                        "Time": True,
                        "__name__": True,
                        "counterparty": True,
                        "qmgr": True,
                        "instance": True,
                        "job": True,
                    },
                    "renameByName": {
                        "msgid": "Correlation key (MsgId)",
                        "sent": "Sent",
                        "state": "State",
                        "Value": "Age (s)",
                    },
                    "indexByName": {"msgid": 0, "sent": 1, "Value": 2, "state": 3},
                },
            },
        ],
    }


_QM_STATUS_MAPPINGS: list[dict[str, Any]] = [
    {"type": "value", "options": {"2": {"text": "RUNNING", "color": "green", "index": 0}}},
    {"type": "value", "options": {"1": {"text": "STARTING", "color": "yellow", "index": 1}}},
    {"type": "special", "options": {"match": "null", "result": {"text": "NO DATA", "index": 2}}},
]
_BACKLOG_THRESHOLDS: list[dict[str, Any]] = [
    {"color": "green", "value": None},
    {"color": "yellow", "value": 1},
    {"color": "red", "value": 100},
]
_UNEXPECTED_CONFIRMATION_THRESHOLDS: list[dict[str, Any]] = [
    {"color": "green", "value": None},
    {"color": "red", "value": 1},
]


def build_dashboard(qmgr: str, counterparty: str, ds_uid: str = "prometheus") -> dict[str, Any]:
    """Return the Grafana dashboard dict for one counterparty on ``qmgr``.

    ``qmgr`` is a parameter, never hardcoded — pass whichever queue manager is up.
    """
    qs = QueueSet.for_counterparty(counterparty)
    header = (
        f"### MQ Gateway · {counterparty} — outbound confirmations\n"
        f"**Queue manager `{qmgr}`.** Base layer: the messaging-flow board "
        f"(QM health + live queues). Extension below: audit reconciliation — a "
        f"browse of the outbound/inbound audit queues, the live path untouched."
    )
    panels = [
        _text(1, _grid(0, 0, 24, 3), header),
        # --- base layer: QM health + live queue depths (standard ibmmq exporter) ---
        _stat(
            2,
            "Queue manager",
            _grid(0, 3, 8, 4),
            f'max(ibmmq_qmgr_status{{qmgr="{qmgr}"}})',
            ds_uid,
            mappings=_QM_STATUS_MAPPINGS,
            description="Queue-manager status from the standard exporter.",
        ),
        _stat(
            3,
            f"Transmit → {counterparty} (live depth)",
            _grid(8, 3, 8, 4),
            _ibmmq("depth", qmgr, qs.request),
            ds_uid,
            description="Depth of the live outbound queue — the hot path, not the audit queue.",
        ),
        _stat(
            4,
            f"Reply ← {counterparty} (live depth)",
            _grid(16, 3, 8, 4),
            _ibmmq("depth", qmgr, qs.reply),
            ds_uid,
            description="Depth of the live reply queue where confirmations arrive.",
        ),
        # --- extension: audit-reconciliation KPI tiles (gwlab exporter) ---
        _stat(
            5,
            "Sent today",
            _grid(0, 7, 5, 4),
            _gwlab("sent", qmgr, counterparty),
            ds_uid,
            description="Current-day outbound audit count.",
        ),
        _stat(
            6,
            "Confirmed today",
            _grid(5, 7, 5, 4),
            _gwlab("confirmed", qmgr, counterparty),
            ds_uid,
            description="Matched on the inbound audit queue.",
        ),
        _stat(
            7,
            "Unconfirmed",
            _grid(10, 7, 5, 4),
            _gwlab("unconfirmed", qmgr, counterparty),
            ds_uid,
            thresholds=_BACKLOG_THRESHOLDS,
            description="Sent but not yet confirmed — the backlog.",
        ),
        _stat(
            8,
            "Mean confirm latency",
            _grid(15, 7, 5, 4),
            _gwlab("confirm_latency_seconds", qmgr, counterparty),
            ds_uid,
            unit="s",
            description="Mean time from send to confirmation, today.",
        ),
        _stat(
            9,
            "Unexpected confirmations",
            _grid(20, 7, 4, 4),
            _gwlab("unexpected_confirmations", qmgr, counterparty),
            ds_uid,
            thresholds=_UNEXPECTED_CONFIRMATION_THRESHOLDS,
            description="Confirmations with no matching sent audit entry — should never happen.",
        ),
        # --- hero + rate ---
        _timeseries(
            10,
            "Unconfirmed backlog over the day",
            _grid(0, 11, 14, 8),
            [
                _target("A", _gwlab("unconfirmed", qmgr, counterparty), "Unconfirmed", ds_uid),
                _target("B", _gwlab("unconfirmed_aged", qmgr, counterparty), "Aged", ds_uid),
            ],
            ds_uid,
            description="Sent minus confirmed, from audit reconciliation — the hero signal.",
        ),
        _timeseries(
            11,
            "Send vs confirm rate",
            _grid(14, 11, 10, 8),
            [
                _target(
                    "A",
                    _ibmmq_rate("mqput_mqput1_count", qmgr, qs.request_audit),
                    "Sent/s",
                    ds_uid,
                ),
                _target(
                    "B",
                    _ibmmq_rate("mqput_mqput1_count", qmgr, qs.reply_audit),
                    "Confirmed/s",
                    ds_uid,
                ),
            ],
            ds_uid,
            unit="reqps",
            description="First derivative: put rate to each audit queue.",
        ),
        # --- which-trades table + scan cost ---
        _table(
            12,
            "Unconfirmed set — which messages, not just how many",
            _grid(0, 19, 16, 9),
            f'gwlab_unconfirmed_message{{qmgr="{qmgr}",counterparty="{counterparty}"}}',
            ds_uid,
            "Browse of the outbound audit queue, entries with no matching confirmation.",
        ),
        _timeseries(
            13,
            "Reconciliation scan cost",
            _grid(16, 19, 8, 5),
            [
                _target(
                    "A",
                    _gwlab("reconcile_scan_seconds", qmgr, counterparty),
                    "Scan seconds",
                    ds_uid,
                )
            ],
            ds_uid,
            unit="s",
            description="Wall-clock of the reconciliation pass — grows with the audit queue.",
        ),
        _stat(
            14,
            "Headers scanned",
            _grid(16, 24, 8, 4),
            _gwlab("reconcile_scanned_headers", qmgr, counterparty),
            ds_uid,
            description="Size of the reconciliation work this pass.",
        ),
    ]
    return {
        "uid": f"gw-{qmgr.lower()}-{counterparty.lower()}",
        "title": f"MQ Gateway - {counterparty} on {qmgr} (audit reconciliation)",
        "tags": ["mq-gateway", "gwlab", counterparty, qmgr],
        "timezone": "browser",
        "schemaVersion": _SCHEMA_VERSION,
        "refresh": "10s",
        "time": {"from": "now-12h", "to": "now"},
        "panels": panels,
    }
