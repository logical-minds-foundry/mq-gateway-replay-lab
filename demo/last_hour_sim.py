"""Live last-hour simulation over the REAL remote topology → the dashboard snapshot.

Runs the ``gwlab.simulation.LAST_HOUR`` scenario against two real queue managers in
real time: the **gateway** connection drives requests into our queue manager (the app
puts to the ``GW.<cp>.REQUEST`` alias, which the queue manager retains as the outbound
audit and streams onward to the counterparty), and the **responder** connection plays
the counterparty on its *own* queue manager across the channel — consuming the request
that arrived there and replying, so the confirmation routes back to our reply queue and
is audited. Reconciliation/observability run separately (the deployed exporter browses
the audit queues on our queue manager).

The scenario: the last hour of a trading day — a steady stretch, a slowdown, a ~10-minute
block during which the counterparty stops confirming (the backlog climbs), then recovery
(the backlog drains). A small end-of-day tail is left unconfirmed, and one injected orphan
reply exercises the should-never-happen anomaly. Capture the board at the end for the
recommendation document.

The responder runs *inline* here (rather than as a standalone service) precisely so the
scenario can control it — inducing the slowdown, the block, the unconfirmed tail, and the
anomaly. Both connections are client-mode; the counterparty channel is typically its
service SVRCONN with a distinct (``O=svc-org``) client certificate.

Run (fill in the two connections' coordinates)::

    python demo/last_hour_sim.py \\
        --qmgr NHAUAPP --conn '10.50.0.11(1414)' --channel APP.SVRCONN \\
        --tls-cipher ANY_TLS13_OR_HIGHER --key-repo <app-stem> --cert-label <app-label> \\
        --svc-qmgr SVCQM --svc-conn '10.50.0.50(1414)' --svc-channel SVC.SVRCONN \\
        --svc-tls-cipher ANY_TLS13_OR_HIGHER --svc-key-repo <svc-stem> \\
        --svc-cert-label <svc-label>
"""

from __future__ import annotations

import argparse
import time

from gwlab.config import QueueSet
from gwlab.pymqi_messaging import PyMqiMessaging
from gwlab.responder import Responder
from gwlab.simulation import LAST_HOUR, Phase, run_last_hour

# Wall-clock seconds each phase occupies; the block is the 10-minute stoppage whose
# backlog climbs then drains during recovery, so the board shows both halves of the curve.
_PHASE_SECONDS = {"steady": 300, "slowdown": 420, "block": 600, "recovery": 360}


def _spread(phase: Phase) -> None:
    seconds = _PHASE_SECONDS.get(phase.label, 60)
    time.sleep(seconds / max(phase.trades, 1))


def _clean_start(gateway: PyMqiMessaging, svc: PyMqiMessaging, queues: QueueSet) -> None:
    """Empty the audit queues (our side) and the landing queue (counterparty side) so the
    day's reconciliation starts from zero — the alias itself is never cleared (you cannot
    clear an alias; its target ``request_audit`` is the real queue)."""
    for queue in (queues.request_audit, queues.reply, queues.reply_audit):
        gateway.clear(queue)
    svc.clear(queues.request)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="last_hour_sim")
    # Gateway (our queue manager): the app puts requests here.
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName, e.g. host(port)")
    parser.add_argument("--channel", required=True, help="SVRCONN channel")
    parser.add_argument("--tls-cipher", help="e.g. ANY_TLS13_OR_HIGHER (enables TLS)")
    parser.add_argument("--key-repo", help="GSKit key repository stem (no .p12 suffix)")
    parser.add_argument("--cert-label", help="client certificate label to present")
    # Counterparty queue manager: the responder consumes + replies here, across the channel.
    parser.add_argument("--svc-qmgr", default="SVCQM", help="counterparty queue manager")
    parser.add_argument("--svc-conn", required=True, help="counterparty ConnectionName")
    parser.add_argument("--svc-channel", default="SVC.SVRCONN", help="counterparty SVRCONN")
    parser.add_argument("--svc-tls-cipher", help="counterparty channel cipher")
    parser.add_argument("--svc-key-repo", help="counterparty client key repository stem")
    parser.add_argument("--svc-cert-label", help="counterparty client certificate label")
    parser.add_argument("--counterparty", default="CPB")
    args = parser.parse_args(argv)

    gateway = PyMqiMessaging(
        args.qmgr,
        args.conn,
        args.channel,
        tls_cipher=args.tls_cipher,
        key_repo=args.key_repo,
        cert_label=args.cert_label,
    )
    svc = PyMqiMessaging(
        args.svc_qmgr,
        args.svc_conn,
        args.svc_channel,
        tls_cipher=args.svc_tls_cipher,
        key_repo=args.svc_key_repo,
        cert_label=args.svc_cert_label,
    )
    queues = QueueSet.for_counterparty(args.counterparty)
    responder = Responder(svc, queues)

    print(f"Last hour of a trading day: {args.qmgr} (gateway) <-> {args.svc_qmgr} (counterparty)")
    _clean_start(gateway, svc, queues)
    for phase in LAST_HOUR:
        tag = "BLOCK — counterparty stalls confirmations" if not phase.confirm else "confirming"
        print(f"  phase {phase.label}: {phase.trades} trades ({tag})")
    sent = run_last_hour(gateway, queues, responder, after_send=_spread)
    print(f"Done: {sent} trades sent across the channel. The block backlog drained during")
    print("recovery; a small end-of-day tail and one injected anomaly remain unconfirmed.")
    print("Snapshot the board for the recommendation document.")


if __name__ == "__main__":
    main()
