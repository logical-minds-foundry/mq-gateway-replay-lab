"""The ``gwlab`` CLI: start-of-day → run → end-of-day.

The experiment's *own* small tool — not ``mqlab``, and not the lab's HA/DR
``validate`` framework (see CLAUDE.md). The command handlers are skeletons here;
their behaviour lands with Track C (``run`` → C1/C6; ``start-of-day`` /
``end-of-day`` clearing → C5).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, cast

from gwlab.config import QueueSet
from gwlab.dashboard import build_dashboard
from gwlab.swift import cliswift

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def _not_yet(command: str, task: str, args: argparse.Namespace) -> int:
    """Report an unimplemented command honestly and loudly (return code 2)."""
    queues = QueueSet.for_counterparty(args.counterparty)
    print(
        f"gwlab {command}: not yet implemented (see task {task}); "
        f"would operate on {queues.request_audit} / {queues.reply_audit}",
        file=sys.stderr,
    )
    return 2


def start_of_day(args: argparse.Namespace) -> int:
    return _not_yet("start-of-day", "C5", args)


def run(args: argparse.Namespace) -> int:
    return _not_yet("run", "C1/C6", args)


def end_of_day(args: argparse.Namespace) -> int:
    return _not_yet("end-of-day", "C5", args)


def dashboard_cmd(args: argparse.Namespace) -> int:
    """Print the code-generated Grafana dashboard JSON for the counterparty.

    Pipe it to the lab Grafana's `/api/dashboards/db`. The queue manager is a
    parameter (`--qmgr`) — pass whichever one is up — never hardcoded.
    """
    print(json.dumps(build_dashboard(args.qmgr, args.counterparty), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser, wiring each subcommand to its handler."""
    parser = argparse.ArgumentParser(prog="gwlab", description="MQ gateway replay lab")
    parser.add_argument(
        "--counterparty",
        default="CPB",
        help="counterparty tag selecting the queue set (default: CPB)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("start-of-day", start_of_day),
        ("run", run),
        ("end-of-day", end_of_day),
    ):
        sub.add_parser(name).set_defaults(handler=handler)
    dash = sub.add_parser("dashboard", help="print the Grafana dashboard JSON")
    dash.add_argument("--qmgr", required=True, help="the queue manager the board targets")
    dash.set_defaults(handler=dashboard_cmd)
    swift = sub.add_parser("swift", help="SWIFT settlement lifecycle tracking (v2)")
    cliswift.register(swift)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = cast("Callable[[argparse.Namespace], int]", args.handler)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
