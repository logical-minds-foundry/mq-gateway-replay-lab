"""The responder — plays the counterparty: consume requests, reply, or drop.

It is the demo's controlled source of *missing confirmations*: dropping a reply
models a counterparty that received the message but whose confirmation never came
back, which is exactly the condition the reconciliation and observability exist to
surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.messaging import Message

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


class Responder:
    """Consume requests from SEND and reply on REPLY with ``CorrelId`` = the
    request's ``MsgId`` (the reconciliation key).

    ``drop_every`` manufactures missing confirmations: every Nth request is
    consumed but not replied to. ``drop_every=0`` never drops.
    """

    def __init__(self, messaging: Messaging, queues: QueueSet, drop_every: int = 0) -> None:
        self._messaging = messaging
        self._queues = queues
        self._drop_every = drop_every
        self._seen = 0

    def process_one(self) -> str | None:
        """Consume one request. Returns ``"replied"``, ``"dropped"``, or ``None``
        when there is nothing to consume."""
        request = self._messaging.get(self._queues.send)
        if request is None:
            return None
        self._seen += 1
        if self._drop_every and self._seen % self._drop_every == 0:
            # The counterparty received it; the confirmation is lost.
            self._messaging.commit()
            return "dropped"
        reply = Message(
            body=request.body,
            msg_id=b"R:" + request.msg_id,
            correl_id=request.msg_id,
        )
        self._messaging.put(self._queues.reply, reply)
        self._messaging.commit()
        return "replied"
