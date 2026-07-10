# Resilient MQ message gateway — proof of concept

A generic trading back-office needs a **message gateway**: applications connect to
the gateway over a proprietary TCP protocol; the gateway connects to IBM MQ, sends
each message to the counterparty, and — for **replayability** — records every
message and every confirmation so the day's flow can be reconstructed and
outstanding messages resent after a failure.

This document is the proof of concept: the problem and the failure modes of the
proposed design, a design ladder, the questions for the application team, the
MQ-native solution, and a **live demonstration on real Native HA MQ**.

## 1. The proposed design, and why it worries us

The application team's design: the gateway runs as a **single process on a box**
and **replicates its logs to two other boxes**; if the box dies, a human starts the
process on a box that has the logs and replays. Deliberately **not** highly
available.

Stated plainly, this makes the *gateway* less reliable than the MQ estate it
fronts — which is the opposite of what a component in a reliable, redundant
architecture should be. Concrete failure modes:

- **Host loss** is a single point of failure gated on **human** recovery time.
- **A replicated log with a failed write** cannot be used for replay — so "two log
  copies" does not mean "two usable replay sources." The corner cases were not
  worked through.
- **No deduplication** on replay: resending risks double-processing.
- **Correctness of the log-write algorithm** (what is written, in what order,
  under what unit of work) was not specified — the hardest part was left implicit.

None of this is a criticism of the goal; replayability is exactly right. It is the
*implementation* that needs the rigor MQ itself already embodies.

## 2. The design ladder

| Rung | Design | Property |
|---|---|---|
| **0** | Their async-log process, logs replicated to two boxes, human restart | Fragile; human-gated; log-write correctness unproven |
| **1** *(recommended)* | **MQ-native archive + reconciliation** — the queue manager atomically archives every message and confirmation; the gateway reconciles by browsing the archives | Recover-fast-correct; **no new vendor dependency** — it is MQ, which they already run and trust to be reliable |
| **2** | Rung 1 on a **Native HA** queue manager | The gateway's state inherits MQ's replication and failover — as reliable as the messaging it carries |

The key move: **stop hand-rolling a log**, and let the queue manager be the
durable, replicated, correct store it already is.

## 3. The MQ-native solution

- **Atomic archive via streaming queues.** Each live queue is configured
  `STREAMQ(<archive>) STRMQOS(MUSTDUP)`: every put to the send queue is atomically
  copied to its archive **with the same MsgId**, by the queue manager, with **zero
  application logic**. `MUSTDUP` means both-or-neither — if the archive copy cannot
  be written, the original put fails, so there is no "sent but unarchived" state.
  The same applies to confirmations arriving on the reply queue.
- **Reconciliation by browse.** An outbound message is *unconfirmed* when its MsgId
  has no matching reply CorrelId (the counterparty echoes the request MsgId back as
  the reply's CorrelId). Reconciliation **browses the archive queues only** — never
  the live path — so it never contends with throughput.
- **Self-clearing retention.** `CAPEXPRY(24h)` caps each archived copy's lifetime,
  so the archive is a **rolling one trading day** and clears itself — retiring
  start-of-day / end-of-day clearing. Reconciliation is scoped to the current
  trading day; prior-day messages are ignored.

Because it is all MQ-native and **base-licensed** (streaming queues are not MQ
Advanced), it adds no product the team must buy, learn, or trust anew.

## 4. Questions for the application team

The Phase-1 payload. Full list in the design spec (epic `#49`, §11); the
load-bearing ones:

1. **Forwarding.** Are confirmations only logged, or also forwarded onward? (It was
   not 100% clear and it changes the reply-path design.)
2. **Volumes.** Confirmed order of magnitude and the expected 1–2 orders of growth,
   to size retention and reconciliation (see §6).
3. **Topology.** One queue manager per counterparty, or shared? This decides whether
   one board can serve many counterparties.
4. **Recovery objective.** Acceptable time-to-recover and time-to-detect an
   unconfirmed message — this is what the observability exists to serve.
5. **Data-at-rest.** The moment a day of messages is *retained* (archive queues, or
   their replicated logs) it becomes data at rest — does that pull in on-disk
   encryption requirements?
6. **HA appetite.** Is "human restarts it" genuinely acceptable for a component in
   an otherwise-redundant path, or is that a gap to close (Rung 2)?

## 5. Live demonstration

Run end-to-end against **real Native HA MQ** (queue manager `NHAUAPP`, 3-node group
in sync) over **mutual TLS 1.3** (the app channel `APP.SVRCONN`, an `O=app-org`
client certificate minted by the lab PKI). The gateway provisions its own queues,
streaming archives, `CAPEXPRY`, reconcile exporter and Grafana board as a
loosely-coupled parasite — the lab is untouched.

A full trading day was simulated with a confirmation outage. **End-of-day state,
read straight off the archives by reconciliation:**

| Signal | Value |
|---|---|
| Sent | 70 |
| Confirmed | 52 |
| **Unconfirmed** | **18** — exactly the outage trades, left outstanding |
| Mean confirm latency | 21.2 s |
| Reconcile scan | 122 headers in 0.064 s |
| **Unconfirmed-set table** | **all 18 trades by MsgId, with sent-time and age** |

The invariants held live: **MsgId is preserved** into the archive by the streaming
copy (so reconciliation matches correctly — issue `#4`); the **CorrelId echo** makes
the match authoritative (`#5`); and replay is **deduplicated** by the counterparty,
confirmed once and never processed twice.

> The demonstration used a *compressed* timeline (a 5-minute outage), so the
> age-based states read within-SLA. Over a real trading day they escalate
> (awaiting → aged/escalate) exactly as the mockup shows — that full-day run is
> scoped as its own experiment (`#49`).

## 6. Scale — where the bottleneck actually is

Measured (full analysis in [`scale-analysis.md`](scale-analysis.md)):

- The **diff is cheap** — matching 2M headers is ~1 s of CPU; a full 1M-trade day
  reconciles in ~1.1 s in memory.
- The bottleneck is the **client-mode `MQGET(BROWSE)` round-trip per header**
  (~640 µs measured), which extrapolates to **~21 minutes for a full-archive scan
  at 1M trades/day**.

So the levers are the scan's **transport and incrementality**, not the algorithm:
run the reconciler on the queue-manager node over **local bindings**, and
**reconcile incrementally** (only messages since the last mark). `CAPEXPRY` bounds
the worst case to one day; the single queue file (~2 TB) is not a limit.

## 7. What the first-order data unlocks

Because reconciliation now *collects* the unconfirmed set with ages, second-order
capabilities become possible — you cannot take a derivative of data you do not
have:

- **Automated recovery** (`recovery.resend_aged`, issue `#36`): resend messages
  unconfirmed past a threshold, reusing the original MsgId so the counterparty
  dedups — bounded by `CAPEXPRY` so it cannot resend forever.
- **The observability differentiator**: the board answers *which specific trades
  are unconfirmed* — by MsgId, sent-time and age — not merely *how many*. That is
  the argument that sells the MQ-native option: a running artifact, not a paragraph.

## 8. Conclusion

Replayability is the right requirement; hand-rolling the log is the wrong
implementation. The queue manager already provides a durable, replicated, correct
store — streaming queues make the archive atomic and application-logic-free, and
reconciliation over the archives makes "which trades are unconfirmed" an
authoritative, MQ-level fact. Demonstrated live on Native HA over mutual TLS, with
the scale bottleneck quantified and a recovery path in hand.

Next: the full-day realistic run for a production-quality end-of-day board (`#49`),
and the design conversation with the application team driven by §4.
