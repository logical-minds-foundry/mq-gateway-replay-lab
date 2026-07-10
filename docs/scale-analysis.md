# Reconciliation scale analysis (#35)

**Question.** As the archive fills toward a full trading day and beyond, what does
reconciliation cost — the browse of both archive queues plus the diff of the two
message-header sets? Where is the bottleneck, and does the approach hold at
one-to-two orders of magnitude past expected volume?

**Method.** Two measurements, both reproducible:

- **Offline** (`demo/scale_bench.py`) — `reconcile.report()` over in-memory
  archives at increasing sizes, timing the browse-and-diff CPU cost alone.
- **Live** — load a throwaway `SCALE` counterparty's archive on the running
  Native HA queue manager (NHAUAPP) over the mutual-TLS app channel, then time a
  real `reconcile.report()` scan (`scan_live.py`).

## Data (measured)

**Offline — the diff is cheap and linear** (~0.5 µs/header):

| trades (day) | headers scanned | scan time |
|---:|---:|---:|
| 1,000 | 1,980 | 0.8 ms |
| 10,000 | 19,800 | 4.2 ms |
| 100,000 | 198,000 | 72.5 ms |
| 1,000,000 | 1,980,000 | 1,078 ms |

**Live — the MQ browse dominates** (~500–650 µs/header):

| sample | headers | scan time | per header |
|---|---:|---:|---:|
| snapshot day (small) | 122 | 64 ms | 525 µs |
| loaded archive | 39,600 | 25,453 ms | 643 µs |

The two live points agree, so the per-header cost is stable and the scan is linear
in archive size.

## Finding

The **diff is not the bottleneck** — set-membership matching of 2M headers is ~1 s
of CPU. The bottleneck is the **client-mode `MQGET(BROWSE)` round-trip per message
header**, which is ~1000× the in-memory cost. Extrapolating the measured 643
µs/header:

> **A full 1,000,000-trade day (~2,000,000 headers) reconciled by a client-mode
> full-archive browse takes ≈ 21 minutes per scan.**

That directly bounds the reconciliation *interval*: you cannot browse the whole
archive every few minutes at full scale.

The **single queue-file size is not a limit** (prior design point, unchanged):
`MAXFSIZE(DEFAULT)` is ~2 TB; a full day at ~200 KB/message is ~200 GB — well
within one file.

## Recommendations (judgment)

1. **Do not scan the full archive client-mode at scale.** Run the reconciler on
   the queue-manager node over **local bindings**, which removes the per-message
   network round-trip that dominates the cost. (The lab's exporter runs
   client-mode for failover convenience; a production reconciler should not.)
2. **Reconcile incrementally.** Track a high-water mark and browse only messages
   arrived since the last pass, turning an O(archive) scan into O(new-since-last).
   The full-archive scan is only needed for a cold recovery.
3. **The 24 h `CAPEXPRY` cap bounds the worst case** to a single trading day's
   volume, so the archive does not grow without limit and neither does the scan.
4. **No work is needed on the diff itself** — it is negligible at every scale
   measured.

Net: the MQ-native archive reconciliation is sound at scale, but the *transport*
and *incrementality* of the scan are the levers — not the algorithm.
