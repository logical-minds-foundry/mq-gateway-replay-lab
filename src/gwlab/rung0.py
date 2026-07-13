"""Simulation of the team's Rung-0 async-log gateway — reproduces host-loss
silent loss (invariant I1 violated), for the honest contrast with the MQ-native
option.

Nothing here talks to MQ; it models the *design*: a single writer that appends to
a local log and **asynchronously** ships entries to two alternate log hosts, with
a manual restart on failure. The hole is that *accepting* a message (logging it
locally, at which point the app believes it is sent) is a separate step from
*shipping* it to a survivor. A host loss in that window leaves the message in
neither place — "sent, but no surviving record": the fatal third state.

Contrast: the MQ-native outbound two-put (``gwlab.outbound.send``) commits the
audit copy in the **same unit of work** as the send, so that window does not
exist — there is no accepted-but-unrecorded state, and thus no silent loss.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Rung0Gateway:
    """Single-writer gateway: local log + async ship to alternate hosts, no quorum."""

    local_log: list[bytes] = field(default_factory=list)
    alternates: list[bytes] = field(default_factory=list)
    _pending: list[bytes] = field(default_factory=list)

    def send(self, msg_id: bytes) -> None:
        """Accept a message: append it to the local log and mark it for shipping.

        The caller now believes the message is *sent* — but it is not yet on any
        alternate host.
        """
        self.local_log.append(msg_id)
        self._pending.append(msg_id)

    def ship(self) -> None:
        """Asynchronously replicate the pending entries to the alternate hosts."""
        self.alternates.extend(self._pending)
        self._pending.clear()

    def lose_host(self) -> None:
        """The host dies: the local log and anything not yet shipped are gone.

        Only what already reached the alternate hosts survives.
        """
        self.local_log.clear()
        self._pending.clear()

    def recover(self) -> set[bytes]:
        """After a manual restart on a surviving host, the only messages that can
        be replayed are those that reached the alternate logs."""
        return set(self.alternates)
