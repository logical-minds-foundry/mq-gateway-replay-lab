# Retrospective: SWIFT MT540–548 settlement-lifecycle tracking (epic #65)

> Partners `spec.md` and `plan.md` at the documentation tier. Read the three in
> order: **spec** (what we set out to do) → **plan** (how we planned it) →
> **retrospective** (honestly, how it went).

---

## §0 — At a glance

**What we set out to do.** v1 tracked confirmations by correlating MQ message
headers (MsgId/CorrId) — correct for the request/reply paradigm, wrong for SWIFT
securities settlement, which correlates on *reference fields inside the message
body* (`:20C::SEME`, `:20C::RELA`). v2 was to prove a body-parsing settlement
tracker: parse ISO 15022 MT540–548, fold each instruction along an explicit
six-state lifecycle, and expose the unconfirmed/aging/anomalous sets — over the
same MQ-native streaming-audit mechanism v1 established, entirely to the public
standard, zero proprietary content.

**What shipped.** All of that — and then three things the plan did not foresee,
each forced by a measurement:

1. the **v2 body-parsing tracker** (tokenizer → field-spec catalog → parsed
   model → SEME/RELA correlation → monotonic lifecycle fold → window + collector
   → observability), proven live end-to-end;
2. a **load-simulation cluster** (generator, gateway requester app, counterparty
   responder, SVCQM landing queues) that drove real distributed round-trips at
   volume — and produced the scale data that redirected the epic;
3. a **streaming stateful collector** — designed through the full four-stage
   pipeline (brainstorm → pushback → writing-plans → alignment) *inside* the
   epic — that replaced the batch browse-and-fold once #123 measured its O(N)
   wall, then **scale-hardened** (#152: bounded batches + incremental fold) from
   a ~1M-message livelock to live-and-progressive at hundreds of thousands, with
   client-vs-bindings limits characterized (~1,400 vs ~14,300 msg/s, ~10×);
4. a **coverage pipeline tuned to zero exemptions** (#129/#156/#158: 8 omitted
   modules → 0, 100% branch + line) and a **decoupled observability** story
   (collector self-metrics, a state-machine board).

**Work delivered.** One repo (`logical-minds-foundry/mq-gateway-replay-lab`,
private/self-homing). Epic opened **2026-07-29**; work merged through
**2026-08-01**. **43 sub-issues** (41 code/doc tasks closed by PR + the
operational tasks #123/#140/#142 closed on recorded `Outcome:` comments + the two
closing bookends). **~36 epic PRs** (#70–#159). Grouped:

| Phase | PRs | What it delivered |
|---|---|---|
| Spec & deliverable | #70, #110 | Epic spec+plan; the v2 MQ-native settlement-tracking recommendation write-up |
| v2 body-parsing build | #89–#101 | Reference-grounding spike → block-4 tokenizer → field-spec catalog → parsed `SwiftMessage` → SEME/RELA correlation + orphan detection → monotonic lifecycle fold → settlement window + read-only collector → corpus → metrics → responder → multi-day sim → state-machine dashboard → `gwlab swift` CLI + exporter |
| Owned MQ objects | #103, #125 | `gw-sec-queues` (GW.SEC.* streaming audit) + `gw-sec-svc-queues` (SVCQM landing + return QREMOTE) Ansible roles |
| Load simulation | #114, #116, #126–#132 | Load-sim design (+ pushback fold); message generator + directive scheme; gateway requester app + app-side tracker; SVC responder + min-heap scheduler; compression factor C + self-metrics; cross-validation report; IO/queue-depth/scan-cost board panels |
| v2 board & obs | #113 | v2 Grafana settlement board + `gw-sec-observability` role |
| Streaming collector | #134, #143–#150, #153 | Design + plan; `/opt/gwlab` install role + `gwlab-collector` entry point; persistent-cursor `browse_new`; `SettlementState` (incremental fold, retention, orphan-gating); `sweep()` round-robin; snapshot holder + self-metrics; serve loop + HTTP thread + mon-probe deploy; board dedup/enlarge/collector-health |
| Scale hardening | #154, #155 | Bounded browse batches + GIL yield; incremental fold cache in `SettlementState` |
| Coverage to zero | #156, #158 | Retire 5 epic + 3 v1 box-only omits → 100% branch+line via boundary mocking |
| Docs sweep | #159 | Docs-review sweep: streaming collector, scale-hardening, observability |

**Operational tasks (closed on `Outcome:`, not PRs):** #123 (load-sim scale
experiment — the pivotal measurement), #140 / #142 (deploy/validation of the
streaming collector on the live Native-HA queues).

---

## §1 — How the plan evolved

The original plan (at epic-create) was a straight line: build the v2
body-parsing tracker, prove it live once, publish the deliverable. That line
shipped intact — #89–#113 delivered exactly the planned tracker, and #110 was the
planned write-up. The **delta is not in that spine; it is in three deliberate
extensions the plan grew as the problem taught us more.** Each extension was
itself designed before it was built — so the delta is large but the *discipline*
held: the epic grew because the work earned it, not because planning slipped.

**Extension 1 — the load simulation (#114–#132).** The plan said "prove it live."
Proving it live at *toy* scale would have been dishonest for a settlement-tracking
claim: the interesting properties (unconfirmed-set growth, aging, scan cost) only
appear at real volume across a real distributed round-trip. So we built a full
load-sim cluster — a generator with a directive scheme, a gateway requester app,
a counterparty responder on a separate queue manager, and the owned SVCQM landing
queues — and cross-validated the two independent views (app-side tracker vs.
browse-based collector) at **12/12 with zero anomalies**. This was planned-for in
spirit ("prove it") but far larger in execution than a single smoke test.

**Extension 2 — the streaming collector (#134–#150).** This is the epic's hinge.
The load-sim let us *measure* the batch collector, and #123 recorded what we
found: browse-and-fold is **O(N) at ~1,400 msg/s client-mode**, and — the killer
— **97.4% of that time is per-message client browse round-trips**, not compute.
Two conclusions fell out immediately: (a) 76 s to scan 100k already blows a 30 s
Prometheus scrape, and settlement is multi-day (T+2/T+3 → 250k+ retained → ~5 min
per pass), so the batch collector was **not production-ready**; and (b) a
faster *language* would attack the wrong 2.6% — the fix had to be
**architectural**. The plan had no streaming collector in it. Rather than bolt one
on, we ran it through the **full four-stage design pipeline inside the epic**
(brainstorm → pushback → writing-plans → alignment) — the same rigor `epic-create`
front-loads — producing `streaming-collector-{design,plan}.md` and then T1–T8.
The design decisions that survived pushback are load-bearing: single-threaded
round-robin sweep (instruct→status order) so the correlation invariant holds with
no pending buffer; persistent browse cursor; cold-load-once + incremental
maintain; atomic snapshot swap served from a separate HTTP thread; client-mode
first (to keep processing inside the firewall, out of the DMZ) with a documented
path to a QM-native bindings service.

**Extension 3 — scale-hardening and coverage-to-zero (#152, #129/#156/#158).**
Deploying the streaming collector and driving it at *hundreds of thousands*
surfaced a livelock at ~1M messages: `browse_new` returned the whole delta in one
GIL-holding fold, and `report()` re-folded the entire state on every publish —
the board went blank, the HTTP thread starved. This was found **live, not in
review**. #152 fixed it in impact order: **bounded browse batches** (fold a
capped batch, publish a partial snapshot, yield the GIL, repeat — which *also*
made the reload populate the board progressively) and an **incremental fold
cache** (advance each `InstructionState` in place as hops arrive; `report()`
becomes O(Δ)). Separately, the coverage discipline the user pushed to "11" turned
into its own retirement campaign: every whole-module `omit` added during the epic
(and the three v1 omits inherited) was retired to **zero** by mocking the MQ
boundary (injecting a fake `pymqi` surface) rather than exempting the adapter.

The honest summary: **spec → plan captured the destination correctly, but not the
terrain.** The measurement-driven arc (v2 → load-sim → #123's O(N)/97.4%-IO wall →
streaming collector → #152 hardening → coverage-to-11) is the real story, and none
of its middle was in the original plan. That is exactly the delta a retrospective
exists to record — and here it means the epic *taught us something* worth the
extra tasks.

---

## §2 — Lessons learned

- **Measure before optimizing — the single most valuable move of the epic.** The
  97.4%-IO profile (#123) killed the "rewrite it faster" instinct outright and
  pointed straight at architecture. Without that number we might have spent the
  epic micro-optimizing the 2.6% that compute occupies. Profiling *first* is what
  turned a vague "it feels slow" into a specific, correct redesign.

- **Client-mode is the realistic deployment; bindings is the lever — characterize
  both with numbers.** The instinct to "just run it in bindings" is right about
  the ~10× (measured: ~1,400 vs ~14,300 msg/s) but wrong about deployment: there
  are real DMZ/firewall reasons to keep the processing client-side. The value was
  in *quantifying* the escape hatch, not assuming it.

- **Failover reality beat the failover design.** We designed a cold-reload
  recovery path; the live drill showed the client's `MQCNO_RECONNECT_Q_MGR`
  reconnect was **transparent** across a Native-HA switchover — better than the
  design assumed. Test the real thing before hardening against an imagined
  failure mode.

- **100% coverage is achievable — even across the MQ boundary — by mocking the
  boundary, not exempting it.** The "diminishing returns" wall that historically
  justifies `# pragma: no cover` on adapter code dissolves when you inject a fake
  `pymqi` surface and exercise the adapter's real branches. Zero whole-module
  omits is a reachable bar, and now the documented one (`coverage-exceptions-audit.md`).

- **The four-stage design pipeline works *inside* an epic, not just at its
  creation.** Running brainstorm → pushback → writing-plans → alignment on the
  streaming collector — a mid-epic pivot — produced a design whose implementation
  (T1–T8) went essentially smoothly. The rigor is portable to any sufficiently
  hard sub-problem, not reserved for epic kickoff.

- **A single-threaded round-robin with the right sweep order needs no locking.**
  Ingesting instruct-before-status in one thread makes the correlation invariant
  (RTT > 0 → the instruction is always seen before its status) hold structurally,
  so no pending buffer and no concurrency control are needed. Choosing the order
  *was* the concurrency design.

---

## §3 — Compromises & tradeoffs

Load-bearing section — these are the corners knowingly cut and the debt
deliberately taken.

- **The lab memory ceiling was an artificial constraint that caused the
  livelock.** The VM was capped at 1 GB purely as a lab default; at ~1M messages
  the whole-delta fold hit it and the board blanked. We bumped it to 12 GB
  (`virsh setmaxmem/setmem`). That unblocked the run, but the *right* fix was the
  bounded-batch architecture (#152) — the memory bump is a lab convenience, **not
  a production sizing**, and should not be read as one.

- **The big scale runs were direct-injected, not full round-trips.** The
  distributed round-trip (app → NHAUAPP → SVCQM responder → back → audit) was
  proven correct at small scale (12/12), but the counterparty-sim responder is
  single-threaded (~150 msg/s), so a full-day round-trip is a background-time
  operation. For volume, `bigday_load.py` **injects lifecycle messages directly
  into the audit queues** with a realistic outcome mix. This is a documented
  shortcut: it exercises the collector at scale but does *not* exercise the live
  responder at scale.

- **The bindings comparison used `amqsbcg`, not pymqi-bindings.** The ~14,300
  msg/s bindings figure came from IBM's C browse sample on the QM node, because a
  true pymqi-bindings run needed node-side provisioning we didn't stand up. It is
  a faithful *demonstration* of the shared-memory ceiling, not an
  apples-to-apples pymqi client-vs-bindings measurement. The gap is called out
  where the number appears.

- **#152 shipped opt1 + opt2; two further levers are deferred, not built.** The
  #152 design notes list five optimizations. We built the two highest-impact
  (bounded batches, incremental fold cache). **Bindings-mode on the QM node**
  (~10× on reload time) and **serving the snapshot from a separate process**
  (node-exporter textfile pattern, so a fold can *never* block a scrape) are
  documented and logged as the collector's remaining production-readiness work —
  not done here.

- **Response-reconstruction was deferred during the pushback.** An early
  pushback option (reconstruct the full response chain in the collector) was
  explicitly deferred in favor of the simpler incremental-fold model. Correct
  call for this epic, but it means the collector tracks *state*, not a
  fully-replayable *response history*.

- **The SVCQM directive-less pileup was left to expire.** During the live runs an
  `ALTER STREAMQ('')` quoting issue left messages piling on SVCQM; rather than
  fight it mid-experiment we let `CAPEXPRY` age them out and drained the landing
  queue afterward. A cleanup shortcut, harmless in the lab, noted for honesty.

- **The parasite-lab boundary was consciously relaxed — with explicit
  approval.** The repo's charter is "configure only what you own; declare-and-
  verify the rest; never mutate the lab's QM." Two actions crossed that line: the
  VM memory bump and measuring on the QM node. Both were done with the user's
  explicit standing authorization for this session ("this session owns the lab"),
  and reframed as owned-and-reproducible. Worth recording that the boundary *was*
  crossed and *why* it was legitimate here, so a future reader doesn't take it as
  the default.

---

## §4 — New problems & opportunities

- **The collector's remaining production-readiness (#152 levers 3–5).** Logged in
  the #123/#152 record: bindings-mode reload (~10× recovery-time cut),
  separate-process snapshot serving, and incremental latency-percentile caching.
  *Logged, not yet acted on* — candidates for a follow-on hardening task.

- **COA/COD transport-milestone telemetry — the seeded follow-on epic.** The
  natural next layer decomposes `INSTRUCTED → ACKNOWLEDGED` using MQ
  confirmation-on-arrival / confirmation-on-delivery report messages, adding
  transport-level milestones beneath the SWIFT application-level lifecycle. Seeded
  as **epic #105** (`feature/106`), to start after #65 closes. *Spun off.*

- **The streaming collector as a standalone artifact.** It is generic to
  "SWIFT-lifecycle-over-MQ-streaming-audit" and currently lives inside this lab's
  `gwlab` package with an `/opt/gwlab` install-mimic. Extracting it into its own
  installable distribution (the `amqsevt` install pattern taken to its
  logical end) is an opportunity. *Logged, not yet acted on.*

- **ISO 20022 re-pointing.** The tokenizer/catalog/model are built to the public
  MT540–548 field structures; the same fold and correlation logic re-points to
  ISO 20022 `sese.023/024/025` with a different front-end parser. Documented in
  the deliverable's generalization section as a design property. *Logged.*

- **Tooling friction surfaced (candidate Vergil-tooling issues).** Two mechanical
  gaps showed up: (a) operational tasks (#123/#140/#142) recorded
  `Outcome: SUCCESS` but did **not** auto-close, needing manual `vrg-gh issue
  close`; and (b) the `vergil-portforward-3000` socket proxy hit "Too many open
  files" twice (fixed with a `LimitNOFILE=65536` drop-in). Both are worth raising
  upstream. *Logged, not yet filed.*

---

## §5 — What's next

- **Immediate follow-on:** epic **#105 (COA/COD telemetry)**, already seeded on
  `feature/106`, builds transport-milestone tracking on top of this epic's
  body-parsing lifecycle. It is the reason the `INSTRUCTED → ACKNOWLEDGED`
  transition was left as a single hop here.
- **Collector hardening:** pick up #152 levers 3–5 (bindings-mode reload,
  separate-process serving) when the collector moves from client-mode demo toward
  a QM-native service — the security-motivated "inside the firewall" deployment
  the design already points at.
- **Near-term (user-noted):** dashboard tweaks and a presentation/demo build for
  this work — the user flagged it as publishable/conference-grade and intends to
  polish the board and narrative separately.
- **Optional:** extract the streaming collector into its own installable
  distribution; re-point the parser to ISO 20022.

Follow-ons are referenced here, not duplicated — see #105 and the #152 record for
detail.

---

## Appendix A — Operational notes

The epic was deploy- and measurement-heavy; the mechanical sequence, for a future
reader reproducing it:

1. **Owned MQ objects.** Apply `gw-sec-queues` (GW.SEC.* streaming audit) and
   `gw-sec-svc-queues` (SVCQM landing + return QREMOTE) Ansible roles against the
   lab's running Native-HA queue manager. Declare-and-verify the QM, the
   QM-to-QM channels, and the keystores — never mutate them.
2. **Live identity.** The collector and load-sim connect over the app-org mutual-
   TLS client channel (`APP.SVRCONN`, `app-client` cert). The counterparty
   responder reuses the lab's **service-side** identity
   (`/var/mqm/ssl/svc-responder/key`, which trusts both CAs) — the fix for the
   initial `MQRC_SSL_INITIALIZATION_ERROR` was to stop inventing a new PKI and
   reuse the lab's dummy responder's cert.
3. **Streaming collector deploy.** Install the `gwlab` distribution to
   `/opt/gwlab` (install-mimic role), exposing the `gwlab-collector` console
   entry point — no source-tree/PYTHONPATH hack, mirroring a production install.
   Run client-mode on the app-client host; the QM-native bindings service is the
   documented next step.
4. **Failover drill.** `endmqm -s` switchover on the active node; verify the
   client reconnects transparently (`MQCNO_RECONNECT_Q_MGR`) and the collector's
   browse cursor resets and resumes. The scope proven was the **collector's
   reconnection**, not that HA preserves in-flight data.
5. **Scale runs.** `bigday_load.py N CHUNK PAUSE START` injects a paced multi-day
   day directly into the audit queues; the collector folds it in bounded memory
   while serving. Scan-cost curve and IO/compute profile captured at 10k–100k
   (see the #123 outcome record).
6. **Board deploy.** `deploy_board.py` regenerates the code-defined settlement
   board and pushes it to Grafana (`overwrite: true`); 17 panels — operational
   KPI row, state-machine distribution, full-width latency panels, collector-
   health section.

**Key gotchas:** VM memory default (1 GB → 12 GB) caused the pre-#152 livelock;
`vrg-git archive` is disallowed (build wheels via a temp detached worktree);
operational tasks don't auto-close on `Outcome:` (close manually); the port-
forward proxy needs a raised `LimitNOFILE`.

---

## Appendix B — Extended metrics

**Scan-cost curve (client-mode browse-and-fold, O(N), from #123):**

| audit depth | scan time | rate |
|---:|---:|---:|
| 10,000 | 6.79 s | 1,472 msg/s |
| 25,000 | 18.95 s | 1,319 msg/s |
| 50,000 | 35.55 s | 1,406 msg/s |
| 100,000 | 75.72 s | 1,321 msg/s |

**IO vs compute (100k pass):** browse 65.04 s (650 µs/msg, **97.4%**), parse
1.41 s (2.1%), fold 0.35 s (0.5%). Bottleneck = per-message client-mode browse
round-trips; language-irrelevant.

**Client vs bindings:** ~1,400 msg/s (pymqi client) vs ~14,300 msg/s (`amqsbcg`
bindings, 200k in 13.96 s) — **~10×**.

**Correctness:** live distributed round-trip cross-validated **12/12**, zero
anomalies. Crash mid-scan (`kill -9`) then fresh scan → **byte-identical** result
(the stateless browse-and-fold *is* the recovery story).

**Footprint:** `GW.SEC.INSTRUCT.AUDIT` ≈ 98 MB at 100k (~1 KB/msg incl. MQ
overhead); a full replied day (~400k msgs) ≈ ~400 MB.

**Coverage:** 8 whole-module `omit`s → **0**; 100% branch + line; zero
`# pragma: no cover`. See `docs/coverage-exceptions-audit.md`.
