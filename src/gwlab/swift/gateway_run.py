"""Live gateway requester runtime — box-only (needs pymqi + a live get-loop).

The real-time half of the app-side view (load-simulation design §2): generate a
day of instructions, PUT each to ``GW.SEC.INSTRUCT`` (the alias whose stream
retains the audit copy and whose remote leg carries the instruction to the
counterparty over the inter-QM link), drain the replies the counterparty routes
back to ``GW.SEC.REPLY``, then fold the app's own sent/received traffic into its
second, independent lifecycle view (:mod:`gwlab.swift.gateway_app`) and log each
instruction's end-state record for the cross-validation (#122).

Excluded from coverage (see ``pyproject.toml`` ``[tool.coverage.run] omit``): it
opens a pymqi connection and runs a blocking get-loop, so it cannot run in the
validation container. The logic it drives — the generator, ``parse_swift``, and
:func:`gwlab.swift.gateway_app.track` / ``app_end_states`` — is unit-tested.

Run on a host with the MQ client + the app-org client certificate, reaching our
queue manager's app channel::

    python -m gwlab.swift.gateway_run --qmgr NHAUAPP \\
        --conn '10.50.0.11(1414)' --channel APP.SVRCONN \\
        --count 1000 --drain-seconds 120
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gwlab.messaging import MQRO_COA, MQRO_COD, Message, ReportOpts
from gwlab.swift import gateway_app
from gwlab.swift.generator import generate
from gwlab.swift.model import parse_swift
from gwlab.swift.swiftconfig import SwiftQueueSet

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gwlab.messaging import Messaging
    from gwlab.swift.endstate import EndState
    from gwlab.swift.model import SwiftMessage

_LOG = logging.getLogger("gwlab.swift.gateway_run")

#: The app-facing request alias: its stream retains the outbound audit copy and
#: its remote leg carries the instruction to the counterparty over the inter-QM
#: link. Distinct from the collector's audit queues (:class:`SwiftQueueSet`).
INSTRUCT_QUEUE = "GW.SEC.INSTRUCT"

#: The queue the counterparty's replies are routed back to over the return link.
REPLY_QUEUE = "GW.SEC.REPLY"


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


def put_instructions(
    messaging: Messaging, count: int, seed: int, *, queues: SwiftQueueSet | None = None
) -> list[SwiftMessage]:
    """Generate and PUT ``count`` instructions to the request alias; return them parsed.

    Each envelope is put to the ``GW.SEC.INSTRUCT`` QALIAS and **committed on its
    own** so its SENT timestamp is its actual transmit moment: a single batched
    commit would date every early instruction as "sent" before it transmits,
    inflating the ``sent->arrived`` delivery-latency leg (spec §7.1). SENT is
    therefore stamped at commit.

    When ``request_reports`` is set (default), plain COA/COD report options are
    attached to each put with ``GW.SEC.REPORT`` as the return address (spec §3), so
    the counterparty queue manager returns arrival/delivery confirmations there.
    Every envelope is also parsed into the app's own sent-message list — the left
    side of the app-side fold.
    """
    queues = queues or SwiftQueueSet()
    report_opts = (
        ReportOpts(MQRO_COA | MQRO_COD, queues.report_ledger, None)
        if queues.request_reports
        else None
    )
    sent: list[SwiftMessage] = []
    for instruction in generate(count, seed=seed):
        message = Message(body=instruction.text.encode())
        messaging.put(queues.instruct, message, report_opts=report_opts)
        messaging.commit()
        put_time = datetime.now(UTC)  # SENT stamped at commit — its real transmit time
        sent.append(parse_swift(instruction.text, put_time))
    return sent


def drain_replies(messaging: Messaging, deadline: float) -> list[SwiftMessage]:
    """Drain ``GW.SEC.REPLY`` until it has stayed empty up to ``deadline``.

    Each reply is destructively got under syncpoint, committed, parsed, and
    collected. When the queue is empty the loop waits and retries until ``deadline``
    so late (T+1/T+2) confirmations still crossing the link are not missed.
    """
    received: list[SwiftMessage] = []
    while True:
        message = messaging.get(REPLY_QUEUE)
        if message is None:
            if time.time() >= deadline:
                return received
            time.sleep(1.0)
            continue
        messaging.commit()
        put_time = message.put_time or datetime.now(UTC)
        received.append(parse_swift(message.body.decode(), put_time))


def emit_end_states(sent: list[SwiftMessage], received: list[SwiftMessage]) -> list[EndState]:
    """Fold the app's own traffic and log each instruction's end-state record."""
    records = gateway_app.app_end_states(sent, received)
    for record in records:
        terminal_at = "-" if record.terminal_at is None else record.terminal_at.isoformat()
        _LOG.info(
            "app-end-state seme=%s state=%s terminal_at=%s",
            record.seme,
            record.state,
            terminal_at,
        )
    return records


def run(args: argparse.Namespace) -> list[EndState]:
    """Connect, put the instructions, drain the replies, and emit the end-states."""
    messaging = connect(args)
    queues = SwiftQueueSet(request_reports=args.request_reports)
    sent = put_instructions(messaging, args.count, args.seed, queues=queues)
    deadline = time.time() + args.drain_seconds
    received = drain_replies(messaging, deadline)
    return emit_end_states(sent, received)


def main(argv: Sequence[str] | None = None) -> None:
    """Standalone entry point (``python -m gwlab.swift.gateway_run``)."""
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="gwlab.swift.gateway_run")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", default="APP.SVRCONN")
    parser.add_argument("--count", type=int, default=1000, help="instructions to generate and put")
    parser.add_argument("--seed", type=int, default=0, help="generator seed (reproducible run)")
    parser.add_argument("--drain-seconds", type=float, default=120.0, help="reply-drain window")
    parser.add_argument(
        "--no-request-reports",
        dest="request_reports",
        action="store_false",
        help="do not attach COA/COD report options to the put (transparency-proof toggle)",
    )
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
