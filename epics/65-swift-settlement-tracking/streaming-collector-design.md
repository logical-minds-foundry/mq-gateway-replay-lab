# Streaming stateful collector — design

The batch collector (spec §11–§12) is correct and simple: every pass it browses
both audit queues end to end and folds the whole retained window into one
`DayReport`. The #123 scale experiment proved that this does not scale — the pass
is dominated by re-reading messages it has already read, and at a realistic
multi-day retention the pass cannot complete inside a Prometheus scrape interval.

This document specifies the **streaming stateful collector** that replaces it: a
long-running queue-manager service that cold-loads the window once, then
*incrementally* maintains the settlement picture as new messages arrive and
publishes a pre-computed snapshot the scraper reads for free.

The design decisions below were each made against **measured** quantities from the
live lab, not guessed ones — that discipline is the point.

---

## 1. Motivation — the measured wall

The batch collector's cost is O(N) in the *total retained* message count, paid in
full on every pass. Measured on the live Native-HA queues from a client:

| audit depth | scan time | rate |
|---:|---:|---:|
| 10,000 | 6.79 s | 1,472 msg/s |
| 25,000 | 18.95 s | 1,319 msg/s |
| 50,000 | 35.55 s | 1,406 msg/s |
| 100,000 | 75.72 s | 1,321 msg/s |

Linear, ~1,400 msg/s. Two consequences:

1. **It already exceeds a 30 s scrape at 100k** (76 s), and settlement is a
   **multi-day** concern — T+2/T+3 means two to three days of instructions must
   stay visible, so the retained window is 250k+ messages and the pass is ~5 min.
2. **It re-does identical work every pass.** A message folded on pass *k* is
   browsed and parsed again on pass *k+1*, *k+2*, … forever, until it ages out.

A profile of one 100k pass shows exactly where the time goes:

```
IO      browse : 65.04 s   650.4 us/msg   97.4%
COMPUTE parse  :  1.41 s    14.1 us/msg    2.1%
COMPUTE fold   :  0.35 s     3.5 us/msg    0.5%
```

**97.4 % is IO** — per-message MQGET-browse round-trips. The parse-and-fold
compute is 2.6 % of the cost (1.76 s for the whole 100k). Two firm conclusions
follow:

- **The bottleneck is re-reading messages**, not computing metrics.
- **Language is irrelevant.** A C/Rust rewrite would attack the 2.6 %. Python
  stays.

This is not premature optimisation: the batch design shipped, we measured it at
real volume, and the measurement — not a hunch — says a stateful streaming design
is required for a production-grade prototype.

---

## 2. The fix, in two moves

1. **Decouple the fold from the scrape.** The expensive work runs on the
   collector's own cadence and writes a snapshot; the scrape endpoint only serves
   the last snapshot, in milliseconds, however long the fold took and however
   often Prometheus polls.
2. **Maintain, don't recompute.** Read each message *once*: cold-load the window
   at startup, then fold only *new arrivals* into an in-memory state on each
   incremental pass. Steady-state cost becomes O(new-per-interval), not
   O(all-retained).

Together these turn an O(N)-per-scrape batch job into an O(Δ)-per-cycle service.

---

## 3. Architecture — a long-running service, four concerns

```
                      ┌──────────────────────────────────────────┐
   GW.SEC.*.AUDIT ───▶│  ingest (1 thread)   state (in-memory)    │
   (2 queues now,     │  cold-load once,     dict[SEME → State]   │
    3 with COA/COD) ──▶│  then BROWSE_NEXT    incremental fold     │
                      │  deltas per cycle    aging by window      │
                      │        │                    │             │
                      │        ▼                    ▼             │
                      │   snapshot (atomic swap of a metrics obj) │
                      │        │                                  │
                      │        ▼                                  │
   Prometheus  ◀──────│  HTTP responder (separate thread)         │
   (scrapes freely)   │  serves last snapshot in ms               │
                      └──────────────────────────────────────────┘
             one process, bindings-mode, an MQ SERVICE on the QM node
```

The four concerns:

- **Ingest** — a single thread that browses new messages and folds them.
- **State** — the maintained per-instruction settlement picture.
- **Snapshot** — the pre-computed metrics the scraper reads.
- **HTTP responder** — a separate thread that serves the snapshot.

"Single-threaded" (§4) refers to the **ingest** loop. The HTTP responder is a
distinct thread doing an atomic read of the last snapshot — that is a background
worker plus a dumb server, not the multi-threaded *ingest* we rejected.

---

## 4. Ingestion — single-threaded round-robin (the decision, and the evidence)

There are N audit queues to browse (two today: the retained instructions and the
streamed responses; a third, COA/COD, is coming). Single-threaded round-robin vs.
a thread per queue is the pivotal choice. The measurement settles it toward
**simplicity**:

**Steady-state per-cycle volume is tiny.** 100k instructions/day + ~300k
responses ≈ 400k msg/day ≈ **4.6 msg/s across all queues**. At a 60 s incremental
cycle that is ~280 new messages total; at 650 µs/msg that is **~180 ms of browse
per cycle**, single-threaded, all queues. Even at 5× the volume it is under a
second. The O(N) pain existed only because the batch design re-read *everything*;
once incremental, the per-cycle delta is small regardless of thread count.

**Multi-threading would only speed up the cold-load** (250k @ 650 µs ≈ ~160 s
serial → maybe ~70 s across three threads) — and the cold-load is already accepted
as the recovery window (§8). It buys nothing in steady state while adding
shared-state locking on the fold dict, per-thread reconnect/error handling, and
the *perception* of complexity in a design whose value is being simple and
MQ-native.

To be fair to the alternative: multi-threading is *viable* — pymqi releases the
GIL on the blocking MQGET, so browse threads genuinely overlap network
round-trips; the GIL is not a dead-end here. It is rejected not because it cannot
work but because the volume it would optimise does not occur in steady state.

**Decision: single-threaded round-robin.** Each queue keeps its own **persistent
browse cursor** (`MQGMO_BROWSE_FIRST` once, then `MQGMO_BROWSE_NEXT` thereafter);
a single loop drains each queue's new arrivals in turn, folds them, refreshes the
snapshot, and sleeps to the next cycle. If cold-load time ever becomes an
operational pain, parallelise *only the bootstrap* and keep steady-state
single-threaded — a YAGNI added later with evidence, not now.

---

## 5. State model — incremental monotonic fold

- **Store:** `dict[SEME → InstructionState]`. Instructions create entries;
  responses fold into the matching entry.
- **Incremental == batch.** The lifecycle fold is monotonic along the settlement
  reachability graph (spec §7–§8), so applying hops one at a time as they arrive
  yields the same terminal state as folding a full correlated set. The existing
  `lifecycle.fold` logic is reused; only its *driver* changes from batch to
  incremental.
- **Out-of-order.** A response whose instruction has not yet been ingested goes
  to a small **pending buffer**, resolved when the instruction arrives, and
  surfaced as an `orphan` anomaly only after it ages out unbound. In this
  QM-native flow an instruction's retained audit copy is on its queue long before
  its round-trip response returns, so cross-queue out-of-order is a rare edge, not
  a design driver — but it is handled explicitly, never assumed away.
- **Aging.** On a timer, evict instructions whose put-time falls outside the
  T+N window; this mirrors the queue's own `CAPEXPRY` expiry. The window is a
  **central config parameter** set identical across all queues (the same config
  that derives the queue names) — no need to inquire `CAPEXPRY` from the QM. The
  existing `Horizon` models this.
- **Memory.** The state is O(instructions-in-window), *not* O(messages). It holds
  small per-SEME records, not raw message bodies — tens of MB at multi-day
  retention, versus the ~289 MB a batch browse-list of 100k `Message` objects
  measured (the batch design materialised every body at once).

---

## 6. Publish — a snapshot the scrape reads for free

After each incremental cycle the maintained state is rendered to a metrics
snapshot (the existing Prometheus exposition), published by an **atomic swap** of
a small immutable object. The HTTP responder thread serves the current snapshot on
every scrape with a single atomic read — no fold, no browse, no lock contention.
Scrape latency is constant and independent of retained volume. Prometheus may
scrape as often as it likes; it never triggers the expensive work.

---

## 7. Self-observability — the watcher watching itself

The collector publishes metrics about **its own** health alongside the settlement
metrics, extending the `swift_settlement_collect_seconds` seed (#121):

- `swift_collector_bootstrap_seconds` — cold-load / post-failover reload time.
  The recovery-time number, now a first-class, alertable metric.
- `swift_collector_cycle_seconds` — incremental-pass duration, as a
  histogram/summary so the *distribution* is visible, not just the last value.
- `swift_collector_messages_ingested_total` — throughput counter.
- `swift_collector_cycle_backlog` — new messages seen in the last cycle; a proxy
  for whether the single-threaded loop is keeping up.
- `swift_collector_last_publish_timestamp_seconds` — so a stalled or
  mid-bootstrap collector is visible as snapshot staleness rather than silence.

---

## 8. Deployment — an MQ SERVICE, bindings-mode, that follows the QM

The collector runs as an **MQ `SERVICE` object** on the queue-manager node, in
**bindings mode** — the same shape as the lab's event-monitor service. This is
deliberate on two axes at once:

- **Follows the QM.** A `SERVICE` starts and stops with the queue manager. On a
  Native-HA failover the QM restarts on the new active node and its services start
  there; the collector simply runs its normal startup routine (cold-load,
  republish) on the new node. There is no browse cursor to preserve across
  failover — **failover is just a restart** — which dissolves the one hard
  correctness question about long-lived cursors. A brief metrics gap during the
  post-failover reload window is acceptable for this observability.
- **Kills the IO cost.** The measured 650 µs/msg is almost entirely client-mode
  TCP/TLS round-trip. Bindings mode talks to the co-located QM over shared memory,
  with no network hop — expected to cut per-message browse cost by an order of
  magnitude and collapse the cold-load/recovery time accordingly. (To be confirmed
  by measurement — see §11.)

Because the QM can be active on any Native-HA node, the collector code and the
`SERVICE` definition must be present on **all** nodes.

**Boundary.** Defining a `SERVICE` on the shared lab's queue manager and deploying
our code onto its nodes is a deliberate, owned extension — not a mutation the base
lab must know about. The parasite boundary here is about **ownership and
reproducibility**, not untouchability: everything we add is defined by *this
repo's* Ansible, layered on top of the base lab. Rebuild the base lab, re-run our
gateway-lab automation, and our service returns. The base lab neither knows nor
cares that a service was added.

### 8.1 Service supervision & crash-restart

The collector cannot be run as a bare process. Like the lab's `MQ.EVENT.MONITOR`
service (the `amqsevt` wrapper), it needs a **supervision wrapper** so that a crash
is surfaced and the service restarted. We mirror that proven pattern, with one
constraint removed.

**Carried over from the event-monitor wrapper:**

- The `SERVICE` (`CONTROL(QMGR)`, `SERVTYPE(SERVER)`) launches a **supervision
  wrapper** (`setsid -w -- /opt/gwlab/bin/gwlab-collector-run …`), stopped by a
  process-group SIGTERM (`STOPCMD /bin/kill -TERM -- -<pgid>`).
- **Clean-stop vs. crash falls out of the mechanism — no in-script signal trap.**
  The wrapper is a `while true` loop that re-launches the collector. A clean stop's
  group-SIGTERM hits the wrapper too and it terminates on the default disposition,
  so the loop never runs again — no orphan. A collector crash exits only the worker;
  the wrapper is still alive, so it logs, sleeps, and restarts (→ cold-reload).
- **Crash-loop bound + fail-loud.** A run lasting ≥ a healthy threshold resets the
  failure burst; repeated *fast* failures past a bound make the wrapper exit
  non-zero so the `SERVICE` goes **DOWN visibly** (alertable) rather than looping
  forever silently. Failure at the boundary must be loud, never swallowed.

**Removed — does NOT apply here.** The event monitor opens its event queue for
**exclusive** input (`DEFSOPT(EXCL)`), so its wrapper must ride out the QM's
consistent ~29 s dead-handle reap on restart — the deliberate
fail-twice-then-succeed 2042 pattern. Our collector consumes by **non-destructive
browse**; the audit queues are **shared by design** (other applications — and our
own collector and metrics — browse them concurrently, which is the whole point of an
audit copy). So there is **no exclusive-input constraint and no reap race**. Our
only fast-fail case is a genuine crash-loop (bad config, QM unavailable), handled by
the bound above.

**Alerting / observability.** The fail-loud `SERVICE`-DOWN state, plus the
self-metrics (§7 — a climbing `bootstrap` count and a stale
`last_publish_timestamp`), are the crash signals. Folding richer service-health
signals into the observability layer is a noted follow-on.

**Aspiration.** This *cold-reload + incremental-maintain + supervised-restart*
pattern is intended to double as a **reference recovery design for a production MQ
gateway** — one of the motivations for building it here, not just a lab convenience.

---

## 9. Packaging & install — mimic the production end-state

The `SERVICE` must run an **installed entry point**, not source. Running
`PYTHONPATH=…/src python -m gwlab.swift.serve_collector` bakes a source-tree layout
and a hand-built interpreter into the service wrapper — adequate to spike, but not
how this is deployed in the real world, and it makes the *deployment* half of the
#65 proof non-representative. The real line is:

> **installed artifact + stable entry point** (production) vs.
> **source tree + PYTHONPATH** (the hack).

A virtual environment is not itself the hack (`pipx` and many packaged Python apps
are venvs internally); *running from the source tree* is.

**Model:**

- This repo is **one distribution exposing many commands** via `console_scripts`:
  `gwlab` (the CLI), `gwlab-collector`, `gwlab-responder`. Multiple installable
  "snippets" are just entry points of one package — no repo-inside-repo, no N
  sub-projects. A component is extracted into its own repo only later, *if* it
  becomes a standalone generic product; that is an extraction, never a
  precondition.
- The build produces an **artifact** (a wheel, or a single-file `shiv`/`pex`).
  Ansible **installs the built artifact** to a fixed location (`/opt/gwlab`) so the
  `SERVICE` runs `/opt/gwlab/bin/gwlab-collector`. This mimics a production install
  the same way the AMQ-7T shell-script role mimics a packaged install — the
  mimicry target is "installed built artifact + entry point," reached by a local
  build rather than a package index.
- A frozen native binary (PyInstaller/Nuitka) is **not** pursued: our code
  dynamically links MQ's libraries through pymqi, freezing that is fragile, and the
  artifact-plus-entry-point path already achieves "looks like a real install."

The broader lab-wide retrofit (the exporter and responder run the source-tree
model today; a possible RPM; extraction into a standalone product) is **out of
scope here** and noted for the follow-on. This design commits only to: *the
collector `SERVICE` runs an installed entry point produced by our Ansible.*

---

## 10. Recovery semantics

The durable truth lives on the queues; the collector persists nothing. A crash or
failover is recovered by **rebuilding** in-memory state from a fresh cold-load —
the same property that made the batch collector safe, kept intact. Recovery time
*is* the cold-load time. That is precisely why cold-load cost is a first-class,
measured, published metric (§7), and why bindings mode (§8) matters: it is the
lever that turns a multi-minute recovery into a short one.

---

## 11. De-risks (early tasks)

1. **Bindings-capable pymqi on the QM nodes** — provisioned by our Ansible on all
   Native-HA nodes; prerequisite for a local, bindings-mode `SERVICE`.
2. **Measure the bindings cold-load speedup** — confirm the 650 µs → tens-of-µs
   expectation and the recovery-time collapse before banking on it. Same
   evidence-first move that produced this design.
3. **Validate persistent-cursor semantics** — that a long-lived `BROWSE_NEXT`
   handle reliably returns only new arrivals across cycles, and behaves under the
   window's `CAPEXPRY` expiry.

---

## 12. Task breakdown (build order)

- **T1 — packaging & install skeleton.** `console_scripts` entry points
  (`gwlab-collector`); build a wheel/`shiv` artifact; Ansible role installs it to
  `/opt/gwlab` with a stable entry point. Proves the install model end-to-end.
- **T2 — bindings-mode enablement + measurement** (de-risks §11.1–§11.2). Provision
  pymqi on the QM nodes; measure bindings vs client browse cost and cold-load time.
- **T3 — incremental ingest core.** Persistent per-queue browse cursors;
  cold-load then `BROWSE_NEXT` deltas; single-threaded round-robin. Pure logic,
  unit-tested at the boundary (no coverage-omit — mock the messaging port).
- **T4 — incremental state + aging.** Per-SEME incremental fold, pending buffer,
  window aging. Unit-tested.
- **T5 — snapshot + HTTP responder thread.** Atomic snapshot swap; separate
  serving thread; the settlement metrics served from maintained state.
- **T6 — self-observability metrics** (§7).
- **T7 — the `SERVICE` object + supervision wrapper + Ansible deployment** across
  Native-HA nodes. The `CONTROL(QMGR)` `SERVICE` launches a supervision wrapper
  (§8.1: `while true` restart loop, clean-stop-vs-crash by process-group SIGTERM,
  crash-loop bound that fails loud); follows-failover behaviour.
- **T8 — validation.** Live cutover from the batch exporter; confirm scrape cost is
  flat, steady-state cycle time is small, and a forced failover recovers within the
  published bootstrap time.

Each carries its own tests and is independently reviewable; T3–T6 are pure logic
and must hold the no-coverage-omit discipline (mock the MQ boundary).

---

## 13. Out of scope / notes

- **Lab-wide packaging retrofit** — moving the exporter and responder off the
  source-tree model, a possible RPM, and any extraction into a standalone product:
  follow-on, noted not built.
- **COA/COD** — the third audit source is a separate follow-on epic; this design is
  built to accommodate N queues so that adding it is a config + one-cursor change,
  not an architectural one.
- **Multi-threaded ingest** — measured to be unnecessary in steady state; revisit
  only with evidence that cold-load time is an operational problem, and then only
  for the bootstrap path.
