"""Entry point for the streaming settlement collector (``gwlab-collector``).

The long-running, client-mode successor to the per-scrape batch exporter: it will
browse the ``GW.SEC.*`` audit queues with persistent cursors, fold each message
once into an in-memory settlement picture, and serve a pre-computed snapshot over
HTTP (design ``epics/65-swift-settlement-tracking/streaming-collector-design.md``).

This is the **T1 packaging skeleton**. ``main`` is intentionally only an argparse
+ print stub: it parses the connection/TLS options — mirroring
:mod:`gwlab.swift.serve_http` — and prints the resolved configuration, so the
installed ``console_scripts`` entry point resolves and is exercisable *without* MQ
client libraries or a bound socket. Because it opens no connection and binds no
socket, it stays in coverage (unlike ``serve_http``): it is unit-tested directly.

The box-only sweep+serve loop (which *does* need pymqi and a socket, and joins the
``[tool.coverage.run] omit`` list) is added in a later task of the plan
(``epics/65-swift-settlement-tracking/streaming-collector-plan.md`` — T5/T7).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def _build_parser() -> argparse.ArgumentParser:
    """Argument parser mirroring :func:`gwlab.swift.serve_http.main`.

    The collector connects client-mode over a dedicated monitoring channel
    (``MON.SVRCONN``) and serves on a distinct port (``9111``) so it can run beside
    the v1/v2 exporters on the shared probe. TLS options mirror ``serve_http`` — a
    GSKit key repository stem plus an optional client certificate label — so the
    same keystore layout the deploy already provisions applies unchanged.
    """
    parser = argparse.ArgumentParser(prog="gwlab-collector")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", default="MON.SVRCONN")
    parser.add_argument("--port", type=int, default=9111)
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    parser.add_argument("--horizon", type=int, default=3, help="settlement window, business days")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """Parse the collector configuration and print it (T1 stub; no MQ, no socket)."""
    args = _build_parser().parse_args(argv)
    tls = args.tls_cipher if args.tls_cipher else "off"
    print(
        f"gwlab-collector: {args.qmgr} via {args.conn} "
        f"channel={args.channel} on :{args.port} "
        f"tls={tls} horizon={args.horizon}d"
    )


if __name__ == "__main__":
    main()
