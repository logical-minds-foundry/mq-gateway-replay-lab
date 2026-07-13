"""Scale benchmark: reconciliation scan cost vs audit-queue size.

Answers #35 — what does the browse-and-diff cost as the audit queue fills toward
a full trading day and beyond? Measures the reconcile.report() scan (the CPU cost
of browsing both audit queues and diffing the two header sets) at increasing sizes,
offline and deterministic, so the cost curve is reproducible. The live per-header
cost (which adds the MQ browse round-trips) is measured separately on the box.

Run: python demo/scale_bench.py
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from gwlab import reconcile
from gwlab.config import QueueSet
from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message

_QS = QueueSet.for_counterparty("CPB")
_NOW = datetime(2026, 7, 10, 17, 0, tzinfo=UTC)
_DAY = _NOW - timedelta(hours=6)  # trades sent earlier today
_SIZES = (1_000, 10_000, 100_000, 1_000_000)
_CONFIRMED_FRACTION = 0.98  # a healthy day: most confirmed, a small unconfirmed tail


def _load(n: int) -> FakeMessaging:
    fm = FakeMessaging()
    confirmed = int(n * _CONFIRMED_FRACTION)
    # Load committed audit queues directly — we are timing the scan, not the puts.
    fm._committed[_QS.send_audit] = [  # noqa: SLF001 — benchmark harness
        Message(b"trade", i.to_bytes(24, "big"), put_time=_DAY) for i in range(n)
    ]
    fm._committed[_QS.reply_audit] = [  # noqa: SLF001
        Message(
            b"ack", b"R" + i.to_bytes(23, "big"), correl_id=i.to_bytes(24, "big"), put_time=_DAY
        )
        for i in range(confirmed)
    ]
    return fm


def main() -> None:
    print(f"{'audit':>10} {'headers':>10} {'unconfirmed':>12} {'scan (ms)':>10} {'M hdr/s':>9}")
    for n in _SIZES:
        fm = _load(n)
        start = time.perf_counter()
        report = reconcile.report(fm, _QS, _NOW)
        elapsed = time.perf_counter() - start
        scanned = report.metrics.scanned_headers
        rate = scanned / elapsed / 1e6
        print(
            f"{n:>10,} {scanned:>10,} {report.metrics.unconfirmed:>12,} "
            f"{elapsed * 1e3:>10.1f} {rate:>9.1f}"
        )


if __name__ == "__main__":
    main()
