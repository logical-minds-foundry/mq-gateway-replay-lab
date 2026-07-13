"""The counterparty responder for the live last-hour scenario — a SEPARATE process.

In production the counterparty is a different program on the other side of the
channel, on its own queue manager. So is this: it is deployed on the counterparty
side with a single connection to *their* queue manager, consumes the gateway's
requests off the landing queue, and generates confirmations. Crucially it is also
where every simulated problem originates — because in reality the counterparty is
the source of them:

- a **slowdown** (paced by the runtime, so confirm latency rises),
- a ~10-minute **block** during which it withholds confirmations (our unconfirmed
  backlog climbs), whose backlog **recovers** afterwards,
- a small number of trades **left unconfirmed** at end of day, and
- one **orphan reply** — a should-never-happen confirmation for a request that was
  never sent (Algorithm 3's exception).

The gateway mimic (``demo/gateway_driver.py``) does none of this: it only sends
requests and consumes replies. The two never talk to each other — they share
``LAST_HOUR`` / ``LAST_HOUR_SECONDS`` and a common start time, so this responder
derives the active phase from the elapsed wall-clock (``phase_at``).

``step`` is pure and clock-injected (the elapsed time is passed in), so the whole
decision matrix is unit-tested against the in-memory fake; the thin real-time loop
that supplies the clock and the pacing lives in ``demo/svc_responder.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.messaging import Message
from gwlab.simulation import phase_at

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging

# The should-never-happen anomaly: a confirmation whose CorrelId matches no sent
# MsgId. Reconciliation flags it as an unexpected confirmation (Algorithm 3).
_ORPHAN = Message(body=b"orphan", msg_id=b"R:orphan", correl_id=b"\xffNOSENT")


class ScenarioResponder:
    """Consume requests on the counterparty's queue manager and confirm them, while
    staging the last-hour problems. One ``step`` performs one action for the phase
    active at the supplied ``elapsed`` seconds after the shared start.
    """

    def __init__(
        self,
        messaging: Messaging,
        queues: QueueSet,
        *,
        leave_unconfirmed: int = 3,
        inject_anomaly: bool = True,
    ) -> None:
        self._messaging = messaging
        self._queues = queues
        self._leave = leave_unconfirmed
        self._dropped = 0
        self._anomaly_done = not inject_anomaly
        self._processed: set[bytes] = set()

    def step(self, elapsed: float) -> str:
        """Perform one scenario action at ``elapsed`` seconds after the shared start.

        Returns the action taken: ``"block"`` (withheld — the request is left on the
        landing queue so the backlog climbs), ``"replied"``, ``"reconfirmed"`` (a
        replay, re-confirmed only — I2), ``"dropped"`` (consumed but not confirmed —
        left unconfirmed), ``"anomaly"`` (the one orphan reply), or ``"idle"``
        (nothing waiting).
        """
        phase = phase_at(elapsed)
        if phase is not None and not phase.confirm:
            return "block"
        if phase is None and not self._anomaly_done:
            self._put(_ORPHAN)
            self._anomaly_done = True
            return "anomaly"
        drop_ok = phase is not None and phase.label == "recovery"
        return self._confirm_one(drop_ok=drop_ok)

    def _confirm_one(self, *, drop_ok: bool) -> str:
        request = self._messaging.get(self._queues.request)
        if request is None:
            return "idle"
        if request.msg_id in self._processed:
            self._reply(request)  # replay of an already-processed request: re-confirm only (I2)
            return "reconfirmed"
        self._processed.add(request.msg_id)
        if drop_ok and self._dropped < self._leave:
            self._dropped += 1
            self._messaging.commit()  # consumed, not confirmed → left unconfirmed at EOD
            return "dropped"
        self._reply(request)
        return "replied"

    def _reply(self, request: Message) -> None:
        reply = Message(body=request.body, msg_id=b"R:" + request.msg_id, correl_id=request.msg_id)
        self._put(reply)

    def _put(self, message: Message) -> None:
        self._messaging.put(self._queues.reply, message)
        self._messaging.commit()
