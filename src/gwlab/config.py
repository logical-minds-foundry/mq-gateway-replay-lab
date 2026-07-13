"""Queue names and run configuration — the single source of truth.

All names are generic/anonymized (Counterparty A/B/…, invented queue names). Per
the parasite rule, these four queues are the *only* MQ objects this experiment
owns; everything else on the queue manager belongs to the lab and is never
touched (see CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QueueSet:
    """The four queues the gateway owns for one counterparty stream.

    - ``send``: the remote/transmission queue toward the counterparty.
    - ``send_audit``: the outbound audit (the replay copy).
    - ``reply``: the local reply queue (confirmations arrive here).
    - ``reply_audit``: the inbound audit (the authoritative "processed"
      ledger).
    """

    send: str
    send_audit: str
    reply: str
    reply_audit: str

    @classmethod
    def for_counterparty(cls, counterparty: str) -> QueueSet:
        """Derive the queue set for a counterparty tag (e.g. ``"CPB"``)."""
        base = f"GW.{counterparty}"
        return cls(
            send=f"{base}.SEND",
            send_audit=f"{base}.SEND.AUDIT",
            reply=f"{base}.REPLY",
            reply_audit=f"{base}.REPLY.AUDIT",
        )
