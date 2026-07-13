"""Automated recovery: resend unconfirmed messages past an age threshold.

A second-order capability that reconciliation makes possible — you cannot resend
what you cannot identify. Given the current trading day's unconfirmed set and each
message's age, resend those outstanding longer than ``older_than`` via ``replay``
(reusing the original MsgId, so the counterparty dedups — a resend is confirmed
once, never processed twice, I2).

Safely repeatable: a message that stays unconfirmed is simply resent again on the
next pass, and the audit queue's ``CAPEXPRY`` (24 h) bounds how long that can go
on — so this cannot resend forever. The caller decides the cadence and the threshold;
this is the *policy-free* mechanism.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab import reconcile, replay

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from gwlab.config import QueueSet
    from gwlab.messaging import Messaging


def resend_aged(
    messaging: Messaging, queues: QueueSet, now: datetime, older_than: timedelta
) -> list[bytes]:
    """Resend today's unconfirmed messages older than ``older_than``.

    Returns the MsgIds resent (empty if none are old enough). Selection uses the
    current-day reconciliation (which carries each message's age); the bodies come
    from the same audit queue, joined by MsgId.
    """
    aged = {
        item.msg_id
        for item in reconcile.unconfirmed_detail(messaging, queues, now)
        if item.age >= older_than
    }
    if not aged:
        return []
    to_resend = [u for u in reconcile.unconfirmed(messaging, queues) if u.msg_id in aged]
    replay.resend(messaging, queues, to_resend)
    return [u.msg_id for u in to_resend]
