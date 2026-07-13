"""Live counterparty responder runtime — ONE connection to the COUNTERPARTY's queue
manager. Deployed as a service on the counterparty side (see the ``gw-svc-responder``
Ansible role).

This is the half of the two-process run that simulates all the trouble, because in
reality the counterparty is the source of it. The decision matrix (confirm /
withhold during the block / drop a few / one orphan) lives in
``gwlab.scenario_responder.ScenarioResponder`` and is unit-tested; this module is the
thin real-time loop that supplies the wall-clock and paces the consumption so the
board shows the story: fast during ``steady`` (low latency), slow during ``slowdown``
(latency rises as requests wait), withholding during ``block`` (backlog climbs), fast
during ``recovery`` (backlog drains).

Run on the counterparty host with its own client certificate, reaching the
counterparty queue manager::

    python demo/svc_responder.py --qmgr SVCQM --conn '10.50.0.50(1414)' \\
        --channel SVC.SVRCONN --counterparty CPB \\
        --tls-cipher ANY_TLS13_OR_HIGHER --key-repo /var/mqm/ssl/svc-responder/key \\
        --cert-label svc-responder --start-epoch <shared-epoch>
"""

from __future__ import annotations

import argparse
import time

from gwlab.config import QueueSet
from gwlab.pymqi_messaging import PyMqiMessaging
from gwlab.scenario_responder import ScenarioResponder
from gwlab.simulation import LAST_HOUR_SECONDS, Phase, phase_at

_TOTAL = sum(LAST_HOUR_SECONDS.values())

# Seconds to wait between steps, per phase. Consuming slower than the gateway sends
# makes requests wait, so confirm latency rises (slowdown) and the backlog builds;
# consuming fast drains it (recovery). Tunable — the board shows the shape.
_PACE = {"steady": 2.0, "slowdown": 40.0, "block": 10.0, "recovery": 0.5}


def _pace(phase: Phase | None) -> float:
    return 1.0 if phase is None else _PACE[phase.label]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="svc_responder")
    parser.add_argument("--qmgr", required=True, help="the COUNTERPARTY queue manager")
    parser.add_argument("--conn", required=True, help="ConnectionName(s), comma-separated")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--counterparty", default="CPB")
    parser.add_argument("--tls-cipher")
    parser.add_argument("--key-repo")
    parser.add_argument("--cert-label")
    parser.add_argument("--start-epoch", type=float, default=0.0, help="shared start time (epoch)")
    parser.add_argument("--drain-seconds", type=float, default=180.0, help="tail after the phases")
    parser.add_argument("--leave-unconfirmed", type=int, default=3)
    args = parser.parse_args(argv)

    svc = PyMqiMessaging(
        args.qmgr, args.conn, args.channel,
        tls_cipher=args.tls_cipher, key_repo=args.key_repo, cert_label=args.cert_label,
    )
    queues = QueueSet.for_counterparty(args.counterparty)
    responder = ScenarioResponder(svc, queues, leave_unconfirmed=args.leave_unconfirmed)

    start = args.start_epoch or time.time()
    if args.start_epoch:
        time.sleep(max(0.0, start - time.time()))
    print(f"responder: serving {queues.request} on {args.qmgr}")
    end = start + _TOTAL + args.drain_seconds
    while time.time() < end:
        elapsed = time.time() - start
        responder.step(elapsed)
        time.sleep(_pace(phase_at(elapsed)))
    print("responder: scenario window complete")


if __name__ == "__main__":
    main()
