"""Live last-hour simulation → the end-of-day dashboard snapshot (box-only).

Runs the ``gwlab.simulation.LAST_HOUR`` scenario against a real queue manager in
real time: the last hour of a trading day — a steady stretch, a slowdown, a
~10-minute block during which confirmations stall, then recovery. Unlike
``full_day_sim.py``'s outage (permanently stranded), the block's backlog
survives into recovery and drains there, so the board shows a climb-and-drain
curve rather than a stuck one. A small tail of end-of-day trades is left
unconfirmed, and one injected orphan reply exercises the should-never-happen
anomaly. Capture the board at the end for the recommendation document.

Prerequisites on the box: the MQ client, the gateway's ``GW.*`` queues + streams
provisioned, the gwlab exporter serving, and Prometheus scraping it. Run::

    python demo/last_hour_sim.py --qmgr NHAUAPP --conn '10.50.0.11(1414)' \\
        --channel APP.SVRCONN --counterparty CPB

Timing is real so the board shows the curve: each phase's trades are spread
across its wall-clock duration (the block is the long one — that is the
story). The responder plays the counterparty locally, confirming except during
the block.
"""

from __future__ import annotations

import argparse
import time

from gwlab.config import QueueSet
from gwlab.pymqi_messaging import PyMqiMessaging
from gwlab.responder import Responder
from gwlab.simulation import LAST_HOUR, Phase, run_last_hour

# Wall-clock seconds each phase occupies; the block is the 10-minute stoppage
# whose backlog climbs then drains during recovery, so the board shows both
# halves of the curve.
_PHASE_SECONDS = {"steady": 300, "slowdown": 420, "block": 600, "recovery": 360}


def _spread(phase: Phase) -> None:
    seconds = _PHASE_SECONDS.get(phase.label, 60)
    time.sleep(seconds / max(phase.trades, 1))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="last_hour_sim")
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

    print(f"Simulating the last hour of a trading day on {args.qmgr} for {args.counterparty}...")
    for phase in LAST_HOUR:
        tag = "BLOCK — confirmations stalled" if not phase.confirm else "confirming"
        print(f"  phase {phase.label}: {phase.trades} trades ({tag})")
    sent = run_last_hour(messaging, queues, responder, after_send=_spread)
    print(f"Done: {sent} trades sent. The block backlog drained during recovery; a small")
    print("end-of-day tail and one injected anomaly remain unconfirmed.")
    print("Snapshot the board for the recommendation document.")


if __name__ == "__main__":
    main()
