"""The live ``Messaging`` implementation, backed by pymqi (needs the MQ client).

This is the one seam that talks to a real queue manager. Everything else in
``gwlab`` runs against it unchanged — the whole point of the ``Messaging``
protocol. It cannot be unit-tested in the validation container (pymqi needs the
MQ client libraries), so it is **excluded from coverage** and verified
end-to-end on the box (the archives are produced by the queue manager's
``STREAMQ … STRMQOS(MUSTDUP)``, so ``put`` is a single put — no app-side copy).

``pymqi`` is imported lazily so importing this module does not require the client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from gwlab.messaging import Message

if TYPE_CHECKING:
    from collections.abc import Iterator


class PyMqiMessaging:
    """A single reconnectable client connection to a real queue manager."""

    def __init__(self, qmgr_name: str, conn_name: str, channel: str) -> None:
        import pymqi  # type: ignore

        self._pymqi: Any = pymqi
        self._c: Any = pymqi.CMQC
        cd = pymqi.CD(
            ChannelName=channel.encode(),
            ConnectionName=conn_name.encode(),
            TransportType=pymqi.CMQC.MQXPT_TCP,
        )
        self._qmgr: Any = pymqi.QueueManager(None)
        self._qmgr.connect_with_options(qmgr_name, cd=cd, opts=pymqi.CMQC.MQCNO_RECONNECT_Q_MGR)
        self._queues: dict[tuple[str, int], Any] = {}

    def _open(self, queue: str, opts: int) -> Any:
        key = (queue, opts)
        handle = self._queues.get(key)
        if handle is None:
            handle = self._pymqi.Queue(self._qmgr, queue, opts)
            self._queues[key] = handle
        return handle

    def put(self, queue: str, message: Message) -> None:
        handle = self._open(queue, self._c.MQOO_OUTPUT)
        md = self._pymqi.MD()
        md.MsgId = message.msg_id
        md.CorrelId = message.correl_id
        pmo = self._pymqi.PMO(Options=self._c.MQPMO_SYNCPOINT)
        handle.put(message.body, md, pmo)

    def get(self, queue: str) -> Message | None:
        handle = self._open(queue, self._c.MQOO_INPUT_SHARED)
        md = self._pymqi.MD()
        gmo = self._pymqi.GMO(
            Options=self._c.MQGMO_SYNCPOINT
            | self._c.MQGMO_NO_WAIT
            | self._c.MQGMO_FAIL_IF_QUIESCING
        )
        try:
            body = handle.get(None, md, gmo)
        except self._pymqi.MQMIError as exc:
            if exc.reason == self._c.MQRC_NO_MSG_AVAILABLE:
                return None
            raise
        return Message(body=body, msg_id=bytes(md.MsgId), correl_id=bytes(md.CorrelId))

    def _browse_iter(self, queue: str) -> Iterator[Message]:
        handle = self._open(queue, self._c.MQOO_BROWSE)
        options = self._c.MQGMO_BROWSE_FIRST | self._c.MQGMO_NO_WAIT
        while True:
            md = self._pymqi.MD()
            gmo = self._pymqi.GMO(Options=options)
            try:
                body = handle.get(None, md, gmo)
            except self._pymqi.MQMIError as exc:
                if exc.reason == self._c.MQRC_NO_MSG_AVAILABLE:
                    return
                raise
            yield Message(body=body, msg_id=bytes(md.MsgId), correl_id=bytes(md.CorrelId))
            options = self._c.MQGMO_BROWSE_NEXT | self._c.MQGMO_NO_WAIT

    def browse(self, queue: str) -> list[Message]:
        return list(self._browse_iter(queue))

    def commit(self) -> None:
        self._qmgr.commit()

    def backout(self) -> None:
        self._qmgr.backout()

    def clear(self, queue: str) -> int:
        removed = 0
        while self.get(queue) is not None:
            removed += 1
        self.commit()
        return removed

    def disconnect(self) -> None:
        self._qmgr.disconnect()
