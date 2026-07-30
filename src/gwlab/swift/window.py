"""Settlement-window scoping: which messages are in scope, which have aged out.

A settlement lifecycle spans days, so v2 scopes retention and reporting by a
business-day *horizon* rather than the calendar day (spec §4.4). Two predicates
express that horizon:

- :func:`in_window` — is a message recent enough to still be in scope? The
  collector browses the retained audit copy and keeps only in-window messages,
  so a late (T+2) confirmation of an in-window instruction is still seen and
  folded, not dropped.
- :func:`is_aged_out` — has an instruction reached the end of the window without
  reaching a terminal state? These are the in-flight instructions at the edge of
  retention: the attention set for "replay the unconfirmed".

**Business-day approximation.** For the prototype the horizon is a plain
calendar-day delta: a message put strictly earlier than ``now - horizon.days``
days is out of scope. Sizing the horizon against real settlement calendars
(weekends, holidays) is a downstream concern (swiftconfig task #88); the delta is
deliberately simple and documented here so the shortcut is explicit.

The window boundary is inclusive at both ends: an instruction put exactly
``horizon.days`` days ago is still in scope *and*, if non-terminal, aged out —
which is precisely the "about to fall out of retention, still unconfirmed" case.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from gwlab.swift.reference import TERMINAL_NEGATIVE, State

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.lifecycle import InstructionState
    from gwlab.swift.swiftconfig import Horizon

#: A lifecycle is terminal for aging once it settles or ends negatively; SETTLED
#: plus the two terminal-negatives (spec §4). A terminal instruction is never
#: "aged out" — it has resolved, whatever its age.
_TERMINAL: frozenset[State] = TERMINAL_NEGATIVE | {State.SETTLED}


def in_window(put_time: datetime, now: datetime, horizon: Horizon) -> bool:
    """Whether a message put at ``put_time`` is still within the settlement window."""
    return now - put_time <= timedelta(days=horizon.days)


def is_aged_out(state: InstructionState, now: datetime, horizon: Horizon) -> bool:
    """Whether an instruction is non-terminal and has been open a full horizon.

    ``True`` when the folded lifecycle has not reached SETTLED / REJECTED / FAILED
    and the instruction (its INSTRUCTED hop) is at least ``horizon.days`` days old
    — the in-flight instructions at the edge of retention that warrant a replay.
    """
    if state.current in _TERMINAL:
        return False
    instructed_at = state.hops[0].at
    return now - instructed_at >= timedelta(days=horizon.days)
