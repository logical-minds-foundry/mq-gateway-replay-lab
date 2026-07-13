"""Outbound path.

**Primary:** a single put to the transmit queue; the queue manager writes the
atomic, MsgId-preserving audit copy via a streaming queue
(``STREAMQ … STRMQOS(MUSTDUP)`` configured in Ansible) — QM-native, base-licensed,
zero application logic (research task #54).

**Fallback:** where streaming is unavailable, ``send_via_two_put`` makes the two
puts explicitly in one unit of work — the glass-box equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.messaging import Message

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


def send(messaging: Messaging, queues: QueueSet, body: bytes) -> bytes:
    """Primary: single put to the transmit queue; the streaming queue produces
    the audit copy (same MsgId, same unit of work) with no application logic.

    The **queue manager mints the MsgId** (``MQPMO_NEW_MSG_ID``) — the gateway does
    not hand-assign a predetermined value; it reads the assigned id back and returns
    it, so it can track the message. The audit copy is guaranteed because the stream
    is configured ``MUSTDUP``: if the copy cannot be written, the put itself fails,
    so there is no "sent but unaudited" state (I1) — enforced by the queue manager.
    """
    message = Message(body=body)
    messaging.put(queues.request, message)
    messaging.commit()
    return message.msg_id


def send_via_two_put(messaging: Messaging, queues: QueueSet, body: bytes) -> bytes:
    """Fallback: PUT to the transmit queue *and* the audit queue in one unit of
    work, with the same MsgId. The explicit, in-app equivalent of the streaming
    audit copy, for queue managers where streaming is not configured. The first put
    lets the queue manager mint the MsgId; the second reuses it (the message now
    carries it) so both copies share the one id.
    """
    message = Message(body=body)
    messaging.put(queues.request, message)
    messaging.put(queues.request_audit, message)
    messaging.commit()
    return message.msg_id
