"""The app-side lifecycle tracker — the gateway's own second view of the lifecycle.

The collector derives each instruction's settlement lifecycle from the two audit
queues (source #1). The gateway app keeps its **own** book: it folds the
instructions it actually sent against the replies it actually drained off its live
put/get path (source #2). Two independent code paths over the same traffic, one
truth — they cross-validate per instruction (load-simulation design §2, §2.1).

This module is the pure, testable core of that view. :func:`track` correlates the
sent instructions with the received replies and folds each into an
:class:`~gwlab.swift.lifecycle.InstructionState` — the *same* fold the collector
runs, but over the app's live data instead of the audit queues.
:func:`app_end_states` reduces each folded state to the common
:class:`~gwlab.swift.endstate.EndState` record #122 diffs against the
collector-derived set.

The live runner that actually puts to ``GW.SEC.INSTRUCT`` and drains
``GW.SEC.REPLY`` on the box lives in :mod:`gwlab.swift.gateway_run` (box-only,
excluded from coverage — it opens a pymqi connection and runs a blocking
get-loop). The correlate+fold logic here is binding-agnostic and fully unit-tested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gwlab.swift import correlate, lifecycle
from gwlab.swift.endstate import end_state

if TYPE_CHECKING:
    from gwlab.swift.endstate import EndState
    from gwlab.swift.lifecycle import InstructionState
    from gwlab.swift.model import SwiftMessage


def track(sent: list[SwiftMessage], received: list[SwiftMessage]) -> list[InstructionState]:
    """Fold the app's own sent instructions against the replies it drained.

    Correlates ``sent`` (the MT540-543 instructions the app put) with ``received``
    (the MT548 / MT544-547 replies it got back) on SEME/RELA, then folds each
    correlated stream — the same monotonic fold the collector applies to the audit
    queues, over the app's live view. Orphan replies (a RELA matching no sent
    instruction) are not part of the app's per-instruction book, so they are not
    folded here; the collector is the channel that surfaces orphans as anomalies.
    """
    correlated, _orphans = correlate.correlate(sent, received)
    return [lifecycle.fold(item) for item in correlated]


def app_end_states(sent: list[SwiftMessage], received: list[SwiftMessage]) -> list[EndState]:
    """Map :func:`track` through :func:`~gwlab.swift.endstate.end_state`.

    The gateway app's per-instruction end-state records, in the common shape the
    cross-validation (#122) diffs against the collector-derived set.
    """
    return [end_state(state) for state in track(sent, received)]
