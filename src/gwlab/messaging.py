"""The MQI-shaped messaging interface the gateway logic depends on.

The design's correctness lives in *how* it drives this interface — two puts in
one unit of work with the same MsgId; a get-and-copy in one unit of work; a
browse of the archives — logic that is essentially identical across MQI
bindings. The gateway logic therefore depends on this ``Messaging`` Protocol,
never on ``pymqi`` directly, so it stays fully testable with an in-memory fake
and binding-agnostic (spec §2). The ``pymqi``-backed adapter is a thin layer
added and proven on the live box in Track C.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Message:
    """A message on a queue: the body plus the MQI header fields we rely on.

    ``msg_id`` is the correlation key: the counterparty echoes it back as the
    reply's ``correl_id``, which is what reconciliation matches on.
    """

    body: bytes
    msg_id: bytes
    correl_id: bytes = b""


class Messaging(Protocol):
    """The syncpoint-aware MQI operations the gateway uses.

    - ``put``: PUT under syncpoint (uncommitted until ``commit``).
    - ``get``: destructive GET under syncpoint; ``None`` if the queue is empty.
    - ``browse``: non-destructive read of every message on a queue.
    - ``commit`` / ``backout``: end the current unit of work.
    - ``clear``: purge a queue immediately (not transactional; e.g. MQSC
      ``CLEAR QLOCAL``). Returns the number of messages removed.
    """

    def put(self, queue: str, message: Message) -> None: ...
    def get(self, queue: str) -> Message | None: ...
    def browse(self, queue: str) -> list[Message]: ...
    def commit(self) -> None: ...
    def backout(self) -> None: ...
    def clear(self, queue: str) -> int: ...
