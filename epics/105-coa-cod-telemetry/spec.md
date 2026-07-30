# Epic #105 — COA/COD transport-milestone telemetry: decomposing `instructed→acknowledged` — spec

**Status:** Design approved; cross-checked against #65's committed work; ready for pushback
**Epic:** logical-minds-foundry/mq-gateway-replay-lab#105
**Builds on:** epic #65 (Confirmation Tracking v2 — SWIFT MT540–548
settlement-lifecycle tracking). This epic is a **bolt-on** to #65, sequenced
**strictly after** it. It reuses #65's `gwlab.swift` collector, correlation, and
dashboard, and adds one browse source plus a small set of derived transport
metrics. It does **not** modify #65's state machine.

> **Rendered-label convention.** Where this spec writes metric labels with a
> Unicode arrow for readability (`instructed→acknowledged`), the *rendered*
> dashboard/label form follows #65's code — ASCII arrow, lowercase state values:
> `instructed->acknowledged`, `sent->arrived`, `arrived->delivered`,
> `delivered->acknowledged` (`observe._latencies`, `observe.py:94`).

---

## 1. Motivation — decomposing the coarsest lifecycle leg

Epic #65 tracks a securities-settlement instruction through six business states
(`INSTRUCTED → ACKNOWLEDGED → MATCHED → SETTLED`, with `REJECTED`/`FAILED`), and
computes **inter-state latencies** from the MQ put-timestamps (#65 spec §11;
`observe._latencies`): `instructed->acknowledged`, `acknowledged->matched`,
`matched->settled`.

The very first of those legs — `instructed->acknowledged` — is a **single opaque
interval**: the time from our put of the instruction to the first MT548 status
advice coming back. When it is slow, #65 **cannot say why**. The delay could be:

- in the **network / inter-QM transport** (our instruction is slow to reach the
  counterparty's queue), or
- in the **counterparty's queue** (it arrived but is sitting unconsumed — their
  consumer is backed up), or
- in the **counterparty's application** (it was consumed promptly but their app is
  slow to emit the first response).

These are operationally very different problems, and the SWIFT application
protocol gives no signal that distinguishes them — the first thing the SWIFT
layer produces is the MT548 ack itself, by which point all three intervals have
already elapsed, invisibly.

IBM MQ's native **report messages** expose exactly the two missing intermediate
timestamps. This epic uses them to split `instructed->acknowledged` into three
attributable sub-legs, and to raise early diagnostic signals *before* any SWIFT
response exists.

### 1.1 Relationship to the SWIFT application protocol (why this is complementary)

COA/COD are **transport-layer** events: "arrived on the queue" and "removed from
the queue by an application." They say nothing about business acceptance — that is
what the SWIFT MT548 qualifiers (`ACKNOWLEDGED`, `MATCHED`, `REJECTED`, …) carry,
at a higher layer. The two do not overlap or compete: COA/COD measure the
transport of the instruction up to the moment the counterparty application takes
ownership; the SWIFT lifecycle measures everything the application does with it
afterwards. This epic is therefore **purely additive** to #65 — a lower-layer
decomposition of a leg the business layer treats as atomic.

## 2. Thesis and boundaries

- **Transparent by construction — zero queue-manager changes.** COA/COD are an
  original-MQSeries, request-driven feature. The sending application sets report
  options in the message descriptor; provided a valid return route already exists
  (it does — the reply route this repo already depends on), the counterparty queue
  manager emits and returns the reports with **no configuration change on either
  queue manager**. This is the epic's central claim and the reason it satisfies
  the **parasite / boundary rule**: we configure only queues **we** own (one new
  report queue) and change **nothing** on the shared lab queue manager or on
  SVCQM. The proof of transparency *is* the absence of any queue-manager change
  (§11).
- **The application is the only non-transparent surface — and part of it is
  net-new.** Requesting reports means setting report options on the instruction
  put. But #65 has **not built a live securities putter**: its simulation puts
  *directly to `GW.SEC.INSTRUCT.AUDIT`* (`simulation._drive_instruction`,
  `simulation.py:100`), bypassing the `GW.SEC.INSTRUCT` QALIAS, and #65 spec §2 is
  explicit that v2 has "no putter on any live path." So this epic must **introduce
  a securities outbound put through the QALIAS** (or, if #65 lands one first, add
  report options to it). Putting directly to the audit queue would fire COA on the
  wrong object and defeat the streaming-audit spike (§4). The counterparty
  responder already consumes destructively (a real settlement app), so COD fires
  without any change to it; no responder work is in scope. All net-new touchpoints
  are enumerated in §8.1.
- **`ARRIVED`/`DELIVERED` are transport milestones, not lifecycle `State`s.** #65's
  six-state reachability graph and its `State` enum (`reference.py`) are untouched.
  Because the milestones are not `State`s, they **cannot** flow through
  `observe._latencies` (which folds adjacent `State` `Hop` pairs); the transport
  legs are a **parallel metric path** (§7, §8).
- **Scope split with #65.** This epic owns the transport sub-timeline, the report
  queue, report correlation, the transport deltas, and the transport attention
  sets. #65 continues to own the business-state latencies (`acknowledged->matched`,
  `matched->settled`, and the long, multi-day legs) and the aging/rejected/failed
  sets. The multi-day settlement window (T+1/T+2) is #65's concern.

## 3. Mechanism — how the reports are requested and returned

The outbound securities instruction is a **datagram** (`MQMT_DATAGRAM`); v2
abandoned MQ request/reply in favour of body-reference correlation, and COA/COD
work on datagrams. The instruction is **put through the `GW.SEC.INSTRUCT`
QALIAS** (not directly to the audit queue) so it traverses the real
QALIAS→AUDIT→`STREAMQ`(MUSTDUP)→QREMOTE→SVCQM chain and SVCQM sees it arrive on
its landing queue. On the put, the application sets:

- `MQMD.Report = MQRO_COA | MQRO_COD` — **plain** variants, *not* `_WITH_DATA`: we
  already retain the full instruction body in `GW.SEC.INSTRUCT.AUDIT` and correlate
  by id, so the reports need carry only their MQMD. (Consequence: reports are
  **body-less** and must **not** be run through the SWIFT tokenizer — §8.1.)
- `MQMD.ReplyToQ = GW.SEC.REPORT`, `MQMD.ReplyToQMgr = <our QM>` — the return
  address for the reports (a queue we own; §5).
- Default id correlation (`MQRO_COPY_MSG_ID_TO_CORREL_ID`): each report's
  `CorrelId` is set to the instruction's `MsgId` (§6).

**Who generates them.** The **counterparty queue manager (SVCQM)** generates both
reports: **COA** when the instruction arrives on its `GW.SEC.INSTRUCT` landing
queue, **COD** when the responder does a destructive get of it. SVCQM routes the
reports back over the **existing** return channel to `GW.SEC.REPORT`. No object is
created or altered on SVCQM. (The streaming-audit topology may also produce a
*local* COA — see §4.)

**Requesting reports is an explicit, config-gated application decision.** A
`request_reports` flag (default **on** in the lab) governs whether the outbound
path sets the report bits, so the behaviour is auditable and can be toggled off to
demonstrate the before/after — and to produce the transparency proof (§11).
Mirrored in both `fake_messaging` and `pymqi_messaging`.

## 4. The streaming-audit interaction — gating spike (first implementation task)

**How do COA/COD interact with a streaming queue?** This is genuinely
undocumented in our own experience and is the most interesting unknown in the
epic. The outbound topology is **not** a simple put to a remote queue; it is the
streaming-audit pattern: the app puts to the **QALIAS** `GW.SEC.INSTRUCT`, which
resolves to the **local** retained original `GW.SEC.INSTRUCT.AUDIT` — a queue
carrying the `STREAMQ … STRMQOS(MUSTDUP)` attribute — which **duplicates** the
message to `GW.SEC.INSTRUCT.REMOTE` (the QREMOTE that hands the copy to SVCQM).
Report-message behaviour depends on the exact object each copy lands on, and the
streamed duplicate **shares the original's `MsgId`**, so any reports it triggers
correlate to the *same* instruction. This **must be resolved before the collector
is built**, via two tracks:

- **Track 1 — documentation research.** Establish from public IBM documentation
  what COA/COD generation is defined to do when the destination queue carries a
  `STREAMQ` attribute and a MUSTDUP duplicate is produced: is a COA generated for
  arrival on the streaming (audit) queue itself; is the duplicate on the QREMOTE
  suppressed from generating a COA under the transmission-queue exclusion
  (`MQFMT_XMIT_Q_HEADER`) until it lands on SVCQM's local queue; and does the
  duplicate carry the `Report`/`ReplyToQ`/`ReplyToQMgr` descriptor fields onward
  unchanged. Cite what is found; record explicitly where the documentation is
  silent (this interaction may simply be unspecified).
- **Track 2 — live experiment.** Against the real lab topology, enable
  `request_reports` for a single instruction and **observe exactly what lands on
  `GW.SEC.REPORT`** — how many reports, of which `Feedback` type, generated by
  which queue manager, with what put-times.

  **Opening hypothesis (to confirm or refute):** because the streamed copy shares
  the `MsgId`, we expect **a COA for each arrival** — one for the arrival on the
  local streaming/audit queue (`GW.SEC.INSTRUCT.AUDIT`, at ~t0) and one for the
  arrival on the counterparty landing queue (SVCQM `GW.SEC.INSTRUCT`, at t1) — plus
  a single COD from SVCQM on destructive get. The transmission-queue exclusion may
  or may not suppress an intermediate COA on the remote leg; the experiment settles
  it.

- **Source discrimination & resolution.** If more than one COA arrives (as the
  hypothesis predicts), determine the field that distinguishes each by origin
  (generating queue manager / put-time), so the collector maps the **local** COA
  to a `SENT`-adjacent confirmation and the **SVCQM** COA to the real `ARRIVED`
  milestone — keeping every report but interpreting each correctly. If they cannot
  be cleanly distinguished, that is itself a finding that reshapes the metric
  derivation, and is surfaced, not worked around silently.

**Deliverable of the spike:** a short, cited findings note recording — from both
the documentation and the experiment — exactly which reports are generated, by
which queue manager, with what timing and distinguishing fields, against the real
lab topology; whether the opening hypothesis held; and the chosen mapping from
observed reports to the `ARRIVED`/`DELIVERED` milestones. This mirrors #65's
gating-spike-first discipline: no collector code is written against unverified
report behaviour, and a surprising result is a **loud finding**, not a silent
workaround.

## 5. Queue topology — a dedicated terminal report ledger

- **`GW.SEC.REPORT`** — a new queue **we own**, the return address for COA/COD
  reports. Reports are kept **out of** the application-message audit queues
  (`GW.SEC.INSTRUCT.AUDIT`, `GW.SEC.STATUS.AUDIT`), which stay pure application
  traffic (our puts and their SWIFT responses). "Report" is the accurate name for
  these messages.
- **Terminal ledger, no `STREAMQ`.** Unlike the instruct/reply queues — which are
  *live* queues that must stream a **copy** to a separate `.AUDIT` because the
  primary message moves on — the report queue is **terminal**: nothing forwards
  the reports, and the collector only browses them. So the report queue is *itself*
  the retained ledger: **no `STREAMQ`, no separate `.AUDIT` copy**.
- **Same retention as the SEC audit queues.** CAPEXPRY-capped to the **same
  multi-day horizon** as the SEC audit queues (the `gw_sec_capexpry` value, derived
  from `gw_sec_horizon_days: 3`, the T+2 horizon — `gw-sec-queues/defaults`),
  because the reports are part of reconstructing each instruction's timeline over
  its settlement life, not just at send time.
- **Provisioned by the role this repo owns** (extending the `gw-sec-queues`
  pattern), declaring only this one new local queue; the queue manager, SVCQM, and
  the inter-QM link remain declare-and-verified, never mutated.

The collector then browses **three** SEC sources, sequentially (§8): the two
existing audit queues plus **`GW.SEC.REPORT`** — leading-edge, non-destructive;
reports are left in place under CAPEXPRY like the audit queues.

## 6. Correlation — the instruction is the join (and MsgId is net-new to carry)

- Reports carry `CorrelId = original instruction MsgId` (MQ default
  `MQRO_COPY_MSG_ID_TO_CORREL_ID`). The MUSTDUP audit copy and the streamed remote
  copy share the same `MsgId` (the point of MUSTDUP), so SVCQM-generated reports'
  `CorrelId` maps back to the retained instruction. (§4 confirms this end-to-end.)
- **The instruction is the join between two id spaces.** The **transport** timeline
  joins on `MsgId ↔ report CorrelId`; the **business** timeline joins on
  `SEME ↔ RELA` (#65's body-reference correlation, `reference.CORRELATION_SENDER_REF
  = "SEME"` / `CORRELATION_RELATED_REF = "RELA"` on `:20C:`). Both anchor to the
  same instruction.
- **Correction vs the approved design: MsgId is not currently retained.** #65's
  parsed `SwiftMessage` (`model.py`) carries `mt, seme, rela, statuses, put_time,
  …` but **no `msg_id`**; the collector reads the raw MQ `MsgId` only to label a
  malformed-parse anomaly (`collect.py:92`) and then discards it, and
  `InstructionState` (`lifecycle.py`) is keyed by `seme`. So joining reports to
  instructions by id requires **threading a new `msg_id` field** through
  `model.SwiftMessage` → `collect` (the parse path) → `correlate` →
  `lifecycle.InstructionState`. This is net-new work (§8.1), not a reuse of
  something #65 already has.

## 7. Transport milestones & metrics

Milestones under `INSTRUCTED` (the state machine is unchanged; these are **not**
`State`s):

| Milestone | Source | Meaning |
|-----------|--------|---------|
| `SENT` | put commit (already captured as the instruction `put_time`) | our QM accepted and audited the instruction |
| `ARRIVED` | **COA** (the SVCQM one; §4) | instruction arrived on SVCQM's `GW.SEC.INSTRUCT` landing queue |
| `DELIVERED` | **COD** | the responder destructively consumed it |

**Derived legs — endpoint-named, rendered ASCII/lowercase (`sent->arrived`, …):**

| Leg | Interval | A slow value indicates |
|-----|----------|------------------------|
| `sent->arrived` (delivery latency) | `SENT → ARRIVED` | network / inter-QM transport |
| `arrived->delivered` (**time on queue**) | `ARRIVED → DELIVERED` | the counterparty consumer is backed up |
| `delivered->acknowledged` | `DELIVERED → first MT548` | the counterparty application's turnaround |

- Because the milestones are not `State`s, these legs are computed on a **parallel
  metric path**, not through `observe._latencies`. They land as **new fields on
  `observe.Metrics`** (alongside the existing `latencies`), and — for the
  `delivered->acknowledged` seam — join the transport put-times to the existing
  `acknowledged` `Hop.at`.
- `arrived->delivered` is the **per-message residence time** on the counterparty
  queue — precisely MQ's `TimeOnQueue` statistic, so **time on queue** is the MQ
  term for it, not an invented one. `delivery latency` / `time on queue` are
  descriptive glosses; the canonical rendered labels remain endpoint-named
  (`sent->arrived`, `arrived->delivered`).
- **No composite "time-to-first-touch."** It would merely be the sum of the first
  two legs; both are expected to be small, and meaningful signal in them flags a
  *delivery/servicing* slowdown rather than business processing. The value is in
  the **individual** deltas.
- `delivered->acknowledged` is retained as a **derived** value bridging transport
  into the first business state, not a separately maintained metric.

**Attention sets (reporting-only, in #65's style — named, no action taken),** which
raise signal *before* any SWIFT response exists:

- **`sent`-but-no-COA** beyond a threshold — a **delivery problem**.
- **`arrived`-but-no-COD** beyond a threshold — **stuck on the counterparty
  queue**; the per-message analog of an MQ **Queue Service Interval High** event.
  The threshold is a configurable **duration** (§8.1 — it belongs with the other
  durations, not on the queue-name set).

## 8. Architecture — where it lands in `gwlab.swift`

- **Outbound (net-new putter + report request).** Introduce a securities outbound
  put through the `GW.SEC.INSTRUCT` QALIAS (see §8.1), gated by `request_reports`.
  The `Messaging` abstraction gains the ability to set
  `Report`/`ReplyToQ`/`ReplyToQMgr` on a put, in both `fake_messaging` and
  `pymqi_messaging`.
- **Collector (third source, distinct path).** #65's collector is a **sequential
  browse loop** over `((instruct_audit, instructions), (status_audit, responses))`
  (`collect.py:107-116`). This epic adds a **third entry** for `GW.SEC.REPORT` —
  but with a **distinct MQMD-only handler**, because reports are body-less and
  would fail the SWIFT tokenizer as `malformed`. The report handler keys on
  `CorrelId` / `Feedback` / `put_time`, folds each report into the matching
  instruction record, and records `ARRIVED`/`DELIVERED`. `DayReport`
  (`collect.py:54-66`) gains a transport field carrying these.
- **`Messaging.Message` gains `feedback`.** `Message` (`messaging.py`) is
  `body, msg_id, correl_id, put_time` today — with **no `Feedback`**, so COA and
  COD are indistinguishable. Add a `feedback` field (unset for application
  messages; `MQFB_COA`/`MQFB_COD` for reports), set by `pymqi_messaging` from the
  MQMD and by `FakeMessaging` in tests.
- **Dashboard (front-of-lifecycle decomposition — new rendering).** #65's latency
  panel (`dashboard._latency_panel`) auto-renders one row per `LatencyStats`; the
  `instructed->acknowledged` "bar" is just one such row, with no object to
  decompose in place. So the decomposition is **new rendering**: a transport panel
  (or sub-rows) for the three sub-legs and the two transport attention sets,
  positioned at the head of the lifecycle board, drawn from the new `Metrics`
  fields.

### 8.1 Integration cost — net-new work vs reuse

Enumerated so the plan is honest (each verified against #65's committed code):

**Net-new (must be built):**
1. **Securities outbound putter through the QALIAS** — #65 has none; its sim puts
   direct-to-audit (`simulation.py:100`). Add the putter and a **`instruct` QALIAS
   field** (`GW.SEC.INSTRUCT`) on `SwiftQueueSet` (`swiftconfig.py`, currently only
   `instruct_audit`/`status_audit`), and wire the simulation to put through it so
   streaming/COA behaviour is exercised.
2. **`msg_id` propagation** through `model.SwiftMessage` → `collect` → `correlate`
   → `lifecycle.InstructionState` (§6) — today MsgId is discarded after anomaly
   labelling.
3. **`feedback` field** on `messaging.Message` + `FakeMessaging` + `pymqi_messaging`
   (COA vs COD).
4. **Report queue** `GW.SEC.REPORT` provisioning in the `gw-sec-queues` role
   (terminal, CAPEXPRY on the SEC horizon).
5. **Report browse + MQMD-only handler** as a third collector source with its own
   parse path (not `_decode_and_parse`).
6. **Config**: `report_ledger` (queue name) + `instruct` (QALIAS) on
   `SwiftQueueSet`; `request_reports` gate; the **service-interval threshold as a
   duration** on `Horizon` (`swiftconfig.py:27-36`) or a new config object — *not*
   on the queue-name `SwiftQueueSet` (a category mismatch).
7. **Transport metrics + two attention sets** as new `observe.Metrics` fields on a
   parallel path (not through `_latencies`); new dashboard rendering.
8. **`orphan-report` anomaly kind** — `_ANOMALY_KINDS` (`observe.py:55`) is a
   **closed 4-tuple** iterated by the dashboard (`dashboard._anomaly_panel`);
   adding the kind means editing that set and the panel, not a free-form add.

**Reused unchanged:** the SEC queue topology and CAPEXPRY horizon; the
`SEME`/`RELA` body correlation; the MQ put-time as the latency time source; the
unified anomaly channel and attention-set *mechanism* (`collect.Anomaly`,
`observe.Metrics.aging/rejected/failed`); the sequential browse model; the
`gw-sec-queues` provisioning pattern.

## 9. Error handling & the no-silent-failures stance

Consistent with the project rule and #65's unified anomaly channel:

- **Orphan report** — a report whose `CorrelId` matches no known instruction is
  flagged as a new `orphan-report` kind on the collector's anomaly channel (the
  transport analog of #65's orphan `RELA`), never dropped. (Requires extending the
  closed `_ANOMALY_KINDS` tuple and the anomaly panel — §8.1.)
- **Missing expected report** — surfaces as an **attention-set** entry (§7), never
  silently treated as healthy.
- **Loud put failure** — if requesting reports ever causes the instruction put to
  fail, it fails loudly; no swallowed exception, no report-less silent send.
- **Local-COA artefact** (pending §4) — if the spike confirms a local COA, the
  collector discriminates it by origin and maps it explicitly (to a `SENT`-adjacent
  confirmation), never by silently discarding reports.

## 10. Testing strategy

Test-driven against the `fake_messaging` harness (as in #65): `FakeMessaging` is
extended to enqueue **body-less** report messages with `feedback` set
(`MQFB_COA`/`MQFB_COD`), `correl_id = the instruction msg_id`, and controllable
`put_time`. This makes correlation, the report fold, the transport-metric
arithmetic, the attention-set thresholds, and the dashboard decomposition all
provable without a live queue manager. The report handler is tested to **not**
route reports through the SWIFT tokenizer (no spurious `malformed` anomalies), and
orphan / missing-report paths are exercised by fixtures. Live behaviour is proven
by the validation task (§11).

## 11. Validation & proof (closing bookend)

A **live-run validation** task (issue #108): drive a day's instructions with
`request_reports` enabled against the live lab, and confirm:

1. COA and COD arrive on `GW.SEC.REPORT` and correlate to their instructions by
   `MsgId`;
2. the three transport sub-legs compute and render on the board;
3. **the queue-manager configurations — ours and SVCQM — are identical before and
   after** (a captured MQSC/config diff showing no change). This diff **is** the
   transparency proof and the headline acceptance criterion.

## 12. Scope — explicitly out

- No change to #65's six-state machine, business-state latencies, or attention
  sets beyond adding the transport milestones and the two transport attention
  sets.
- No responder change (it already consumes destructively).
- No `_WITH_DATA` report variants; no exception/expiry report handling (COA/COD
  only).
- No remediation/replay action on a missing report — reporting/observability only,
  matching #65's boundary.
- No queue-manager, SVCQM, or channel configuration changes (the entire point).

## 13. Success criteria

1. A securities outbound put exists through the `GW.SEC.INSTRUCT` QALIAS and
   requests plain COA/COD (gated by `request_reports`) with correct
   `ReplyToQ`/`ReplyToQMgr`; the simulation drives it through the QALIAS.
2. The gating spike (§4) has recorded, against the real topology, exactly which
   reports are generated and by which queue manager, and resolved the double-COA
   question (documentation + experiment).
3. `GW.SEC.REPORT` exists as a terminal CAPEXPRY ledger on the SEC horizon,
   provisioned by the role this repo owns; nothing on the shared QM or SVCQM
   changed.
4. `msg_id` is carried through the model and `feedback` distinguishes COA/COD; the
   collector browses the report queue as a third source with an MQMD-only handler
   and folds reports into each instruction by `CorrelId`.
5. `instructed->acknowledged` is decomposed into `sent->arrived`,
   `arrived->delivered` (time on queue), and `delivered->acknowledged`, on a
   parallel metric path, rendered at the front of the lifecycle board.
6. The two transport attention sets surface, with the `arrived`-but-no-COD bound
   framed as a per-message service-interval-high signal, its threshold configured
   as a duration.
7. Orphan (`orphan-report`) and missing reports are flagged, never swallowed.
8. The live-run validation captures the before/after config diff proving
   transparency.

## 14. Sources (public IBM documentation)

- Report field details — MQMD:
  https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd
- Types of message (report messages):
  https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message
- Report (MQLONG) reference:
  https://www.ibm.com/docs/SSFKSJ_9.2.0/com.ibm.mq.ref.dev.doc/q097680_.htm
- Queue service interval events:
  https://www.ibm.com/docs/en/ibm-mq/8.0.0?topic=events-queue-service-interval
- Queue Service Interval High:
  https://www.ibm.com/docs/en/SSFKSJ_9.1.0/reference/q050010_.html

## 15. Relationship to #65

This epic presupposes #65's `gwlab.swift` collector, correlation, dashboard, and
`GW.SEC.*` topology, and extends them additively (the exact reuse-vs-net-new split
is §8.1): one new owned queue, one new browse source with its own handler, three
new derived metrics on a parallel path, two new attention sets, `msg_id`/`feedback`
plumbing, and a gated securities outbound put. It is the transport-layer
decomposition of the single leg #65 treats as atomic — valuable precisely when
that leg is slow and someone needs to know whether to call the network team, the
counterparty's operations, or the counterparty's application owners.
