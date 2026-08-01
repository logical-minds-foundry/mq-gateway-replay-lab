"""The MQI-shaped messaging interface the gateway logic depends on.

The design's correctness lives in *how* it drives this interface — two puts in
one unit of work with the same MsgId; a get-and-copy in one unit of work; a
browse of the audit queues — logic that is essentially identical across MQI
bindings. The gateway logic therefore depends on this ``Messaging`` Protocol,
never on ``pymqi`` directly, so it stays fully testable with an in-memory fake
and binding-agnostic (spec §2). The ``pymqi``-backed adapter is a thin layer
added and proven on the live box in Track C.
"""

from __future__ import annotations

from collections import namedtuple
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

#: MQMD ``Report`` option bits (values match IBM MQ ``CMQC`` so the pymqi adapter
#: maps them straight onto ``MQMD.Report``). We request the **plain** variants —
#: never ``_WITH_DATA`` — so the returned reports are body-less (spec §3): the
#: full instruction body is retained in the audit queue and correlated by id.
MQRO_COA = 0x00000100
MQRO_COD = 0x00000800

#: MQMD ``Feedback`` codes carried by the returned report messages (``CMQC``
#: values). A COA (Confirmation On Arrival) reports arrival on the destination
#: queue; a COD (Confirmation On Delivery) reports destructive consumption.
MQFB_COA = 259
MQFB_COD = 260

#: Report options attached to an outbound put (spec §3): ``report`` is the MQMD
#: ``Report`` bitmask (e.g. ``MQRO_COA | MQRO_COD``); ``reply_to_q`` /
#: ``reply_to_qmgr`` become ``MQMD.ReplyToQ`` / ``MQMD.ReplyToQMgr``, the return
#: address the counterparty queue manager routes the reports to. ``reply_to_qmgr``
#: may be ``None`` — left blank, MQ resolves it to the local (our) queue manager.
ReportOpts = namedtuple("ReportOpts", "report reply_to_q reply_to_qmgr")


@dataclass
class Message:
    """A message on a queue: the body plus the MQI header fields we rely on.

    ``msg_id`` is the correlation key: the counterparty echoes it back as the
    reply's ``correl_id``, which is what reconciliation matches on. It defaults to
    empty, meaning **let the queue manager mint a globally-unique MsgId** on put —
    the gateway never hand-assigns a predetermined value (that would fake global
    uniqueness). The put reads the assigned id back onto the message.

    ``put_time`` is when the queue manager stamped the message (MQMD PutDate +
    PutTime). The live adapter reads it on browse; it is the basis for
    current-trading-day scoping and the age of an unconfirmed message. It is
    optional because the gateway's put/get logic never needs it — only the
    reconciliation/observability tier does.

    ``feedback`` is the MQMD ``Feedback`` code, non-``None`` only on a returned
    **report message** (COA/COD; spec §3): it is the transport milestone's kind.
    A normal application message carries no feedback (``MQFB_NONE``), read as
    ``None``. The adapter reads it in the ``browse_new``/``browse``/``get`` sites;
    a body-less message with ``feedback`` set is a report, never tokenized.
    """

    body: bytes
    msg_id: bytes = b""
    correl_id: bytes = b""
    put_time: datetime | None = None
    feedback: int | None = None


class Messaging(Protocol):
    """The syncpoint-aware MQI operations the gateway uses.

    - ``put``: PUT under syncpoint (uncommitted until ``commit``).
    - ``get``: destructive GET under syncpoint; ``None`` if the queue is empty.
    - ``browse``: non-destructive read of every message on a queue.
    - ``browse_new``: non-destructive read behind a persistent cursor. The first
      call returns every message from the first; each later call returns only the
      messages that arrived since the previous call, and a message is never
      returned twice. After a reconnect the adapter re-opens at the first message
      (a full re-read = the cold-reload recovery path), so the caller's fold must
      be idempotent under a replay of already-seen messages. ``limit`` bounds a
      single call: ``limit > 0`` returns at most that many new messages (the
      cursor continues from there on the next call — the bounded-batch drain that
      keeps a giant delta from folding in one GIL-holding pass, issue #152);
      ``limit == 0`` (the default) returns the whole new delta.
    - ``commit`` / ``backout``: end the current unit of work.
    - ``clear``: purge a queue immediately (not transactional; e.g. MQSC
      ``CLEAR QLOCAL``). Returns the number of messages removed.
    """

    def put(
        self, queue: str, message: Message, *, report_opts: ReportOpts | None = None
    ) -> None: ...
    def get(self, queue: str) -> Message | None: ...
    def browse(self, queue: str) -> list[Message]: ...
    def browse_new(self, queue: str, limit: int = 0) -> list[Message]: ...
    def commit(self) -> None: ...
    def backout(self) -> None: ...
    def clear(self, queue: str) -> int: ...
