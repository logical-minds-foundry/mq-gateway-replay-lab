"""The live ``Messaging`` implementation, backed by pymqi (needs the MQ client).

This is the one seam that talks to a real queue manager. Everything else in
``gwlab`` runs against it unchanged — the whole point of the ``Messaging``
protocol. It cannot be unit-tested in the validation container (pymqi needs the
MQ client libraries), so it is **excluded from coverage** and verified
end-to-end on the box (the audit queues are produced by the queue manager's
``STREAMQ … STRMQOS(MUSTDUP)``, so ``put`` is a single put — no app-side copy).

``pymqi`` is imported lazily so importing this module does not require the client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from gwlab.messaging import Message

if TYPE_CHECKING:
    from collections.abc import Iterator


def _put_time(md: Any) -> datetime | None:
    """Parse the queue manager's put timestamp (MQMD PutDate + PutTime) to UTC.

    PutDate is ``YYYYMMDD`` and PutTime is ``HHMMSShh`` (hundredths); MQ stamps
    them in UTC. Reconciliation needs this for current-day scoping and message
    age. An unstamped descriptor yields ``None`` (the reconciliation tier fails
    loudly on that rather than silently mis-dating a message).
    """
    date_raw = bytes(md.PutDate).strip(b"\x00 ")
    time_raw = bytes(md.PutTime).strip(b"\x00 ")
    if not date_raw or not time_raw:
        return None
    stamp = date_raw.decode() + time_raw.decode()[:6]  # YYYYMMDD + HHMMSS
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


class PyMqiMessaging:
    """A single reconnectable client connection to a real queue manager."""

    def __init__(
        self,
        qmgr_name: str,
        conn_name: str,
        channel: str,
        *,
        tls_cipher: str | None = None,
        key_repo: str | None = None,
        cert_label: str | None = None,
    ) -> None:
        """Connect a reconnectable client channel, optionally over mutual TLS.

        When ``tls_cipher`` is given the channel runs TLS: the cipher is set on the
        channel definition, and an ``MQSCO`` supplies the key repository (a GSKit
        stem — the ``.p12`` and its ``.sth`` stash sit beside it) and the client
        certificate label to present. Plaintext stays the default so the offline
        path is unaffected.
        """
        import pymqi  # type: ignore

        self._pymqi: Any = pymqi
        self._c: Any = pymqi.CMQC
        cd = pymqi.CD(
            ChannelName=channel.encode(),
            ConnectionName=conn_name.encode(),
            TransportType=pymqi.CMQC.MQXPT_TCP,
        )
        sco: Any = None
        if tls_cipher is not None:
            cd.SSLCipherSpec = tls_cipher.encode()
            sco = pymqi.SCO(KeyRepository=(key_repo or "").encode())
            if cert_label is not None:
                sco.CertificateLabel = cert_label.encode()
        self._qmgr: Any = pymqi.QueueManager(None)
        opts = pymqi.CMQC.MQCNO_RECONNECT_Q_MGR
        if sco is not None:
            self._qmgr.connect_with_options(qmgr_name, cd=cd, sco=sco, opts=opts)
        else:
            self._qmgr.connect_with_options(qmgr_name, cd=cd, opts=opts)
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
        options = self._c.MQPMO_SYNCPOINT
        if message.msg_id:
            md.MsgId = message.msg_id
        else:
            # Let the queue manager mint a globally-unique MsgId rather than assert
            # uniqueness ourselves; read it back so the caller can track the message.
            options |= self._c.MQPMO_NEW_MSG_ID
        md.CorrelId = message.correl_id
        pmo = self._pymqi.PMO(Options=options)
        handle.put(message.body, md, pmo)
        if not message.msg_id:
            message.msg_id = bytes(md.MsgId)

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
        return Message(
            body=body,
            msg_id=bytes(md.MsgId),
            correl_id=bytes(md.CorrelId),
            put_time=_put_time(md),
        )

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
            yield Message(
                body=body,
                msg_id=bytes(md.MsgId),
                correl_id=bytes(md.CorrelId),
                put_time=_put_time(md),
            )
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
