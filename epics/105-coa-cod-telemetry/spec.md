# Epic #105 — COA/COD transport-milestone telemetry: decomposing `instructed→acknowledged` — spec

**Status:** Design approved; pushback + alignment complete; **refit to #65's completed architecture** (streaming collector, requester/responder apps, Grafana/Prometheus board). Ready to execute.
**Epic:** logical-minds-foundry/mq-gateway-replay-lab#105
**Builds on:** epic #65 (Confirmation Tracking v2 — SWIFT MT540–548 settlement tracking), now essentially complete. This epic is a **bolt-on** to #65: it consumes #65's streaming collector, correlation, apps, and board, and adds one browse source plus a small set of derived transport metrics. It does **not** modify #65's state machine.

> **Rendered-label convention.** Metric labels written with a Unicode arrow for readability (`instructed→acknowledged`) render as ASCII, lowercase, in the code: `sent->arrived`, `arrived->delivered`, `delivered->acknowledged` (`observe._latencies`, `observe.py:94`), and as Prometheus series/label values on the Grafana board.

---

## 1. Motivation — decomposing the coarsest lifecycle leg

Epic #65 drives each securities-settlement instruction through six business states (`INSTRUCTED → ACKNOWLEDGED → MATCHED → SETTLED`, with `REJECTED`/`FAILED`) and computes **inter-state latencies** from MQ put-timestamps (#65 spec §11; `observe._latencies`): `instructed->acknowledged`, `acknowledged->matched`, `matched->settled`.

The first leg — `instructed->acknowledged` — is a **single opaque interval**: our put of the instruction to the first MT548 status advice. When it is slow, #65 **cannot say why**: the delay could be in the **network/inter-QM transport**, in the **counterparty's queue** (arrived but unconsumed — their consumer is backed up), or in the **counterparty's application** (consumed promptly but slow to respond). The SWIFT protocol gives no signal that distinguishes them — the first thing it emits is the MT548 ack, by which point all three intervals have already elapsed, invisibly.

MQ's native **report messages** — **COA** (Confirmation On Arrival) and **COD** (Confirmation On Delivery) — expose the two missing intermediate timestamps, splitting the leg into three attributable sub-legs.

### 1.1 Why this is a high-value observability win

Two of these sub-legs answer, at a glance, the two questions that dominate settlement performance triage:

- **`sent->arrived` (delivery latency, from COA) — the headline.** COA gives an aggregate measure of **round-trip network / inter-QM performance**. The ability to **systematically rule the network in or out** is arguably the single biggest win here: the network path between us and the counterparty is *traditionally the first thing blamed* for a slowdown, and this eliminates or confirms it immediately, short-circuiting a large amount of misdirected troubleshooting.
- **`arrived->delivered` (time on queue, COA→COD).** Tells you whether **the counterparty application is backlogged** — arrived on their queue but not yet consumed.

Together these two timestamps localise a performance problem to *network*, *their queue*, or *their app* before any business response exists. That is the epic's core value.

### 1.2 Relationship to the SWIFT application protocol (complementary, not competing)

COA/COD are **transport-layer** events ("arrived on the queue", "removed by an application"); they say nothing about business acceptance, which the SWIFT MT548 qualifiers carry at a higher layer. This epic is **purely additive** to #65 — a lower-layer decomposition of a leg the business layer treats as atomic.

## 2. Thesis and boundaries

- **Transparent by construction — zero queue-manager changes.** COA/COD are request-driven: the application sets report options in the message descriptor and the counterparty queue manager (SVCQM) emits and returns the reports with **no configuration change on either queue manager**. We create only `GW.SEC.REPORT` (a queue we own). This satisfies the **parasite / boundary rule**; the transparency proof is a before/after MQSC config diff (§11).
- **Why the return route is free (store-and-forward).** A report is routed on SVCQM purely by its `ReplyToQMgr` (our QM) to whatever transmission queue our QM name resolves to; the **`ReplyToQ` name (`GW.SEC.REPORT`) is evaluated only on arrival back at *our* QM**, where we create it. Replies already flow back today, so the qmgr-name routing is proven; the counterparty's `GW.SEC.REPLY` object is an artifact of their app, not a precondition for reports.
- **The real end-to-end flow is already delivered by #65 (no longer a #105 prerequisite to build).** #65's **gateway requester app** puts each instruction through the `GW.SEC.INSTRUCT` QALIAS (`gateway_run.put_instructions`, `gateway_run.py:69-82`, put at `:79`) → `.AUDIT` → `STREAMQ`(MUSTDUP) → `.REMOTE` → SVCQM's landing queue (provisioned by `gw-sec-svc-queues`, #125) → the **SVC responder app**'s destructive get (`serve_responder._drain_instructions`, `serve_responder.py:63-69`). COA/COD therefore have a real flow to fire on. #105's only outbound change is to **attach the report options to that existing put**.
- **The application is the only non-transparent surface.** The sole outbound change is setting the report bits on `gateway_run.put_instructions`. The responder already consumes destructively (COD fires with no change), and its **acknowledgement-delay injection already exists** (Directive `delay_vector`, `directive.py:44-46`; min-heap scheduler) — only pickup-delay/withhold and a per-instruction-commit restructure are new (§8.1).
- **`ARRIVED`/`DELIVERED` are transport milestones, not lifecycle `State`s.** #65's six-state reachability graph (`reference.py`) is untouched; the transport metrics are a **parallel path**, never through `observe._latencies`.
- **Scope split with #65.** This epic owns the transport sub-timeline, the report queue, report correlation, the transport deltas, and the transport attention sets. #65 keeps the business-state latencies, aging/rejected/failed sets, and the multi-day window.

### 2.1 Which lifecycle view COA/COD feeds — a design fork #65 introduced

#65 now maintains **two independent lifecycle views** that are cross-validated per SEME (`crossvalidate.cross_validate`, `crossvalidate.py:83-117`):

- **Source #1 — the browse collector** (streaming `SettlementState` fed by `sweep()` over the audit queues), keyed by `SEME`.
- **Source #2 — the app-side tracker** (`gateway_app.track`), the requester folding its *own* sent instructions against drained replies.

**Decision: COA/COD feed source #1 (the browse collector).** The reports arrive on `GW.SEC.REPORT` and are *browsed*, which is source #1's native mode; and source #1 already browses `GW.SEC.INSTRUCT.AUDIT`, whose retained copy carries **both** the instruction's `MsgId` (`Message.msg_id`) **and** its `SEME` (in the body). So source #1 can **self-join**: `report.CorrelId → instruction MsgId → SEME`, attaching the transport milestones to the SEME-keyed instruction with no dependency on the app path. Transport milestones are a **source-#1 enrichment** and are **not part of the cross-validation** in this epic (the two views cross-validate *business* state; transport data exists only on the browse side). This keeps `msg_id` threading confined to the collector path (§6) and off `gateway_app`.

## 3. Mechanism — how the reports are requested and returned

The outbound instruction is a **datagram** (`MQMT_DATAGRAM`). On the existing put in `gateway_run.put_instructions`, when reports are enabled the application sets:

- `MQMD.Report = MQRO_COA | MQRO_COD` — **plain** variants, *not* `_WITH_DATA` (we retain the full body in `GW.SEC.INSTRUCT.AUDIT` and correlate by id, so reports carry only their MQMD; consequently reports are **body-less** and must **not** be run through the SWIFT tokenizer).
- `MQMD.ReplyToQ = GW.SEC.REPORT`, `MQMD.ReplyToQMgr = <our QM>`.
- Default id correlation (`MQRO_COPY_MSG_ID_TO_CORREL_ID`): each report's `CorrelId` is the instruction's `MsgId` (§6).

**Who generates them.** SVCQM generates **COA** when the instruction arrives on its landing queue and **COD** when the responder destructively gets it, routing both back to `GW.SEC.REPORT` over the existing return channel. No object on SVCQM is created or altered. (The streaming-audit topology may also produce a *local* COA — §4.)

**Config-gated.** A `request_reports` flag (default **on** in the lab) governs whether the put sets the report bits — auditable, and toggle-off gives the before/after transparency proof (§11). Mirrored in `fake_messaging` and `pymqi_messaging`.

## 4. The streaming-audit interaction — gating spike (first implementation task)

**How do COA/COD interact with a streaming queue?** Undocumented in our experience and the most interesting unknown. The app puts to the QALIAS `GW.SEC.INSTRUCT`, which resolves to the local retained original `GW.SEC.INSTRUCT.AUDIT` (carrying `STREAMQ … STRMQOS(MUSTDUP)`), which **duplicates** the message to `GW.SEC.INSTRUCT.REMOTE` (QREMOTE → SVCQM). The streamed duplicate **shares the original's `MsgId`**, so any reports it triggers correlate to the same instruction. Resolve **before** the collector work, in two tracks:

- **Track 1 — documentation research.** From public IBM docs, what does COA/COD generation do when the destination queue carries `STREAMQ`/MUSTDUP: a COA on arrival at the local streaming/audit queue? the transmission-queue exclusion (`MQFMT_XMIT_Q_HEADER`) suppressing a COA on the remote leg until it lands on SVCQM? do the `Report`/`ReplyToQ`/`ReplyToQMgr` fields propagate through the duplicate? Cite each; record where the docs are silent.
- **Track 2 — live experiment.** The real flow already exists (§2), so this is now easy: enable `request_reports` for **one** instruction via `gateway_run` and observe exactly what lands on `GW.SEC.REPORT` (count, `Feedback`, generating QM, put-times) with `runmqsc`/`amqsbcg`.

  **Opening hypothesis:** because the streamed copy shares the `MsgId`, expect **a COA per arrival** — one on the local audit queue (`GW.SEC.INSTRUCT.AUDIT`, ~t0) and one on SVCQM's landing queue (t1) — plus one COD. The transmission-queue exclusion may or may not suppress an intermediate COA; the experiment settles it.

- **Source discrimination.** If more than one COA arrives, determine the field that distinguishes each by origin (generating QM / put-time), so the collector maps the **local** COA to a `SENT`-adjacent confirmation and the **SVCQM** COA to `ARRIVED` — keeping every report but interpreting each correctly. If they cannot be distinguished, that is a finding that reshapes the derivation, surfaced not hidden.

**Deliverable:** a short, cited findings note (doc + experiment) recording which reports are generated, by which QM, with what timing and distinguishing fields; whether the hypothesis held; and the chosen mapping from observed reports to `ARRIVED`/`DELIVERED`.

**A negative result is an acceptable terminal outcome.** If COA/COD do not compose usefully with the streaming-audit + remote-QM topology, that well-characterised finding **is** the epic's value; downstream tasks are cut and the retrospective records it. The spike is **not** pass-or-bust.

## 5. Queue topology — a dedicated terminal report ledger

- **`GW.SEC.REPORT`** — a new queue **we own**, the return address for COA/COD reports (confirmed not to exist yet: absent from `gw-sec-queues/defaults` `q_*` list). Kept **out of** the audit queues, which stay pure application traffic.
- **Terminal ledger, no `STREAMQ`.** Nothing forwards the reports; the collector only browses them. So the report queue is *itself* the retained ledger — CAPEXPRY-capped to the **same multi-day horizon** as the SEC audit queues (`gw_sec_capexpry`, from `gw_sec_horizon_days: 3`), because reports are part of reconstructing each instruction's timeline over its settlement life.
- **Provisioned by the role we own** (`gw-sec-queues`), one new local QLOCAL; the QM, SVCQM, and the inter-QM link stay declare-and-verified.

It becomes the collector's **third browse source** (§8).

## 6. Correlation — the instruction is the join (MsgId is net-new to carry)

- Reports carry `CorrelId = instruction MsgId` (`MQRO_COPY_MSG_ID_TO_CORREL_ID`). The MUSTDUP audit copy and the streamed remote copy share the `MsgId`, so SVCQM-generated reports' `CorrelId` maps back to the retained instruction (§4 confirms end-to-end).
- **Two id spaces, one join.** Transport joins on `MsgId ↔ report CorrelId`; business joins on `SEME ↔ RELA` (`reference.CORRELATION_SENDER_REF="SEME"` / `CORRELATION_RELATED_REF="RELA"` on `:20C:`). The retained instruction copy on `GW.SEC.INSTRUCT.AUDIT` carries **both** `MsgId` and `SEME`, so the **browse collector (source #1) self-joins** `report.CorrelId → MsgId → SEME` (§2.1).
- **MsgId is not currently retained (net-new).** `model.SwiftMessage` (`model.py:48-58`) and `lifecycle.InstructionState` (`lifecycle.py:71-84`) carry no `msg_id`; the streaming `SettlementState` keys by `seme` (`stream/state.py:97`); `collect._decode_and_parse` reads `message.msg_id` only for an anomaly label and discards it (`collect.py:92`). So threading `msg_id` is net-new in the **streaming path** (`stream/state.py` entries, reading `Message.msg_id` from `browse_new` in `sweep`) and the batch model path for parity. It does **not** need to reach `gateway_app` (§2.1).

## 7. Transport milestones & metrics

Milestones under `INSTRUCTED` (not `State`s):

| Milestone | Source | Meaning |
|-----------|--------|---------|
| `SENT` | the instruction put-time | our QM accepted and audited the instruction |
| `ARRIVED` | **COA** (SVCQM; §4) | arrived on SVCQM's landing queue |
| `DELIVERED` | **COD** | the responder destructively consumed it |

**Derived legs — endpoint-named, ASCII/lowercase:**

| Leg | Interval | A slow value means | Priority |
|-----|----------|--------------------|----------|
| `sent->arrived` (delivery latency) | `SENT → ARRIVED` | **network / inter-QM transport** — rule the network in/out | **headline (§1.1)** |
| `arrived->delivered` (**time on queue**) | `ARRIVED → DELIVERED` | the counterparty consumer is backed up | primary |
| `delivered->acknowledged` | `DELIVERED → first MT548` | the counterparty application's turnaround | derived |

- Computed on a **parallel path** (not via `_latencies`): new fields on the metrics container and new Prometheus `swift_settlement_*` series (§8). `delivered->acknowledged` joins `delivered` to the folded `acknowledged` timestamp.
- `arrived->delivered` is the per-message residence time = MQ's `TimeOnQueue`, so **time on queue** is the MQ term, not invented. "delivery latency" / "time on queue" are glosses; canonical labels are endpoint-named.
- **No composite "time-to-first-touch"** — the value is in the individual deltas.

**Attention sets (reporting-only, in #65's style),** raising signal *before* any SWIFT response:

- **`sent`-but-no-COA** beyond a threshold — a **delivery problem**.
- **`arrived`-but-no-COD** beyond a threshold — **stuck on the counterparty queue**; the per-message analog of an MQ **Queue Service Interval High** event. The threshold is a configurable **duration** (§8.1 — with the other durations, not on the queue-name set).

### 7.1 Measurement validity

- **Single-clock vs cross-clock.** `SENT` is stamped by **our** QM; `ARRIVED`/`DELIVERED`/first-MT548 by **SVCQM**. So `arrived->delivered` and `delivered->acknowledged` are **single-clock (SVCQM) → skew-free**; `sent->arrived` is the **only cross-clock leg**. We **assume NTP-synchronised queue managers** (stated explicitly), treat a **negative/backwards `sent->arrived` as a `clock-skew` anomaly** (never a silent metric), and **declare-and-verify** the lab's actual time-sync (§11).
- **SENT timestamp vs batched commit.** `gateway_run.put_instructions` currently stamps `put_time` locally per instruction (`gateway_run.py:78`) but commits the whole batch in one UOW (`:81`) — so early-in-batch instructions "sent" before they actually transmit, inflating `sent->arrived`. For accurate delivery latency, either **commit per-instruction** or **stamp SENT at commit**; resolved at plan time.
- **Per-instruction commit on the responder.** For exact `time on queue`, the responder must **get+commit per instruction** — a restructure of the current batch-drain UOW (`serve_responder.py:63-69`, §8.1).
- **Time-compression.** The load-sim compresses time (`Horizon.compression`, `swiftconfig.py:44`). COA/COD are real MQ events stamped in real wall-clock, and `SENT`/first-MT548 are also real put-times, so the legs are internally consistent in **real** time; but injected dwell/ack delays appear at their **compressed** real-time magnitude. Board panels label transport legs as real-time measurements.
- **Tertiary metrics (deferred).** Report *receipt* times on `GW.SEC.REPORT` (COA-generated→received, COD-put→received) are possible future work; out of scope now (§12).

## 8. Architecture — where it lands in `gwlab.swift`

- **Outbound (report options on the existing put).** Add the report request to `gateway_run.put_instructions` (`gateway_run.py:69-82`), gated by `request_reports`. Extend the `Messaging` port so `put` accepts report options and `Message` carries `feedback`, in `fake_messaging` and `pymqi_messaging`; the `feedback` value is read in the `Message(...)` construction sites for `browse_new`/`browse`/`get` (`pymqi_messaging.py:214-221`, `:157-162`, `:138-143`).
- **Collector (third source into the streaming path).** Production ingest is the streaming `sweep()` over `browse_new`, folding into `SettlementState` — **#65 already reserved the COA/COD slot** (`sweep.py:44-46`; streaming-collector design §3/§4). Add `report_audit` (i.e. `GW.SEC.REPORT`) to `sweep._QUEUE_ORDER` and a **report branch to `SettlementState.ingest`** (`stream/state.py:101-134`) that keys the body-less report by `CorrelId → MsgId → SEME` and records `ARRIVED`/`DELIVERED` on the instruction entry. Mirror in the batch `collect.collect` loop (`collect.py:107-116`) + `DayReport` (`collect.py:54-66`) for parity. Reports are never routed through `_decode_and_parse` (they'd be `malformed`).
- **Metrics + exposition.** New transport-leg + attention-set fields on the metrics container (`observe.Metrics`, `observe.py:67-79`); two new anomaly kinds (`orphan-report`, `clock-skew`) added to the closed `_ANOMALY_KINDS` tuple (`observe.py:55`) **and** emitted as Prometheus label values via the streaming exposition (`stream/snapshot.py`, `stream/selfmetrics.py`, `exposition.py`).
- **Board (Grafana, the deployed surface).** The front-of-lifecycle decomposition is new panels on the code-generated Grafana board (`board.py`, `build_settlement_board`, querying `swift_settlement_*`): the three sub-legs + the two attention sets, at the head of the lifecycle view. The legacy HTML `dashboard.py` (`_latency_panel`/`_anomaly_panel`) is updated in parallel for the offline board.
- **Condition injection.** Extend `Directive` (`directive.py`) with `pickup_delay`/`withhold_pickup` alongside the existing `delay_vector` ack-timing, and restructure `serve_responder._drain_instructions` to per-instruction get+commit (§8.1).

### 8.1 Integration cost — reused / resolved vs net-new (verified against current `develop`)

**Delivered by #65 (reuse, no longer #105 work):**
- The live securities put through the QALIAS (`gateway_run.put_instructions`) and the full real chain to SVCQM and back (`gw-sec-queues`, `gw-sec-svc-queues`).
- A destructive-get responder (`serve_responder`).
- Per-instruction **acknowledgement**-delay injection (`Directive.delay_vector` + min-heap scheduler).
- The streaming collector with a **reserved third-source slot** for COA/COD (`sweep.py:44-46`).
- The `GW.SEC.INSTRUCT` QALIAS name (as code/infra constants) and the `SEME`/`RELA` correlation.

**Net-new (built in #105):**
1. **Report options on `gateway_run.put_instructions`** (gated by `request_reports`); `Messaging.put` gains report options; `Message` gains `feedback` (read in `browse_new`/`browse`/`get`).
2. **`GW.SEC.REPORT`** provisioning in `gw-sec-queues` (terminal, CAPEXPRY on the SEC horizon; new `q_report` var).
3. **`msg_id` threading** into the **streaming** path (`stream/state.py` entries + reading `Message.msg_id` in `sweep`) and the batch model for parity — not into `gateway_app` (§2.1).
4. **Report ingest**: a third `sweep._QUEUE_ORDER` entry + a report branch in `SettlementState.ingest` (self-join `CorrelId→MsgId→SEME`); batch-`collect` parity; `DayReport` transport field.
5. **Transport metrics + two attention sets** on the metrics container **and** the Prometheus exposition; **two anomaly kinds** (`orphan-report`, `clock-skew`) in `_ANOMALY_KINDS` **and** as exposition label values.
6. **Grafana board panels** (`board.py`) for the transport decomposition + attention sets; parallel HTML `dashboard.py` update.
7. **Config**: `SwiftQueueSet` gains `instruct` (QALIAS) + `report_ledger`; `request_reports`; `Horizon` gains a `service_interval` duration (`Horizon` now spans `swiftconfig.py:27-45` and already has `compression`).
8. **Condition injection**: `Directive` gains `pickup_delay`/`withhold_pickup`; `serve_responder` UOW restructured to per-instruction get+commit.
9. **SENT-accuracy fix**: per-instruction commit (or commit-time SENT stamp) on `gateway_run.put_instructions` (§7.1).

## 9. Error handling & the no-silent-failures stance

- **Orphan report** — a report whose `CorrelId` matches no known instruction is flagged as `orphan-report` on the anomaly channel (transport analog of #65's orphan `RELA`), never dropped. In the streaming `SettlementState`, a report arriving before its instruction is held (like the existing orphan-gating) and re-joined, not discarded.
- **Missing expected report** — surfaces as an **attention-set** entry (§7), never silently healthy.
- **Loud put failure** — if requesting reports ever fails the put, it fails loudly.
- **Clock-skew** — negative `sent->arrived` → `clock-skew` anomaly, excluded from the leg (§7.1).
- **Local-COA artefact** (pending §4) — if confirmed, discriminated by origin and mapped explicitly, never silently discarded.

## 10. Testing strategy

Test-driven against `fake_messaging`: extend `FakeMessaging` to enqueue **body-less** reports with `feedback` (`MQFB_COA`/`MQFB_COD`), `correl_id = instruction msg_id`, controllable `put_time`, served over `browse_new`. This makes the `CorrelId→MsgId→SEME` self-join, the `SettlementState` report branch, the transport-metric arithmetic, the attention-set thresholds, and the anomaly paths (orphan/clock-skew) provable without a live QM. The report handler is tested to **not** tokenize reports (no spurious `malformed`). Live behaviour is proven by the validation task (§11).

## 11. Validation & proof (closing bookend, issue #108)

Drive a day via `gateway_run` with `request_reports` enabled against the live lab, and confirm:

1. COA and COD arrive on `GW.SEC.REPORT` and correlate to instructions by `MsgId → SEME`;
2. the three transport sub-legs compute and render on the Grafana board;
3. **the QM configurations — ours and SVCQM — are identical before and after** (captured MQSC diff = the transparency proof, the headline acceptance).

Plus a lightweight **time-sync sanity check** (declare-and-verify, not a mutation): confirm what keeps the QM host clocks synced (presumed default host-sync, never yet verified) and whether they are in fact synced, since `sent->arrived` depends on it. A drift finding is loud, not silently corrected.

## 12. Scope — explicitly out

- No change to #65's six-state machine or business-state latencies beyond adding the transport milestones and two attention sets.
- Transport milestones do **not** join #65's cross-validation of the two views (§2.1); COA/COD feed source #1 only.
- No `_WITH_DATA` report variants; no exception/expiry report handling.
- No remediation/replay on a missing report — observability only.
- No queue-manager, SVCQM, or channel configuration changes.
- **Deferred tertiary metrics** — report back-propagation times (COA-generated→received, COD-put→received).

## 13. Success criteria

1. `gateway_run.put_instructions` requests plain COA/COD through the QALIAS (gated by `request_reports`) with correct `ReplyToQ`/`ReplyToQMgr`, and SENT is accurate (per-instruction commit or commit-time stamp).
2. The gating spike (§4) has recorded which reports are generated and by which QM, and resolved the double-COA question (doc + experiment).
3. `GW.SEC.REPORT` exists as a terminal CAPEXPRY ledger; nothing on the shared QM or SVCQM changed.
4. `msg_id` is carried through the streaming path; the report branch self-joins `CorrelId→MsgId→SEME`; `feedback` distinguishes COA/COD.
5. `instructed->acknowledged` is decomposed into `sent->arrived`, `arrived->delivered`, `delivered->acknowledged` on a parallel path, rendered on the Grafana board with `sent->arrived` foregrounded (§1.1).
6. The two transport attention sets surface; the `arrived`-but-no-COD bound is a per-message service-interval-high signal, its threshold a duration.
7. Orphan (`orphan-report`) and `clock-skew` anomalies are flagged (container + exposition), never swallowed.
8. Condition injection (pickup-delay/withhold on `Directive`; per-instruction responder commit) produces demonstrable dwell / stuck-on-queue conditions.
9. The live-run validation captures the before/after config diff proving transparency.

## 14. Sources (public IBM documentation)

- Report field details — MQMD: https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd
- Types of message (report messages): https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message
- Report (MQLONG) reference: https://www.ibm.com/docs/SSFKSJ_9.2.0/com.ibm.mq.ref.dev.doc/q097680_.htm
- Queue service interval events: https://www.ibm.com/docs/en/ibm-mq/8.0.0?topic=events-queue-service-interval
- Queue Service Interval High: https://www.ibm.com/docs/en/SSFKSJ_9.1.0/reference/q050010_.html

## 15. Relationship to #65

This epic presupposes #65's now-complete streaming collector, requester/responder apps, correlation, Grafana board, and `GW.SEC.*` topology, and extends them additively (§8.1): one owned queue, one browse source folded into the reserved slot, three derived metrics on a parallel path, two attention sets, `msg_id`/`feedback` plumbing, report options on the existing put, and pickup/withhold condition injection. It is the transport-layer decomposition of the single leg #65 treats as atomic — and its headline, `sent->arrived`, lets an operator rule the network in or out at a glance.
