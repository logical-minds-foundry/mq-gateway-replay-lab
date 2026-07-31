"""The streaming settlement collector (``gwlab-collector``) — box-only wiring.

The long-running, client-mode successor to the per-scrape batch exporter
(:mod:`gwlab.swift.serve_http`): it browses the ``GW.SEC.*`` audit queues with
persistent cursors (:func:`gwlab.swift.stream.sweep.sweep`), folds each message
once into an in-memory :class:`~gwlab.swift.stream.state.SettlementState`, and
serves a **pre-computed** snapshot over HTTP. The fold runs on the collector's
own cadence (a loop-level sleep between sweeps); the scrape only reads the last
published snapshot, so scrape latency is constant regardless of retained volume
(design ``epics/65-swift-settlement-tracking/streaming-collector-design.md`` §6).

Excluded from coverage (see ``pyproject.toml`` ``[tool.coverage.run] omit``),
exactly like :mod:`gwlab.swift.serve_http`: it needs the MQ client libraries
(pymqi), binds a socket, reads the real clock, and loops forever, so it cannot
run in the validation container. Every pure piece it wires together is unit-tested
in its own module — :func:`~gwlab.swift.stream.sweep.sweep`,
:class:`~gwlab.swift.stream.state.SettlementState`,
:class:`~gwlab.swift.stream.snapshot.SnapshotHolder`,
:class:`~gwlab.swift.stream.selfmetrics.CollectorStats`,
:func:`gwlab.swift.observe.metrics`, :func:`gwlab.swift.cliswift.render_exposition`,
and :func:`~gwlab.swift.stream.selfmetrics.render_collector_metrics`. The
connection is reconnectable (the adapter sets ``MQCNO_RECONNECT_Q_MGR``), so a
Native-HA failover is transparent — the client follows the QM to whichever node
holds it (design §8).

Run (the systemd unit the deploy installs does this on ``mon-probe``)::

    gwlab-collector --qmgr NHAUAPP --conn '10.50.0.11(1414)' \\
        --channel MON.SVRCONN --port 9111
"""

from __future__ import annotations

import argparse
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from gwlab.swift import observe
from gwlab.swift.cliswift import render_exposition
from gwlab.swift.stream.selfmetrics import CollectorStats, render_collector_metrics
from gwlab.swift.stream.snapshot import SnapshotHolder
from gwlab.swift.stream.state import SettlementState
from gwlab.swift.stream.sweep import sweep
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gwlab.messaging import Messaging


def connect(args: argparse.Namespace) -> Messaging:
    """Open a pymqi-backed :class:`~gwlab.messaging.Messaging` from the parsed args."""
    from gwlab.pymqi_messaging import PyMqiMessaging

    return PyMqiMessaging(
        args.qmgr,
        args.conn,
        args.channel,
        tls_cipher=args.tls_cipher,
        key_repo=args.key_repo,
        cert_label=args.cert_label,
    )


def _make_handler(holder: SnapshotHolder) -> type[BaseHTTPRequestHandler]:
    """Build a handler that serves the last-published snapshot on every scrape.

    A single atomic read of :meth:`SnapshotHolder.latest` — no fold, no browse —
    so the scrape latency is constant regardless of retained volume (design §6).
    """

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server dispatch name
            if self.path not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            body = holder.latest().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
            return  # keep the scrape loop quiet

    return _Handler


def _serve(port: int, holder: SnapshotHolder) -> None:
    """Serve ``holder`` on ``port`` until killed (runs in the daemon HTTP thread)."""
    HTTPServer(("", port), _make_handler(holder)).serve_forever()


def run(args: argparse.Namespace) -> None:
    """Connect once, start the HTTP thread, then sweep-and-publish until killed.

    The ingest loop is the sole publisher into the :class:`SnapshotHolder` and the
    daemon HTTP thread the sole reader; the publish is an atomic reference swap, so
    the two need no lock (design §6). Each lap: time the sweep, fold its timing and
    throughput into :class:`CollectorStats`, re-report the state through the single
    ``state -> metrics`` seam, render the settlement + collector expositions, and
    publish. The first lap is the cold rebuild; every later lap folds only new
    arrivals behind the persistent cursors.

    Each sweep is bounded to ``--batch-size`` messages per queue and the snapshot
    is published after **every** sweep, so a large reload populates the board
    progressively and the box-only HTTP thread gets a fresh GIL window each pass
    (issue #152). Only when the sweep reports it has caught up (``more`` is
    ``False``) does the loop pause for ``--poll-seconds``; while a big delta is
    still draining it keeps sweeping and publishing without pausing.
    """
    messaging = connect(args)
    queues = SwiftQueueSet()
    horizon = Horizon(days=args.horizon)
    state, holder, stats = SettlementState(), SnapshotHolder("# starting"), CollectorStats()
    threading.Thread(target=_serve, args=(args.port, holder), daemon=True).start()
    while True:
        t0 = time.monotonic()
        now = datetime.now(UTC)
        result = sweep(messaging, state, queues, now, horizon, batch=args.batch_size)
        stats.observe(
            cycle_seconds=time.monotonic() - t0,
            ingested=result.ingested,
            now=now.timestamp(),
        )
        report = state.report(now, horizon)  # the single state -> metrics seam
        holder.publish(
            render_exposition(observe.metrics(report, now)) + render_collector_metrics(stats)
        )
        if not result.more:
            time.sleep(args.poll_seconds)


def main(argv: Sequence[str] | None = None) -> None:
    """Standalone entry point for the systemd unit (``gwlab-collector``)."""
    parser = argparse.ArgumentParser(prog="gwlab-collector")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", default="MON.SVRCONN")
    parser.add_argument("--port", type=int, default=9111)
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    parser.add_argument("--horizon", type=int, default=3, help="settlement window, business days")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20000,
        help="max messages folded per queue per sweep; publishes between batches (issue #152)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=5.0,
        help="loop-level sleep between sweeps once caught up (the collector's own cadence)",
    )
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
