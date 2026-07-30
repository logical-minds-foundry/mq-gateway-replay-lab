"""The SVC responder's live runtime loop — box-only wiring (§6).

The real-time half of the counterparty-sim: on the SVCQM side it drains our
streamed instructions off the landing ``QLOCAL`` (:data:`LANDING_QUEUE`), plans each
one's lifecycle response (:func:`gwlab.swift.responder_app.plan`), and pushes the
timed events onto the one shared :class:`~gwlab.swift.scheduler.Scheduler`; a
scheduler pass then PUTs every due response back to the reply ``QREMOTE``
(:data:`~gwlab.swift.responder_app.REPLY_QUEUE`). The planning and scheduling cores
are unit-tested; this module is the thin loop that supplies the wall clock and the
pymqi UOWs, so — like :mod:`gwlab.swift.serve_http` — it is excluded from coverage
(see ``pyproject.toml`` ``[tool.coverage.run] omit``): it needs the MQ client
libraries and sleeps against real time, so it cannot run in the validation container.

The connection is reconnectable (the adapter sets ``MQCNO_RECONNECT_Q_MGR``), so a
queue-manager failover is transparent to the loop.

Run on the counterparty host with its own client certificate (the systemd unit the
deploy installs does this)::

    python -m gwlab.swift.serve_responder --qmgr SVCQM --conn '10.50.0.50(1414)' \\
        --channel SVC.SVRCONN --tls-cipher ANY_TLS13_OR_HIGHER \\
        --key-repo /var/mqm/ssl/svc-responder/key --cert-label svc-responder \\
        --compression 60
"""

from __future__ import annotations

import argparse
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from gwlab.messaging import Message
from gwlab.swift.model import parse_swift
from gwlab.swift.responder_app import directive_of, plan
from gwlab.swift.scheduler import Scheduler

if TYPE_CHECKING:
    from collections.abc import Sequence

    from gwlab.messaging import Messaging

#: The SVCQM landing ``QLOCAL`` where our streamed instructions arrive.
LANDING_QUEUE = "GW.SEC.INSTRUCT"


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


def _drain_instructions(messaging: Messaging, scheduler: Scheduler, c: float) -> None:
    """Drain every landing instruction under one UOW, planning each onto ``scheduler``."""
    while (message := messaging.get(LANDING_QUEUE)) is not None:
        text = message.body.decode()
        now = datetime.now(UTC)
        instruction = parse_swift(text, now)
        for event in plan(instruction, directive_of(text), now, c):
            scheduler.push(event)
    messaging.commit()


def _emit_due(messaging: Messaging, scheduler: Scheduler) -> None:
    """PUT every event due at the current wall clock, under one UOW."""
    for event in scheduler.pop_due(datetime.now(UTC)):
        messaging.put(event.queue, Message(body=event.body.encode()))
    messaging.commit()


def run(args: argparse.Namespace) -> None:
    """Connect once and serve the drain/plan/emit loop until killed."""
    messaging = connect(args)
    scheduler = Scheduler()
    while True:
        _drain_instructions(messaging, scheduler, args.compression)
        _emit_due(messaging, scheduler)
        time.sleep(args.poll_seconds)


def main(argv: Sequence[str] | None = None) -> None:
    """Standalone entry point for the systemd unit (``python -m …serve_responder``)."""
    parser = argparse.ArgumentParser(prog="gwlab.swift.serve_responder")
    parser.add_argument("--qmgr", required=True, help="the COUNTERPARTY queue manager")
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", default="SVC.SVRCONN")
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    parser.add_argument(
        "--compression", type=float, default=1.0, help="time-compression factor C (§7)"
    )
    parser.add_argument("--poll-seconds", type=float, default=1.0, help="idle wait between passes")
    run(parser.parse_args(argv))


if __name__ == "__main__":
    main()
