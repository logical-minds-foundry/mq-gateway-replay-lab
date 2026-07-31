# Streaming stateful collector — design

The batch collector (spec §11–§12) is correct and simple: every pass it browses
both audit queues end to end and folds the whole retained window into one
`DayReport`. The #123 scale experiment proved that this does not scale — the pass
is dominated by re-reading messages it has already read, and at a realistic
multi-day retention the pass cannot complete inside a Prometheus scrape interval.

This document specifies the **streaming stateful collector** that replaces it: a
long-running client-mode service (on the observability probe host, alongside the
base MQ exporter) that reads every message once, incrementally maintains the
settlement picture in memory, and publishes a pre-computed snapshot the scraper
reads for free.

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
2. **Maintain, don't recompute.** Read each message *once*: a single sweep loop
   starts from empty, drains every queue, and thereafter folds only *new arrivals*
   into an in-memory state. Steady-state cost becomes O(new-per-interval), not
   O(all-retained).

Together these turn an O(N)-per-scrape batch job into an O(Δ)-per-cycle service.

---

## 3. Architecture — a long-running service, four concerns

```
                      ┌──────────────────────────────────────────┐
   GW.SEC.*.AUDIT ───▶│  ingest (1 thread)   state (in-memory)    │
   (2 queues now,     │  one sweep loop:     dict[SEME → State]    │
    3 with COA/COD) ──▶│  BROWSE_FIRST →      incremental fold      │
                      │  drain → deltas      lifecycle-aged        │
                      │        │                    │             │
                      │        ▼                    ▼             │
                      │   snapshot (atomic swap of a metrics obj) │
                      │        │                                  │
                      │        ▼                                  │
   Prometheus  ◀──────│  HTTP responder (separate thread)         │
   (scrapes freely)   │  serves last snapshot in ms               │
                      └──────────────────────────────────────────┘
        one process, client-mode, a systemd service on mon-probe
        (same deployment shape as the base MQ Prometheus exporter)
```

The four concerns:

- **Ingest** — a single thread that sweeps the queues and folds messages.
- **State** — the maintained per-instruction settlement picture.
- **Snapshot** — the pre-computed metrics the scraper reads.
- **HTTP responder** — a separate thread that serves the snapshot.

"Single-threaded" (§4) refers to the **ingest** loop. The HTTP responder is a
distinct thread doing an atomic read of the last snapshot — that is a background
worker plus a dumb server, not the multi-threaded *ingest* we rejected.

---

## 4. Ingestion — one continuous sweep loop (single-threaded)

**There is no distinct cold-load phase.** The same loop that rebuilds from empty
is the one that keeps the picture current. On start the state is empty and every
queue's cursor is at `BROWSE_FIRST`; the loop sweeps the queues **in order** —
instructions, then responses, then (with COA/COD) the report queue — draining each
and folding as it goes. When it catches up it simply keeps sweeping; the same
persistent cursors now return only new arrivals. "Cold-load" is just the first lap
of the loop, by definition (nothing has been read yet). A crash/restart is not a
special path: every cursor re-opens at `BROWSE_FIRST` and the first lap rebuilds.

Mechanics that make this correct and cheap:

- **One continuous cursor per queue.** `MQGMO_BROWSE_FIRST` once, then
  `MQGMO_BROWSE_NEXT` forever. A browsed message is never re-browsed, so we must
  fold it when we see it (see the correlation invariant, §5). Any scheme that
  re-opens or re-scans to "catch stragglers" would either miss messages or
  double-count — rejected.
- **Zero browse-wait.** The get uses `WaitInterval = 0` (`MQGMO_NO_WAIT`): an
  empty queue returns `MQRC_NO_MSG_AVAILABLE` (2033) immediately, so the loop
  hops straight to the next queue. The only wait is a **loop-level sleep** between
  full sweeps, so the loop neither blocks per-queue nor spins.
- **Sweep order matters** — instructions before responses — and it is load-bearing
  for correlation (§5), not just tidy.

### Single-threaded, not parallel — the evidence

**Steady-state per-cycle volume is tiny.** 100k instructions/day + ~300k responses
≈ 400k msg/day ≈ **4.6 msg/s across all queues**. At a 60 s sweep that is ~280 new
messages total; at 650 µs/msg that is **~180 ms of browse per cycle**,
single-threaded, all queues. Even at 5× the volume it is under a second. The O(N)
pain existed only because the batch design re-read *everything*; once the sweep is
incremental, the per-cycle delta is small regardless of thread count.

**Multi-threading would only speed up the first lap** (250k @ 650 µs ≈ ~160 s
serial → maybe ~70 s across three threads) — which is the accepted recovery window
(§10). It buys nothing in steady state while adding shared-state locking on the
fold dict and per-thread reconnect handling. It is *viable* (pymqi releases the
GIL on the blocking MQGET, so browse threads overlap round-trips) but rejected
because the volume it would optimise does not occur in steady state. If first-lap
time ever becomes an operational problem, parallelise *only* the first lap — a
YAGNI added later with evidence.

---

## 5. State model — incremental fold, correlation, retention

- **Store:** `dict[SEME → InstructionState]`. Instructions create entries;
  responses fold into the matching entry.
- **Incremental == batch.** The lifecycle fold is monotonic along the settlement
  reachability graph (spec §7–§8), so applying hops as they arrive yields the same
  terminal state as folding a full correlated set. The existing `lifecycle.fold`
  is reused by re-folding one instruction's small hop-set on each new message
  (O(hops) — trivial); only its *driver* changes from batch to incremental.

- **Correlation invariant (why there is no pending buffer).** *When we browse any
  response R for instruction I, I's audit copy is already in state — unless I has
  expired off the instruct queue.* R exists only because I was sent, and I's audit
  copy is streamed in I's own PUT UOW, so it is on the instruct queue at `t_I`;
  R appears at `t_R = t_I + RTT`, `RTT > 0`. With zero browse-wait and the
  instruct-before-response sweep order:
  - if I arrived before this sweep finished the instruct queue, we browsed it (this
    lap or earlier) → in state; else
  - I arrived *after* we passed the instruct queue this lap, but then R cannot
    exist until `t_I + RTT`, well after this lap's near-instant hop to the response
    queue — so we browse R on a *later* lap, by which time I is in state.

  The only escape is I expiring off the instruct queue before we ever browsed it —
  the **cold-reload trailing edge** — which is not a "wait for I" case (I is never
  coming) but the trailing-edge rule below. So **no pending buffer**; the two
  constraints that guarantee this (zero browse-wait, instruct-before-response
  order) are correctness requirements, not preferences.

- **Retention (lifecycle-aware).** Keep an instruction in memory **while its
  lifecycle is open, regardless of age** — a trade "matched but never confirmed,
  two days old" is exactly the attention set and must stay reportable past its
  instruction's nominal timeout. **Purge only when it is both complete (settled /
  rejected / failed) *and* past the window.** This self-bounds memory (open items
  are few; completed items leave on expiry). Tradeoff, made explicit: on a
  cold-reload we rebuild only from what is still on the queue, so an open lifecycle
  whose instruction already expired will not come back.

- **Orphan detection is window-gated.** Because `CAPEXPRY` is per-message and the
  instruction is the *oldest* message in a lifecycle, near the trailing edge the
  instruction expires while its responses linger for up to T+2/T+3 more days — so
  on a cold-reload, "response with no instruction" is the **common** case at the
  edge, not an anomaly. A naive orphan rule would flood false orphans on every
  restart. Rule: **flag a response as an `orphan` only if its referenced
  instruction should still be in-window** (recent). An old response whose
  instruction has legitimately expired is a trailing-edge artifact — dropped, not
  alarmed. (Reconstructing a partial lifecycle from the responses alone — a
  confirmation self-describes "settled" via its MT + `RELA` — is a deferred
  enhancement, §13.)

- **Anomaly parity — all four kinds.** The streaming state surfaces the *same*
  unified anomaly channel as the batch collector: `malformed` and
  `field-violation` from per-message decode (`collect._decode_and_parse`),
  `orphan` (window-gated, above), and `transition` (from the fold). No kind is
  dropped in the streaming path — a malformed body is a counted anomaly that never
  aborts the sweep, exactly as in the batch collector (no silent failures).

- **Aging** runs on a timer against a **central config window** (the same config
  that derives the queue names), set identical to the queues' `CAPEXPRY` — no need
  to inquire the QM. The existing `Horizon` models it.

- **Memory.** State is O(instructions-in-window), *not* O(messages) — small
  per-SEME records, not raw bodies (tens of MB at multi-day retention, versus the
  ~289 MB a batch browse-list of 100k `Message` objects measured).

---

## 6. Publish — a snapshot the scrape reads for free

After each sweep the maintained state is rendered to a metrics snapshot (the
existing Prometheus exposition), published by an **atomic swap** of a small
immutable object (a bare reference assignment is atomic under the GIL — no lock).
The HTTP responder thread serves the current snapshot on every scrape with a
single atomic read — no fold, no browse. Scrape latency is constant and
independent of retained volume; Prometheus may scrape as often as it likes and
never triggers the expensive work.

### 6.1 Reporting semantics — gauges, windowed counts, the trailing edge

Two kinds of metric behave differently at the trailing edge, and separating them
dissolves most of the "how long do completed items stay reported?" question:

- **State / attention gauges** — the in-flight distribution and the aged-out
  "replay the unconfirmed" set. These are *current* snapshots; a completed
  lifecycle simply leaves the in-flight buckets the instant it reaches terminal.
  **No trailing-edge choice needed** — this, the operationally important metric,
  is clean.
- **Outcome counts** — settled / rejected / failed. A count only means something
  over a window, so report these as **windowed gauges over the one retention
  window**: "settled within the retained N-day window." The trailing edge is then
  not arbitrary — it is the *same single knob* that governs queue `CAPEXPRY`, the
  in-memory retention (§5), and metric windowing. A completed lifecycle stays
  counted until it is complete-and-expired, then drops.

Deferred (§13): monotonic `..._total` counters for finer Grafana windowing
(`increase(...[1h])`, best-effort across reloads), and a settlement-date dimension
(group by SETT/ESTT) if a per-day view proves useful.

---

## 7. Self-observability — the watcher watching itself

The collector publishes metrics about **its own** health alongside the settlement
metrics, extending the `swift_settlement_collect_seconds` seed (#121):

- `swift_collector_bootstrap_seconds` — first-lap / post-restart rebuild time.
  The recovery-time number, now a first-class, alertable metric.
- `swift_collector_cycle_seconds` — incremental-sweep duration, as a
  histogram/summary so the *distribution* is visible, not just the last value.
- `swift_collector_messages_ingested_total` — throughput counter.
- `swift_collector_cycle_backlog` — new messages seen in the last sweep; a proxy
  for whether the single-threaded loop is keeping up.
- `swift_collector_last_publish_timestamp_seconds` — so a stalled or
  mid-bootstrap collector is visible as snapshot staleness rather than silence.

These self-metrics **supersede the batch collector's per-pass scan-cost gauge**
(`swift_settlement_scanned` / `swift_settlement_collect_seconds`), which has no
per-scrape meaning once the fold is decoupled from the scrape: `cycle_seconds` is
the fold cost and `messages_ingested_total` the throughput. The settlement
exposition therefore reports the current in-state **population**, not a per-pass
browse count — a one-time meaning change to note at the board cutover (T8).

---

## 8. Deployment — a client on the observability probe (consistent with the base exporter)

The collector runs the **same deployment shape as the base MQ Prometheus exporter**
(the lab's `mq-exporter` role): a **client-mode** process on the fixed
off-cluster **`mon-probe`** host, as a **systemd** unit — *not* a QM service, not
on the QM node. This was a deliberate reversal (an earlier draft put it on the QM
node in bindings mode); the deciding factor is **consistency with how the lab
already collects MQ metrics**, and it happens to also be the simplest option:

- **Follows Native-HA failover via a CCDT CONNAME list**, exactly as the base
  exporter does — the CCDT lists every node so the client reaches whichever holds
  the QM. Native-HA **has no VIP** (it is not part of the documented Native-HA
  requirement), so a CONNAME list, not a VIP, is the mechanism. (A VIP would list
  ~2 addresses instead of ~6; behaviourally a wash.)
- **The scrape target is fixed** — `mon-probe:<port>` — so the moving-target
  problem of a QM-node service does not arise. Series are labelled by **`qmgr`**
  (the stable identity that "moves" logically across failover), as
  `mq-metric-samples` already does — so the board is continuous across a failover.
- **Monitoring identity.** A dedicated monitoring `SVRCONN` + keystore in the
  style of the base exporter's `MON.SVRCONN` / `mq_prometheus` cert — not the app
  or responder identities.
- **No extra machine, minimal boundary.** `mon-probe` already exists and already
  runs the base exporter; we add ours beside it, defined by *this repo's* Ansible.
  Rebuild the base lab, re-run our automation, our collector returns.

**Cost of this choice:** client-mode keeps the measured 650 µs/msg IO, so the
first-lap / recovery time stays in *minutes* rather than the seconds bindings-mode
would give. Accepted — steady-state cost is tiny either way (§4), and recovery is
a rare cold-reload. **Bindings-mode on the QM node is kept as a documented escape
hatch** (§11) if the first-lap time at multi-day depth proves unacceptable; our
bulk-browse workload (unlike the base exporter's cheap PCF stat queries) is a
legitimate reason it *could* one day justify diverging.

### 8.1 Service supervision & crash-restart

Consistent with the base exporter, the collector is a **systemd** service with
`Restart=always`. The **principle** we carry from the lab's `MQ.EVENT.MONITOR`
(`amqsevt`) wrapper is *crash → surfaced → restart, and fail loud on a crash-loop*
— realised here by systemd rather than an MQ `SERVICE` + `setsid` wrapper:

- **Crash → restart → cold-reload.** On a genuine crash systemd restarts the unit;
  the first lap (§4) rebuilds state. This is the **first-order** behaviour and all
  the PoC needs.
- **Fail loud on a crash-loop.** `StartLimitIntervalSec` / `StartLimitBurst` make
  repeated fast failures drive the unit into `failed` (alertable) rather than an
  invisible restart loop. Failure at the boundary is loud, never swallowed.
- **Alerting signals:** the `failed` unit state plus the self-metrics (§7 — a
  climbing `bootstrap` count, a stale `last_publish_timestamp`).

**Deployment mode dictates the correct crash policy.** A *bindings/local* service
*should* die with the QM (no reconnect logic — it restarts with the QM). A
*client* need not: it cannot even tell a network blip from a failover, so it may
be made **more stable than the QM it watches**. For the PoC we take the simple
first-order path (lose connection → crash → restart → cold-reload). A
**higher-order, production** option — a resilient client that catches
`MQRC_RECONNECTED`, reconnects, and reopens its cursors *without* a full restart
(fresher stats, no reload gap) — is deferred (§13). It also raises **service
redundancy** as its own system requirement: a client does not inherit the QM's HA
the way a co-located bindings service would — that is a system-implementation
concern, not a PoC one.

The exclusive-input handling the event-monitor wrapper needs does **not** apply
here: `amqsevt` opens its event queue `DEFSOPT(EXCL)` and must ride out the QM's
~29 s dead-handle reap on restart (the fail-twice-then-succeed 2042 pattern). We
consume by **non-destructive browse** on **shared** audit queues (other apps
browse them by design — the point of an audit copy), so there is no exclusive
constraint and no reap race.

**Aspiration.** This *first-lap-rebuild + incremental-maintain + supervised-restart*
pattern is meant to double as a **reference recovery design for a production MQ
gateway** — a motivation for building it here, not just a lab convenience.

---

## 9. Packaging & install — mimic the production end-state

The systemd unit must run an **installed entry point**, not source. Running
`PYTHONPATH=…/src python -m gwlab.swift.serve_collector` bakes a source-tree layout
and a hand-built interpreter into the unit — adequate to spike, but not how this
deploys in the real world, and it makes the *deployment* half of the #65 proof
non-representative. The real line is:

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
  systemd unit runs `/opt/gwlab/bin/gwlab-collector`. This mimics a production
  install the same way the AMQ-7T shell-script role mimics a packaged install —
  the target is "installed built artifact + entry point," reached by a local build
  rather than a package index.
- A frozen native binary (PyInstaller/Nuitka) is **not** pursued: our code
  dynamically links MQ's libraries through pymqi, freezing that is fragile, and the
  artifact-plus-entry-point path already achieves "looks like a real install."

**Aspiration — a standalone utility.** The collector is generic enough to be its
own small installable, deployed *like* the MQ Prometheus exporter and pointed at a
configured queue set — useful to anyone doing SWIFT-over-MQ, independent of this
lab. Extraction into its own repo/product is a follow-on (§13); the `console_scripts`
structure keeps that door open without opening it now.

The broader lab-wide retrofit (moving the exporter and responder off the
source-tree model, a possible RPM) is **out of scope here** (§13). This design
commits only to: *the collector runs an installed entry point produced by our
Ansible.*

---

## 10. Recovery semantics

The durable truth lives on the queues; the collector persists nothing. A crash or
failover is recovered by **rebuilding** in-memory state from the first lap of the
sweep loop (§4) — the same property that made the batch collector safe, kept
intact. Recovery time *is* the first-lap time, which is why it is a first-class,
published metric (§7). In client mode at multi-day depth that is *minutes*
(accepted); the bindings-mode escape hatch (§8, §11) is the lever that would
shorten it if ever needed. The deferred resilient-reconnect client (§8.1, §13)
would avoid the reload entirely on a failover, at the cost of complexity the PoC
does not need.

---

## 11. De-risks (early tasks)

1. **pymqi + our artifact on `mon-probe`.** `mon-probe` already carries MQ client
   libraries (it builds the base exporter), so pymqi builds there; provision it,
   the CCDT, and the monitoring identity via our Ansible.
2. **Measure client-mode first-lap time at multi-day depth** — confirm it is
   acceptable (minutes). If not, the **bindings-mode escape hatch** is the decided
   fallback. Same evidence-first move that produced this design.
3. **Validate persistent-cursor semantics** — that a long-lived `BROWSE_NEXT`
   handle reliably returns only new arrivals across sweeps, its behaviour under the
   window's `CAPEXPRY` expiry, and that a client reconnect re-opens cleanly at
   `BROWSE_FIRST`.

---

## 12. Task breakdown (build order)

- **T1 — packaging & install skeleton.** `console_scripts` entry point
  (`gwlab-collector`); build a wheel/`shiv` artifact; Ansible installs it to
  `/opt/gwlab` on `mon-probe` and runs it under a systemd unit. Proves the install
  model end-to-end.
- **T2 — client connectivity + first-lap measurement** (de-risks §11.1–§11.2).
  pymqi + CCDT (CONNAME list) + monitoring identity on `mon-probe`; measure the
  client-mode first-lap time at multi-day depth; record the bindings escape-hatch
  decision.
- **T3 — the sweep-loop ingest core.** One continuous cursor per queue,
  `BROWSE_FIRST` → drain → `BROWSE_NEXT` deltas, zero browse-wait + loop sleep,
  instruct-before-response sweep order; enforce the correlation invariant (§5).
  Pure logic, unit-tested at the boundary (no coverage-omit — mock the messaging
  port).
- **T4 — incremental state + retention.** Per-SEME incremental fold;
  **lifecycle-aware retention** (keep while open, purge when complete-and-expired);
  **window-gated orphan** detection; timer aging. No pending buffer (§5).
  Unit-tested.
- **T5 — snapshot + HTTP thread + reporting semantics.** Atomic snapshot swap;
  separate serving thread; state/attention **gauges** vs **windowed outcome
  counts** (§6.1) served from maintained state.
- **T6 — self-observability metrics** (§7).
- **T7 — `mon-probe` deployment.** systemd unit (`Restart=always` +
  start-limit fail-loud, §8.1); CCDT CONNAME failover-follow; `qmgr`-labelled
  series; the Prometheus target (fixed `mon-probe`); reconnect → crash → restart →
  cold-reload (first-order). Ansible role.
- **T8 — validation & cutover.** Repoint Prometheus at the new fixed target,
  **decommission the `app-client` batch exporter**; confirm scrape cost is flat,
  steady-state sweep time is small, and a forced Native-HA failover recovers within
  the published `bootstrap_seconds`.

Each carries its own tests and is independently reviewable; T3–T6 are pure logic
and must hold the no-coverage-omit discipline (mock the MQ boundary).

---

## 13. Out of scope / notes

- **Higher-order resilient client** — catch `MQRC_RECONNECTED`, reconnect and
  reopen cursors without a full restart (fresher stats, no reload gap), and the
  **service-redundancy** requirement that a client (unlike a co-located bindings
  service) does not inherit from the QM's HA. Production concerns, not PoC.
- **Bindings-mode escape hatch** — the QM-node bindings deployment that would
  collapse first-lap/recovery time; decided by the §11.2 measurement, not assumed.
- **Response-only lifecycle reconstruction** — rebuild a partial lifecycle from
  responses alone when the instruction has expired (a confirmation self-describes
  via MT + `RELA`); richer trailing-edge fidelity, deferred.
- **Finer outcome windowing** — monotonic `..._total` counters and a
  settlement-date dimension, if per-hour / per-day views prove useful.
- **Standalone-utility extraction** — the collector as its own installable
  product, deployed like the MQ exporter against any SWIFT-over-MQ queue set.
- **Lab-wide packaging retrofit** — moving the exporter and responder off the
  source-tree model, a possible RPM.
- **COA/COD** — the third audit source is a separate follow-on epic; this design
  accommodates N queues so adding it is a config + one-cursor change, not an
  architectural one.
- **Multi-threaded ingest** — measured unnecessary in steady state; revisit only
  with evidence that first-lap time is an operational problem, and then only for
  the first lap.
