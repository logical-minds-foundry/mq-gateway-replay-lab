"""Start-of-day / end-of-day audit-queue lifecycle (the **fallback** retention).

The primary intraday-retention mechanism is now the queue manager itself:
``CAPEXPRY`` on the audit queues gives every streamed copy a rolling 24h TTL
(set in the gw_queues Ansible role), verified live on the lab (see issue #27). So
nobody has to remember to clear the audit queues — the queue manager bounds it.

These explicit clear operations are retained as the **fallback** for queue
managers where CAPEXPRY-based retention is not used: start-of-day clears the
audit queues for a fresh day; end-of-day clears them once the day has
reconciled and **refuses** to clear while anything is still unconfirmed, so a
day is never silently discarded with outstanding messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.reconcile import unconfirmed

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


class UnreconciledDayError(RuntimeError):
    """Raised when end-of-day is asked to clear with confirmations outstanding."""


def clear_audits(messaging: Messaging, queues: QueueSet) -> int:
    """Empty both audit queues. Returns the total number of messages cleared."""
    return messaging.clear(queues.request_audit) + messaging.clear(queues.reply_audit)


def start_of_day(messaging: Messaging, queues: QueueSet) -> int:
    """Clear the audit queues for a fresh business day. Returns messages cleared."""
    return clear_audits(messaging, queues)


def end_of_day(messaging: Messaging, queues: QueueSet, *, force: bool = False) -> int:
    """Clear the audit queues at end of day.

    Refuses (raising :class:`UnreconciledDayError`) if any message is still
    unconfirmed — reconcile and replay first — unless ``force`` is set, which
    clears anyway as a deliberate, loud override. Returns messages cleared.
    """
    outstanding = unconfirmed(messaging, queues)
    if outstanding and not force:
        message = (
            f"{len(outstanding)} message(s) still unconfirmed; reconcile/replay "
            f"first, or pass force=True to clear anyway"
        )
        raise UnreconciledDayError(message)
    return clear_audits(messaging, queues)
