"""Reconciliation: the unconfirmed set, from browsing the archives only.

This is the authoritative "what didn't come back" computation. It runs entirely
against the outbound and inbound **archive** queues — never the live transmit or
reply queues — so it never contends with the gateway's live throughput. An
outbound message is *unconfirmed* when its MsgId has no matching reply CorrelId
(the counterparty echoes the request MsgId back as the reply CorrelId).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


@dataclass(frozen=True)
class Unconfirmed:
    """An outbound message with no matching inbound confirmation."""

    msg_id: bytes
    body: bytes


@dataclass(frozen=True)
class Metrics:
    """Point-in-time counts for the observability tier."""

    sent: int
    confirmed: int
    unconfirmed: int


def _split(messaging: Messaging, queues: QueueSet) -> tuple[list[Unconfirmed], int]:
    sent = messaging.browse(queues.send_archive)
    replies = messaging.browse(queues.reply_archive)
    confirmed_ids = {reply.correl_id for reply in replies}
    unmatched = [
        Unconfirmed(msg_id=message.msg_id, body=message.body)
        for message in sent
        if message.msg_id not in confirmed_ids
    ]
    return unmatched, len(sent)


def unconfirmed(messaging: Messaging, queues: QueueSet) -> list[Unconfirmed]:
    """Return the outbound messages with no matching confirmation."""
    unmatched, _ = _split(messaging, queues)
    return unmatched


def metrics(messaging: Messaging, queues: QueueSet) -> Metrics:
    """Return sent / confirmed / unconfirmed counts for the dashboard."""
    unmatched, total = _split(messaging, queues)
    return Metrics(sent=total, confirmed=total - len(unmatched), unconfirmed=len(unmatched))
