"""Start-of-day / end-of-day archive lifecycle.

The archives hold one business day (intraday retention, R12): start-of-day clears
them for a fresh day; end-of-day clears them once the day has reconciled — and
**refuses** to clear while anything is still unconfirmed, so a day is never
silently discarded with outstanding messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.reconcile import unconfirmed

if TYPE_CHECKING:
    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


class UnreconciledDayError(RuntimeError):
    """Raised when end-of-day is asked to clear with confirmations outstanding."""


def clear_archives(messaging: Messaging, queues: QueueSet) -> int:
    """Empty both archive queues. Returns the total number of messages cleared."""
    return messaging.clear(queues.send_archive) + messaging.clear(queues.reply_archive)


def start_of_day(messaging: Messaging, queues: QueueSet) -> int:
    """Clear the archives for a fresh business day. Returns messages cleared."""
    return clear_archives(messaging, queues)


def end_of_day(messaging: Messaging, queues: QueueSet, *, force: bool = False) -> int:
    """Clear the archives at end of day.

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
    return clear_archives(messaging, queues)
