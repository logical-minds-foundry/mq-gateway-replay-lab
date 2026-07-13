"""Live gateway mimic — ONE connection to OUR queue manager.

This is the deliberately dumb half of the two-process live run: it sends requests
and consumes the replies that come back, and nothing else. It knows nothing about
slowdowns, blocks, or anomalies — in reality the gateway doesn't; the counterparty
does. All of that lives in ``demo/svc_responder.py`` on the other side of the
channel. The two never talk: they share ``LAST_HOUR`` / ``LAST_HOUR_SECONDS`` and a
common ``--start-epoch`` so the gateway's sends line up with the responder's phases.

Run on a host with the MQ client + the app-org client certificate, reaching our
queue manager's app channel::

    python demo/gateway_driver.py --qmgr NHAUAPP \\
        --conn '10.50.0.11(1414),10.50.0.12(1414),10.50.0.13(1414)' \\
        --channel APP.SVRCONN --counterparty CPB \\
        --tls-cipher ANY_TLS13_OR_HIGHER --key-repo /var/mqm/ssl/app-client/key \\
        --cert-label app-client --start-epoch <shared-epoch>
"""

from __future__ import annotations

import argparse
import time

from gwlab import inbound, outbound
from gwlab.config import QueueSet
from gwlab.pymqi_messaging import PyMqiMessaging
from gwlab.simulation import LAST_HOUR, LAST_HOUR_SECONDS


def _msgid(seq: int) -> bytes:
    return seq.to_bytes(24, "big")


def _drain_replies(messaging: PyMqiMessaging, queues: QueueSet) -> None:
    """Consume whatever confirmations have arrived (the audit copy is already
    retained on the reply-audit queue by the stream, so this never loses history)."""
    while inbound.receive(messaging, queues) is not None:
        pass


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gateway_driver")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True, help="ConnectionName(s), comma-separated")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--counterparty", default="CPB")
    parser.add_argument("--tls-cipher")
    parser.add_argument("--key-repo")
    parser.add_argument("--cert-label")
    parser.add_argument("--start-epoch", type=float, default=0.0, help="shared start time (epoch)")
    parser.add_argument("--drain-seconds", type=float, default=120.0, help="post-run reply drain")
    args = parser.parse_args(argv)

    gw = PyMqiMessaging(
        args.qmgr, args.conn, args.channel,
        tls_cipher=args.tls_cipher, key_repo=args.key_repo, cert_label=args.cert_label,
    )
    queues = QueueSet.for_counterparty(args.counterparty)

    if args.start_epoch:
        time.sleep(max(0.0, args.start_epoch - time.time()))
    print(f"gateway: sending on {args.qmgr}/{queues.request}")
    seq = 0
    for phase in LAST_HOUR:
        interval = LAST_HOUR_SECONDS[phase.label] / phase.trades
        for _ in range(phase.trades):
            outbound.send(gw, queues, f"trade-{seq:06d}".encode(), _msgid(seq))
            seq += 1
            _drain_replies(gw, queues)
            time.sleep(interval)
    # Keep draining replies while the recovery backlog clears on the far side.
    deadline = time.time() + args.drain_seconds
    while time.time() < deadline:
        _drain_replies(gw, queues)
        time.sleep(5)
    print(f"gateway: sent {seq} requests")


if __name__ == "__main__":
    main()
