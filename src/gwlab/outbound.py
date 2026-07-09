"""Outbound path: the atomic two-put (transmit + archive) in one unit of work."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.messaging import Message

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


def send(messaging: Messaging, queues: QueueSet, body: bytes, msg_id: bytes) -> bytes:
    """PUT the message to the transmit queue and the archive in one unit of work.

    The gateway assigns the MsgId (R9), so both copies carry the **same** MsgId —
    the key the counterparty echoes back as the reply's CorrelId, and the key
    reconciliation matches on. The two PUTs share one unit of work: either both
    commit or neither (invariant I1 — no message reaches the counterparty without
    a durable archive record; the fatal "sent but unarchived" state is
    impossible because archiving rides the send's commit).
    """
    message = Message(body=body, msg_id=msg_id)
    messaging.put(queues.send, message)
    messaging.put(queues.send_archive, message)
    messaging.commit()
    return msg_id
