"""HTTP serving of the reconciliation exposition — the live wiring, box-only.

Runs as a small service beside a queue manager: on each Prometheus scrape it
browses the archives (via pymqi) and renders the current-day exposition. Excluded
from coverage — it needs the MQ client libraries and binds a socket, so it cannot
run in the validation container; the exposition it serves is ``exporter.render``,
which is unit-tested. The connection is reconnectable (the adapter sets
``MQCNO_RECONNECT_Q_MGR``), so a queue-manager failover is transparent to scrapes.

Run (the systemd unit the deploy installs does this per QM x counterparty)::

    python -m gwlab.exporter_server --qmgr NHAUAPP \\
        --conn '10.50.0.11(1414)' --channel APP.SVRCONN \\
        --counterparty CPB --port 9109

Prometheus scrapes ``:9109/metrics``; its scrape target carries the ``qmgr`` and
``counterparty`` labels the board's queries filter on.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from gwlab.config import QueueSet
from gwlab.exporter import render
from gwlab.pymqi_messaging import PyMqiMessaging

if TYPE_CHECKING:
    from collections.abc import Sequence


def _make_handler(
    messaging: Any, queues: QueueSet, counterparty: str
) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server dispatch name
            if self.path not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            body = render(messaging, queues, counterparty, datetime.now(UTC)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002, ARG002
            return  # keep the scrape loop quiet

    return _Handler


def serve(qmgr: str, conn: str, channel: str, counterparty: str, port: int) -> None:
    """Connect once and serve the exposition until killed."""
    messaging = PyMqiMessaging(qmgr, conn, channel)
    queues = QueueSet.for_counterparty(counterparty)
    handler = _make_handler(messaging, queues, counterparty)
    HTTPServer(("", port), handler).serve_forever()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gwlab.exporter_server")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", required=True, help="SVRCONN channel")
    parser.add_argument("--counterparty", required=True)
    parser.add_argument("--port", type=int, default=9109)
    args = parser.parse_args(argv)
    serve(args.qmgr, args.conn, args.channel, args.counterparty, args.port)


if __name__ == "__main__":
    main()
