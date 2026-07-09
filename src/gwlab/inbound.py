"""Inbound path: get a confirmation and copy it to the archive in one UOW."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Message, Messaging


def drain(messaging: Messaging, queues: QueueSet) -> Message | None:
    """GET one confirmation off the reply queue and copy it to the inbound
    archive, in a single unit of work.

    Returns the confirmation, or ``None`` if the reply queue is empty. The
    inbound archive is thereby the authoritative "processed" ledger: a message is
    in it iff it was taken off the live reply queue and committed. The GET and the
    archive PUT share one unit of work, so a crash before commit **redelivers**
    the confirmation rather than losing it (the mirror of the outbound I1
    guarantee).
    """
    message = messaging.get(queues.reply)
    if message is None:
        return None
    messaging.put(queues.reply_archive, message)
    messaging.commit()
    return message
