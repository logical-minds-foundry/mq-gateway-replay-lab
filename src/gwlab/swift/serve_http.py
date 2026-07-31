"""HTTP serving of the settlement exposition — the live wiring, box-only.

The v2 analog of v1's :mod:`gwlab.exporter_server`: on each Prometheus scrape it
browses the two ``GW.SEC.*`` audit queues (via pymqi), folds the day's settlement
picture, and renders the exposition. Excluded from coverage (see ``pyproject.toml``
``[tool.coverage.run] omit``) — it needs the MQ client libraries and binds a
socket, so it cannot run in the validation container. The exposition it serves is
:func:`gwlab.swift.cliswift.render_exposition`, which is unit-tested. The
connection is reconnectable (the adapter sets ``MQCNO_RECONNECT_Q_MGR``), so a
queue-manager failover is transparent to scrapes.

Run (the systemd unit the deploy installs does this per QM)::

    gwlab swift serve --qmgr NHAUAPP --conn '10.50.0.11(1414)' \\
        --channel APP.SVRCONN --port 9110

Prometheus scrapes ``:9110/metrics`` — a **distinct** port from v1's exporter so
the two run side by side on the shared lab box (spec §7 coexistence).
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from gwlab.swift.cliswift import render_exposition, timed_metrics
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


def _make_handler(
    messaging: Any, queues: SwiftQueueSet, horizon: Horizon
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server dispatch name
            if self.path not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            now = datetime.now(UTC)
            snapshot, collect_seconds = timed_metrics(
                messaging, queues, now, horizon, time.monotonic
            )
            body = render_exposition(snapshot, collect_seconds).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
            return  # keep the scrape loop quiet

    return _Handler


def run(args: argparse.Namespace) -> None:
    """Connect once (optionally over TLS) and serve the exposition until killed."""
    messaging = connect(args)
    queues = SwiftQueueSet()
    horizon = Horizon(days=args.horizon, compression=args.compression)
    handler = _make_handler(messaging, queues, horizon)
    HTTPServer(("", args.port), handler).serve_forever()


def main(argv: Sequence[str] | None = None) -> None:
    """Standalone entry point for the systemd unit (``python -m …serve_http``)."""
    parser = argparse.ArgumentParser(prog="gwlab.swift.serve_http")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", default="APP.SVRCONN")
    parser.add_argument("--port", type=int, default=9110)
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    parser.add_argument("--horizon", type=int, default=3, help="settlement window, business days")
    parser.add_argument(
        "--compression", type=float, default=1.0, help="time-compression factor C (§7)"
    )
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
