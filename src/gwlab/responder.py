"""The responder — plays the counterparty: consume requests, reply, or drop.

It is the demo's controlled source of *missing confirmations*: dropping a reply
models a counterparty that received the message but whose confirmation never came
back, which is exactly the condition the reconciliation and observability exist to
surface. On a **replay** — a request whose MsgId it has already processed — it
re-sends the confirmation but does not process the message again: delivered-once
(invariant I2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.messaging import Message

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


class Responder:
    """Consume requests from REQUEST and reply on REPLY with ``CorrelId`` = the
    request's ``MsgId`` (the reconciliation key).

    ``drop_every`` manufactures missing confirmations: every Nth *new* request is
    consumed but not replied to. ``drop_every=0`` never drops. A request whose
    MsgId has already been processed is a replay — it is re-confirmed only, never
    processed twice (I2), and does not affect the drop cadence.
    """

    def __init__(self, messaging: Messaging, queues: QueueSet, drop_every: int = 0) -> None:
        self._messaging = messaging
        self._queues = queues
        self._drop_every = drop_every
        self._count = 0
        self._processed: set[bytes] = set()

    def process_one(self) -> str | None:
        """Consume one request. Returns ``"replied"``, ``"dropped"``,
        ``"reconfirmed"``, or ``None`` when there is nothing to consume."""
        request = self._messaging.get(self._queues.request)
        if request is None:
            return None
        if request.msg_id in self._processed:
            # Replay of an already-processed message: re-confirm only (I2).
            self._reply(request)
            return "reconfirmed"
        self._processed.add(request.msg_id)
        self._count += 1
        if self._drop_every and self._count % self._drop_every == 0:
            # The counterparty received it; the confirmation is lost.
            self._messaging.commit()
            return "dropped"
        self._reply(request)
        return "replied"

    def _reply(self, request: Message) -> None:
        reply = Message(
            body=request.body,
            msg_id=b"R:" + request.msg_id,
            correl_id=request.msg_id,
        )
        self._messaging.put(self._queues.reply, reply)
        self._messaging.commit()
