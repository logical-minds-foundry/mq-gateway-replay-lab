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

**Compression-aware (load-sim §7).** Both predicates work in *sim-time*: the
horizon is scaled by ``1 / horizon.compression`` in real terms, so under a
time-compressed run (``C`` > 1) a whole multi-day sim collapses into real minutes
and aged-out still fires. At ``C = 1`` (the production default) the real horizon
is exactly ``horizon.days`` days and behaviour is unchanged.
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


def _real_horizon(horizon: Horizon) -> timedelta:
    """The horizon as real (wall-clock) elapsed time: sim-days scaled by ``1 / C``."""
    return timedelta(days=horizon.days) / horizon.compression


def in_window(put_time: datetime, now: datetime, horizon: Horizon) -> bool:
    """Whether a message put at ``put_time`` is still within the settlement window."""
    return now - put_time <= _real_horizon(horizon)


def is_aged_out(state: InstructionState, now: datetime, horizon: Horizon) -> bool:
    """Whether an instruction is non-terminal and has been open a full horizon.

    ``True`` when the folded lifecycle has not reached SETTLED / REJECTED / FAILED
    and the instruction (its INSTRUCTED hop) is at least a full horizon old — the
    in-flight instructions at the edge of retention that warrant a replay. The
    horizon is the sim-time window scaled by ``1 / horizon.compression``, so
    aged-out fires under compression (``C`` > 1) and collapses to ``horizon.days``
    real days at ``C = 1``.
    """
    if state.current in _TERMINAL:
        return False
    instructed_at = state.hops[0].at
    return now - instructed_at >= _real_horizon(horizon)
