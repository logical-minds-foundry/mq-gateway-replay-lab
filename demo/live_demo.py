"""Run the whole gwlab loop against a live queue manager, end to end.

send trades → the counterparty drops one confirmation → reconcile isolates the
unconfirmed one → replay → the counterparty re-confirms (not re-processes) →
the backlog drains to zero. The audit queues are produced by the queue manager's
streaming queues (STRMQOS(MUSTDUP)); this driver only does single puts/gets — the
exact same gwlab code that runs against the in-memory fake, now against real MQ.

Usage (on a host with pymqi + the MQ client, e.g. the app-client node):
    python live_demo.py            # defaults target NHAUAPP over APP.SVRCONN
"""

from __future__ import annotations

import os

from gwlab import dayboundary, inbound, outbound, reconcile, replay
from gwlab.config import QueueSet
from gwlab.pymqi_messaging import PyMqiMessaging
from gwlab.responder import Responder

QM = os.environ.get("GW_QM", "NHAUAPP")
CONN = os.environ.get("GW_CONN", "10.50.0.11(1414),10.50.0.12(1414),10.50.0.13(1414)")
CHANNEL = os.environ.get("GW_CHANNEL", "APP.SVRCONN")
COUNTERPARTY = os.environ.get("GW_CP", "CPB")
N_TRADES = int(os.environ.get("GW_N", "6"))
DROP_EVERY = int(os.environ.get("GW_DROP_EVERY", "3"))


def _msgid(i: int) -> bytes:
    # A 24-byte gateway-assigned MsgId (R9): the counterparty echoes it back as
    # the reply CorrelId, and reconciliation matches on it.
    return b"GWLABDEMO" + f"{i:015d}".encode()


def _counts(label: str, gw: PyMqiMessaging, qs: QueueSet) -> None:
    m = reconcile.metrics(gw, qs)
    print(f"  [{label}]  sent={m.sent}  confirmed={m.confirmed}  unconfirmed={m.unconfirmed}")


def main() -> int:
    qs = QueueSet.for_counterparty(COUNTERPARTY)
    gw = PyMqiMessaging(QM, CONN, CHANNEL)  # the gateway
    cp = gw  # one process → share the connection (real apps would be separate procs)
    print(f"connected to {QM} via {CHANNEL} ({CONN})")

    print(f"start-of-day: cleared {dayboundary.start_of_day(gw, qs)} audited message(s)")

    for i in range(1, N_TRADES + 1):
        outbound.send(gw, qs, f"TRADE-{i}".encode(), _msgid(i))
    print(f"sent {N_TRADES} trades to {qs.request} (QM streams the audit copy, MUSTDUP)")
    _counts("after send", gw, qs)

    responder = Responder(cp, qs, drop_every=DROP_EVERY)
    while responder.process_one() is not None:
        pass
    while inbound.receive(gw, qs) is not None:
        pass
    _counts("after respond + drain", gw, qs)

    unconfirmed = reconcile.unconfirmed(gw, qs)
    print(f"  unconfirmed set = {[u.msg_id.decode(errors='replace').strip() for u in unconfirmed]}")

    print(f"replaying {len(unconfirmed)} unconfirmed message(s)...")
    replay.resend(gw, qs, unconfirmed)
    replay_results = []
    while (result := responder.process_one()) is not None:
        replay_results.append(result)
    while inbound.receive(gw, qs) is not None:
        pass
    print(f"  responder on the replay: {replay_results}  (I2: re-confirmed, not re-processed)")
    _counts("after replay + drain", gw, qs)

    drained = not reconcile.unconfirmed(gw, qs)
    print("RESULT:", "backlog DRAINED TO ZERO ✓" if drained else "STILL UNCONFIRMED ✗")

    gw.disconnect()
    return 0 if drained else 1


if __name__ == "__main__":
    raise SystemExit(main())
