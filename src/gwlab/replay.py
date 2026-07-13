"""Replay: re-transmit the unconfirmed set by original MsgId (R4 resend + I2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.messaging import Message

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging
    from gwlab.reconcile import Unconfirmed


def resend(messaging: Messaging, queues: QueueSet, items: list[Unconfirmed]) -> int:
    """Re-transmit each unconfirmed message to the counterparty, reusing its
    original MsgId. Returns the count resent.

    Only the transmit queue is written — the message is already in the outbound
    audit queue, so it is not re-audited. Reusing the original MsgId makes the
    resend identifiable, and the counterparty dedups on it, so a replay is
    confirmed once, never processed twice (I2). Replay is safely retriable: a
    message that stays unconfirmed is simply found and resent again on the next
    reconciliation.
    """
    for item in items:
        messaging.put(queues.request, Message(body=item.body, msg_id=item.msg_id))
        messaging.commit()
    return len(items)
