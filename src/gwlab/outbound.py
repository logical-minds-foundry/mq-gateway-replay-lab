"""Outbound path.

**Primary:** a single put to the transmit queue; the queue manager writes the
atomic, MsgId-preserving archive copy via a streaming queue
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


def send(messaging: Messaging, queues: QueueSet, body: bytes, msg_id: bytes) -> bytes:
    """Primary: single put to the transmit queue; the streaming queue produces
    the archive copy (same MsgId, same unit of work) with no application logic.

    The gateway assigns the MsgId (R9). The archive is guaranteed because the
    stream is configured ``MUSTDUP``: if the copy cannot be written, the put
    itself fails, so there is no "sent but unarchived" state (I1) — enforced by
    the queue manager, not the app.
    """
    messaging.put(queues.send, Message(body=body, msg_id=msg_id))
    messaging.commit()
    return msg_id


def send_via_two_put(messaging: Messaging, queues: QueueSet, body: bytes, msg_id: bytes) -> bytes:
    """Fallback: PUT to the transmit queue *and* the archive queue in one unit of
    work, with the same MsgId. The explicit, in-app equivalent of the streaming
    archive, for queue managers where streaming is not configured.
    """
    message = Message(body=body, msg_id=msg_id)
    messaging.put(queues.send, message)
    messaging.put(queues.send_archive, message)
    messaging.commit()
    return msg_id
