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

from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from gwlab.swift.cliswift import render_exposition
from gwlab.swift.collect import collect
from gwlab.swift.observe import metrics
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

if TYPE_CHECKING:
    import argparse

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
            snapshot = metrics(collect(messaging, queues, now, horizon), now)
            body = render_exposition(snapshot).encode()
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
    horizon = Horizon(days=args.horizon)
    handler = _make_handler(messaging, queues, horizon)
    HTTPServer(("", args.port), handler).serve_forever()
