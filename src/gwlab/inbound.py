"""Inbound path.

**Primary:** a single get from the reply queue. The confirmation was already
copied to the inbound archive **on arrival** by a streaming queue
(``STREAMQ … STRMQOS(MUSTDUP)`` on the reply queue), so the gateway just consumes
it — no application copy. Note the semantic: the archive records confirmations
**as they arrive**, which is exactly the "did a confirmation arrive for X?"
signal reconciliation needs.

**Fallback:** where streaming is unavailable, ``receive_via_get_copy`` GETs and
copies to the archive in one unit of work — the explicit, in-app equivalent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Message, Messaging


def receive(messaging: Messaging, queues: QueueSet) -> Message | None:
    """Primary: GET one confirmation off the reply queue and commit.

    The inbound archive is populated on arrival by the streaming queue, so no
    application copy is made here. Returns the confirmation, or ``None`` if the
    reply queue is empty.
    """
    message = messaging.get(queues.reply)
    if message is None:
        return None
    messaging.commit()
    return message


def receive_via_get_copy(messaging: Messaging, queues: QueueSet) -> Message | None:
    """Fallback: GET one confirmation and copy it to the inbound archive in a
    single unit of work.

    The archive is thereby the authoritative "processed" ledger, and a crash
    before commit **redelivers** the confirmation rather than losing it (the
    mirror of the outbound I1 guarantee). For queue managers where the reply
    queue is not configured to stream.
    """
    message = messaging.get(queues.reply)
    if message is None:
        return None
    messaging.put(queues.reply_archive, message)
    messaging.commit()
    return message
