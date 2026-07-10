"""Live full-day simulation → the end-of-day dashboard snapshot (box-only).

Runs the ``gwlab.simulation`` scenario against a real queue manager in real time,
so the observability board records a *troubled* trading day: a steady open and
midday, then a confirmation outage during which the unconfirmed backlog climbs and
is left outstanding at end of day. Capture the board at the end for the
proof-of-concept documentation.

Prerequisites on the box: the MQ client, the gateway's ``GW.*`` queues + streams
provisioned, the gwlab exporter serving, and Prometheus scraping it. Run::

    python demo/full_day_sim.py --qmgr NHAUAPP --conn '10.50.0.11(1414)' \\
        --channel APP.SVRCONN --counterparty CPB

Timing is real so the board shows the curve: each phase's trades are spread across
its wall-clock duration (the outage is the long one — that is the story). The
responder plays the counterparty locally, confirming except during the outage.
"""

from __future__ import annotations

import argparse
import time

from gwlab.config import QueueSet
from gwlab.pymqi_messaging import PyMqiMessaging
from gwlab.responder import Responder
from gwlab.simulation import TROUBLED_DAY, Phase, run_day

# Wall-clock seconds each phase occupies; the outage is deliberately long so the
# climbing backlog is visible across many Prometheus scrapes.
_PHASE_SECONDS = {"open": 120, "midday": 240, "outage": 1800, "recovery": 180}


def _spread(phase: Phase) -> None:
    seconds = _PHASE_SECONDS.get(phase.label, 60)
    time.sleep(seconds / max(phase.trades, 1))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="full_day_sim")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", required=True, help="SVRCONN channel")
    parser.add_argument("--counterparty", default="CPB")
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    args = parser.parse_args(argv)

    messaging = PyMqiMessaging(
        args.qmgr,
        args.conn,
        args.channel,
        tls_cipher=args.tls_cipher,
        key_repo=args.key_repo,
        cert_label=args.cert_label,
    )
    queues = QueueSet.for_counterparty(args.counterparty)
    responder = Responder(messaging, queues)

    print(f"Simulating a troubled trading day on {args.qmgr} for {args.counterparty}...")
    for phase in TROUBLED_DAY:
        tag = "OUTAGE — confirmations stalled" if not phase.confirm else "confirming"
        print(f"  phase {phase.label}: {phase.trades} trades ({tag})")
    sent = run_day(messaging, queues, responder, TROUBLED_DAY, after_send=_spread)
    print(f"Done: {sent} trades sent. The outage backlog is now the unconfirmed set.")
    print("Snapshot the board for the proof-of-concept documentation.")


if __name__ == "__main__":
    main()
