"""The ``gwlab swift`` command group: simulate, report, board, serve.

The v2 settlement-lifecycle head of the CLI (spec §11). It is **purely additive**
to v1's :mod:`gwlab.cli`: it registers its own ``swift`` subcommand group and
touches no v1 command, handler, metric name, or port.

Four subcommands:

- ``swift simulate`` — drive a representative :class:`~gwlab.swift.simulation.Scenario`
  into an in-memory :class:`~gwlab.swift.swiftconfig.SwiftQueueSet` and print the
  resulting distribution. Offline by construction (a :class:`~gwlab.fake_messaging.FakeMessaging`),
  so it needs no queue manager.
- ``swift report`` — browse the live audits, fold them, and print the settlement
  :class:`~gwlab.swift.observe.Metrics` as text or (``--json``) JSON.
- ``swift board`` — render the self-contained :func:`~gwlab.swift.dashboard.render_board`
  HTML and write it to a file (``--output``) or stdout.
- ``swift serve`` — Prometheus exposition of the settlement metrics on a port
  **distinct from v1's** (:data:`DEFAULT_PORT` vs v1's :data:`V1_PORT`, per spec §7).

The exposition itself — :func:`render_exposition`, a pure ``Metrics -> str`` fold —
lives here and is unit-tested to 100%. The socket-binding HTTP serve loop and the
``pymqi`` connection are box-only and live in :mod:`gwlab.swift.serve_http`
(coverage-omitted, mirroring v1's :mod:`gwlab.exporter_server`); the ``report`` /
``board`` / ``serve`` handlers reach them through the small seams
:func:`_connect` and ``serve_http.run`` so they stay testable against a fake.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from gwlab.fake_messaging import FakeMessaging
from gwlab.swift.collect import collect
from gwlab.swift.dashboard import render_board
from gwlab.swift.exposition import append_gauge
from gwlab.swift.observe import metrics
from gwlab.swift.simulation import (
    SILENT_PATH,
    InstructionSpec,
    Scenario,
    run_window,
)
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from gwlab.messaging import Messaging
    from gwlab.swift.observe import Metrics

#: The Prometheus exposition port for the v2 settlement exporter. Deliberately
#: **distinct** from v1's :data:`V1_PORT` so the two exporters run side by side on
#: the shared lab box without collision (spec §7 coexistence).
DEFAULT_PORT = 9110

#: v1's exporter default port (see :mod:`gwlab.exporter_server`). Recorded here so
#: the distinctness is asserted in one place and can never silently regress.
V1_PORT = 9109

#: Two days, in seconds — pushes a confirmation into a late T+2 arrival in the
#: demonstrator scenario (still correlates; never an orphan).
_TWO_DAYS = 2 * 24 * 60 * 60.0

# Prometheus metric names — all ``swift_`` prefixed, so they never collide with
# v1's ``gwlab_`` families (no v1 metric name is touched).
_STATE_METRIC = "swift_settlement_state"
# The in-flight buckets (instructed/acknowledged/matched) are *current* snapshots;
# the terminal-outcome buckets (settled/rejected/failed) are **windowed gauges over
# the retention horizon** — "settled within the retained N-day window" — since a
# count only means something over a window (design §6.1). The state already windows
# them (a complete lifecycle drops once complete-and-expired); this note documents
# the meaning, it changes no number.
_STATE_HELP = (
    "Instructions in each lifecycle state (the hero distribution); the "
    "settled/rejected/failed outcome buckets are windowed over the retention horizon (design §6.1)."
)
_ANOMALY_METRIC = "swift_settlement_anomalies"
_ANOMALY_HELP = "Surfaced anomalies broken out by kind (never swallowed)."
_ANOMALY_TOTAL_METRIC = "swift_settlement_anomalies_total"
_ANOMALY_TOTAL_HELP = "Total surfaced anomalies across all kinds."
_AGING_METRIC = "swift_settlement_aging"
_AGING_HELP = "In-flight, non-terminal instructions still aging (the chase list)."
_AGED_OUT_METRIC = "swift_settlement_aged_out"
_AGED_OUT_HELP = "Non-terminal instructions past the retention horizon (replay candidates)."
_SCANNED_METRIC = "swift_settlement_scanned"
_SCANNED_HELP = "Messages the last browse scan examined (the scan-cost thread)."
_HOP_MEAN_METRIC = "swift_settlement_hop_mean_seconds"
_HOP_MEAN_HELP = "Mean latency of each inter-state lifecycle hop, in seconds."
_HOP_P95_METRIC = "swift_settlement_hop_p95_seconds"
_HOP_P95_HELP = "Nearest-rank p95 latency of each inter-state lifecycle hop, in seconds."
_COLLECT_SECONDS_METRIC = "swift_settlement_collect_seconds"
_COLLECT_SECONDS_HELP = (
    "Wall-clock of the last full collect()+metrics pass (the exporter's self-timing, "
    "load-sim §9.2) — must stay well under the scrape interval so passes never back up."
)


def timed_metrics(
    messaging: Messaging,
    queues: SwiftQueueSet,
    now: datetime,
    horizon: Horizon,
    monotonic: Callable[[], float],
) -> tuple[Metrics, float]:
    """Fold one full collect pass, timing its wall-clock via an injected ``monotonic``.

    Returns ``(snapshot, collect_seconds)`` where ``collect_seconds`` is the
    wall-clock of the whole ``collect()`` + ``metrics()`` pass — the exporter's
    self-timing (load-sim §9.2). ``monotonic`` is injected (a
    ``Callable[[], float]``) so the timing is deterministically testable against a
    fake clock; the live exporter passes :func:`time.monotonic`.
    """
    start = monotonic()
    snapshot = metrics(collect(messaging, queues, now, horizon), now)
    collect_seconds = monotonic() - start
    return snapshot, collect_seconds


def render_exposition(snapshot: Metrics, collect_seconds: float | None = None) -> str:
    """Render a settlement :class:`Metrics` snapshot as Prometheus text exposition.

    Pure function of the snapshot — no clock read, no I/O — so it is fully
    deterministic and unit-tested. Emits the state distribution, the per-kind and
    total anomaly counts, the aging / aged-out / scan-cost gauges, and the
    per-hop mean/p95 latencies. When ``collect_seconds`` is supplied (the live
    exporter times each pass via :func:`timed_metrics`), it also emits the
    ``swift_settlement_collect_seconds`` self-timing gauge.
    """
    lines: list[str] = []

    lines.append(f"# HELP {_STATE_METRIC} {_STATE_HELP}")
    lines.append(f"# TYPE {_STATE_METRIC} gauge")
    for state, count in snapshot.distribution.items():
        lines.append(f'{_STATE_METRIC}{{state="{state.value}"}} {count}')

    lines.append(f"# HELP {_ANOMALY_METRIC} {_ANOMALY_HELP}")
    lines.append(f"# TYPE {_ANOMALY_METRIC} gauge")
    for kind, count in snapshot.anomalies_by_kind.items():
        lines.append(f'{_ANOMALY_METRIC}{{kind="{kind}"}} {count}')

    scalars = (
        (_ANOMALY_TOTAL_METRIC, _ANOMALY_TOTAL_HELP, snapshot.anomalies),
        (_AGING_METRIC, _AGING_HELP, len(snapshot.aging)),
        (_AGED_OUT_METRIC, _AGED_OUT_HELP, len(snapshot.aged_out)),
        (_SCANNED_METRIC, _SCANNED_HELP, snapshot.scanned),
    )
    for name, help_text, value in scalars:
        append_gauge(lines, name, help_text, value)

    lines.append(f"# HELP {_HOP_MEAN_METRIC} {_HOP_MEAN_HELP}")
    lines.append(f"# TYPE {_HOP_MEAN_METRIC} gauge")
    lines.append(f"# HELP {_HOP_P95_METRIC} {_HOP_P95_HELP}")
    lines.append(f"# TYPE {_HOP_P95_METRIC} gauge")
    for stat in snapshot.latencies:
        lines.append(f'{_HOP_MEAN_METRIC}{{hop="{stat.hop}"}} {stat.mean_seconds}')
        lines.append(f'{_HOP_P95_METRIC}{{hop="{stat.hop}"}} {stat.p95_seconds}')

    if collect_seconds is not None:
        append_gauge(lines, _COLLECT_SECONDS_METRIC, _COLLECT_SECONDS_HELP, collect_seconds)

    return "\n".join(lines) + "\n"


def _render_report_text(snapshot: Metrics) -> str:
    """Render a settlement :class:`Metrics` snapshot as a human-readable summary."""
    lines = ["State distribution:"]
    lines.extend(f"  {state.value}: {count}" for state, count in snapshot.distribution.items())
    lines.append(f"Anomalies ({snapshot.anomalies} total):")
    lines.extend(f"  {kind}: {count}" for kind, count in snapshot.anomalies_by_kind.items())
    lines.append(f"Aging: {len(snapshot.aging)}")
    lines.append(f"Aged out: {len(snapshot.aged_out)}")
    lines.append(f"Scanned: {snapshot.scanned}")
    return "\n".join(lines)


def _report_dict(snapshot: Metrics) -> dict[str, object]:
    """Fold a settlement :class:`Metrics` snapshot into a JSON-serialisable dict."""
    return {
        "distribution": {state.value: count for state, count in snapshot.distribution.items()},
        "anomalies_total": snapshot.anomalies,
        "anomalies_by_kind": dict(snapshot.anomalies_by_kind),
        "aging": len(snapshot.aging),
        "aged_out": len(snapshot.aged_out),
        "scanned": snapshot.scanned,
    }


def _now() -> datetime:
    """Return the current UTC instant — the reporting ``now`` (injectable seam)."""
    return datetime.now(UTC)


def _demo_scenario() -> Scenario:
    """A representative settlement window: one of every terminal path plus an orphan.

    Deterministic and fully anonymised (``GWSEC…`` references). Offline-only — used
    by ``swift simulate`` to populate an in-memory audit set with a lifecycle across
    settled, rejected, failed, acknowledged-and-aging, and instructed-then-silent.
    """
    return Scenario(
        specs=[
            InstructionSpec(
                seme="GWSEC0000000001",
                path="clean-settle",
                instructed_offset_days=0,
                response_delays=[300.0, 300.0, _TWO_DAYS],
            ),
            InstructionSpec(
                seme="GWSEC0000000002",
                path="rejected-at-validation",
                instructed_offset_days=1,
                response_delays=[300.0],
            ),
            InstructionSpec(
                seme="GWSEC0000000003",
                path="matched-then-failing",
                instructed_offset_days=1,
                response_delays=[300.0, 600.0, 900.0],
            ),
            InstructionSpec(
                seme="GWSEC0000000004",
                path="acknowledged-aging",
                instructed_offset_days=2,
                response_delays=[300.0],
            ),
            InstructionSpec(seme="GWSEC0000000005", path=SILENT_PATH, instructed_offset_days=0),
        ],
        inject_orphan=True,
    )


def _collect_metrics(messaging: Messaging, args: argparse.Namespace, now: datetime) -> Metrics:
    """Browse the audits and fold them into the day's settlement :class:`Metrics`."""
    queues = SwiftQueueSet()
    horizon = Horizon(days=args.horizon, compression=args.compression)
    report = collect(messaging, queues, now, horizon)
    return metrics(report, now)


def _connect(args: argparse.Namespace) -> Messaging:
    """Open the live audit-browsing messaging (box-only seam; faked under test)."""
    from gwlab.swift import serve_http

    return serve_http.connect(args)


def simulate_cmd(args: argparse.Namespace) -> int:
    """Drive the demonstrator scenario into an in-memory audit set and print the picture."""
    now = _now()
    horizon = Horizon(days=args.horizon, compression=args.compression)
    start = now - timedelta(days=horizon.days)
    report = run_window(FakeMessaging(), SwiftQueueSet(), horizon, _demo_scenario(), start, now)
    print(_render_report_text(metrics(report, now)))
    return 0


def report_cmd(args: argparse.Namespace) -> int:
    """Collect the audits and print the settlement metrics (text, or JSON with ``--json``)."""
    snapshot = _collect_metrics(_connect(args), args, _now())
    if args.json:
        print(json.dumps(_report_dict(snapshot), indent=2))
    else:
        print(_render_report_text(snapshot))
    return 0


def board_cmd(args: argparse.Namespace) -> int:
    """Render the settlement board HTML and write it to ``--output`` or stdout."""
    now = _now()
    page = render_board(_collect_metrics(_connect(args), args, now), now)
    if args.output is not None:
        Path(args.output).write_text(page, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(page)
    return 0


def serve_cmd(args: argparse.Namespace) -> int:
    """Serve the Prometheus settlement exposition until killed (box-only serve loop)."""
    from gwlab.swift import serve_http

    serve_http.run(args)
    return 0


def _add_connection_args(parser: argparse.ArgumentParser) -> None:
    """Add the live queue-manager connection flags a browsing subcommand needs."""
    parser.add_argument("--qmgr", required=True, help="queue manager to browse")
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", required=True, help="SVRCONN channel")
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")


def _add_horizon(parser: argparse.ArgumentParser) -> None:
    """Add the ``--horizon`` (window, days) and ``--compression`` (factor C) flags."""
    parser.add_argument(
        "--horizon", type=int, default=Horizon().days, help="settlement window, days"
    )
    parser.add_argument(
        "--compression",
        type=float,
        default=Horizon().compression,
        help="time-compression factor C (load-sim §7); 1.0 = real time",
    )


def register(parser: argparse.ArgumentParser) -> None:
    """Wire the ``swift`` subcommand group onto ``parser`` (additive; v1 untouched)."""
    sub = parser.add_subparsers(dest="swift_command", required=True)

    sim = sub.add_parser("simulate", help="drive a demo settlement scenario into an in-memory set")
    _add_horizon(sim)
    sim.set_defaults(handler=simulate_cmd)

    rep = sub.add_parser("report", help="browse the audits and print the settlement metrics")
    _add_connection_args(rep)
    _add_horizon(rep)
    rep.add_argument("--json", action="store_true", help="emit JSON instead of text")
    rep.set_defaults(handler=report_cmd)

    board = sub.add_parser("board", help="render the settlement board HTML")
    _add_connection_args(board)
    _add_horizon(board)
    board.add_argument("--output", help="write the HTML board here (default: stdout)")
    board.set_defaults(handler=board_cmd)

    serve = sub.add_parser("serve", help="serve the Prometheus settlement exposition")
    _add_connection_args(serve)
    _add_horizon(serve)
    serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"exposition port (default {DEFAULT_PORT}; distinct from v1's {V1_PORT})",
    )
    serve.set_defaults(handler=serve_cmd)
