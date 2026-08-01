# Tracking securities-settlement state from the message body with MQ-native audit queues

*A recommendation for the SWIFT MT540–548 settlement-lifecycle problem — the
body-parsing companion to the request/reply confirmation-tracking recommendation.*

---

## 1. Summary

A back-office gateway that exchanges **SWIFT securities-settlement** messages over
IBM MQ — instructions (MT540–543) out, status advices (MT548) and settlement
confirmations (MT544–547) back — needs to answer, at any moment, **what state
every instruction is in**: acknowledged, matched, settled, rejected, or failing.
That is not a "sent vs confirmed" question. A single instruction is followed,
over a **multi-day** settlement cycle, by a *stream* of messages that move it
through a lifecycle, and the messages **do not correlate through the MQ headers**
the way a symmetric request/reply flow does — the linkage lives **inside the
message body**.

This document recommends deriving that lifecycle the way the queue manager
already lets you keep the record — an **MQ-native audit copy** of every
instruction and every response, written atomically by the queue manager under the
same unit of work as the live traffic — and then, over those audit queues,
**parsing the message body** to (a) correlate each response to its instruction by
reference and (b) fold the response stream through an explicit, documented
settlement state machine. The result answers, authoritatively, *which instruction
is in which state, and for how long* — with the named attention sets (aging,
rejected, failing) and the latency between lifecycle milestones — without a
hand-written log, a new product, or any change to the messages.

It is a **parallel, independent** design to the request/reply recommendation: that
one correlates on the MQ message descriptor (the request identifier echoed as the
reply correlation identifier); this one correlates on **body content** because
securities settlement demands it. The streaming-queue audit mechanism is shared;
everything above it is new.

The recommendation is **built and proven live**. Every mechanism and algorithm
below was implemented as a working prototype and validated against a **real
Native-HA queue manager** over a mutual-TLS channel; the figures in §10 are
measured output from that run, not an illustration. The collector is a **streaming,
stateful** service that reads each message once and serves a snapshot decoupled from
the fold (§9); it was **scale-hardened to hundreds of thousands of instructions**
with the dashboard staying live throughout, its **client-vs-bindings** performance
and its **Native-HA failover** recovery both measured (§11), and its settlement
picture plus its own health surfaced on a Grafana board (§12).

## 2. The problem: settlement state is multi-valued, multi-day, and in the body

Three properties make this different from a request/reply confirmation:

- **State is not binary.** An instruction is acknowledged, then matched, then
  settled — or it is rejected at validation, or it fails to settle. "Did a
  confirmation come back?" cannot express that. Operations needs the *state*, the
  *set* of instructions stuck short of settlement, and *how long* each has waited.
- **The lifecycle spans days.** Securities settle on T+1/T+2, and a failing
  instruction can roll to a later day. A record scoped to "today" — or capped to a
  rolling 24 hours — will drop an instruction before its settlement confirmation
  arrives, and then mis-count that late confirmation as unexpected. The window
  must follow the **settlement cycle**, not the calendar day.
- **Correlation is in the body.** These messages carry their own **sender's
  reference** and each response carries a **related reference** pointing back at
  the instruction. That linkage is a field *inside the message*, not an MQ header,
  so the record cannot be built by reading descriptors alone — the body must be
  parsed.

## 3. The core insight: keep the MQ-native record, then read the body

Two moves, in order.

**Keep the record the queue manager's way.** IBM MQ can duplicate a queue's
traffic for you — a **streaming queue** with must-duplicate quality of service —
so a copy is retained for audit while the live message flows on, under the **same
unit of work**, with no application logic. Two audit queues result: the retained
**instruction audit** (everything we sent) and the streamed **response audit**
(every status advice and confirmation received). This is the same proven
mechanism used for the request/reply flow; it is transport-agnostic to what is
*inside* the message.

**Then read the body.** Over those audit queues a read-only collector parses each
message's ISO 15022 block-4 structure, links each response to its instruction by
reference, and folds the response stream through a documented state machine. The
collector never touches the live path, so it never competes with throughput; the
audit queues *are* the state, as durable and replicated as the messaging itself.

## 4. The blueprint

### 4.1 The MQ objects

The streaming remote model, with a **settlement-cycle** retention window. Names
are illustrative; substitute your own convention.

```mqsc
* Outbound: the app puts to an alias; the original is retained as the instruction
* audit; a must-duplicate copy is streamed onward to the counterparty over the
* existing transmission queue + channel. CAPEXPRY caps the retained audit to the
* SETTLEMENT WINDOW (a few business days), NOT a rolling 24h — so a late (T+2)
* confirmation still finds its instruction in the audit.
DEFINE QALIAS('GW.SEC.INSTRUCT') TARGET('GW.SEC.INSTRUCT.AUDIT')
DEFINE QLOCAL('GW.SEC.INSTRUCT.AUDIT') DEFPSIST(YES) SHARE MAXDEPTH(999999999) CAPEXPRY(2592000)
ALTER  QLOCAL('GW.SEC.INSTRUCT.AUDIT') STREAMQ('GW.SEC.INSTRUCT.REMOTE') STRMQOS(MUSTDUP)
DEFINE QREMOTE('GW.SEC.INSTRUCT.REMOTE') RNAME('<counterparty landing>') +
       RQMNAME('<counterparty qmgr>') XMITQ('<existing xmitq>')

* Inbound: responses arrive on the reply queue and stream a must-duplicate copy to
* the response audit, likewise capped to the settlement window.
DEFINE QLOCAL('GW.SEC.REPLY')       DEFPSIST(YES) SHARE MAXDEPTH(999999999)
DEFINE QLOCAL('GW.SEC.STATUS.AUDIT') DEFPSIST(YES) SHARE MAXDEPTH(999999999) CAPEXPRY(2592000)
ALTER  QLOCAL('GW.SEC.REPLY') STREAMQ('GW.SEC.STATUS.AUDIT') STRMQOS(MUSTDUP)
```

`CAPEXPRY(2592000)` is three business days in tenths of a second; size it to your
settlement horizon plus a fails buffer. The transmission queue and channel to the
counterparty already exist — declare-and-verify them, never create them.

### 4.2 The settlement state machine

Six states — four progressive, two terminal-negative:

```
INSTRUCTED ─▶ ACKNOWLEDGED ─▶ MATCHED ─▶ SETTLED        (lifecycle complete, positive)
     │             │             │
     └─────────────┴─────────────┴─▶ REJECTED / FAILED   (lifecycle complete, negative)
                                        FAILED ─▶ SETTLED (recovery within the window)
```

Every intermediate transition is driven by an **explicitly documented** MT548
status qualifier; the terminal-positive is driven by the arrival of a confirmation
message. There is **no MT548 "settled" status code** — settlement is signalled by
the MT544–547 confirmation itself.

| Trigger (parsed from the body) | → State |
|---|---|
| MT540–543 instruction present in the instruction audit | INSTRUCTED |
| MT548 processing status *accepted* (`:25D::IPRC//PACK`) | ACKNOWLEDGED |
| MT548 matching status *matched* (`:25D::MTCH//MACH`) | MATCHED |
| MT544 / 545 / 546 / 547 confirmation arrives | SETTLED |
| MT548 processing status *rejected* (`:25D::IPRC//REJT`) | REJECTED |
| MT548 settlement status *failing* (`:25D::SETT//PENF`) | FAILED |

The status codes are taken from public ISO 15022 field renderings and
cross-checked against the free ISO 20022 `sese.024` status structure; see the
grounded, cited reference in `docs/swift-settlement-reference.md`. Documented
codes that do not advance the machine (for example settlement *pending*) are
recognised and ignored, not flagged.

### 4.3 Correlation — reference in the body

Each instruction carries its own **sender's message reference**, field
`:20C::SEME//`. Each response carries, in its linkages, a **related reference**,
field `:20C::RELA//`, whose value **is the instruction's `SEME`**. The collector
indexes instructions by `SEME` and groups responses by `RELA`. A response whose
`RELA` matches no instruction is an **orphan** — flagged, never folded. This is
the body-level analog of the request/reply echo, and it is what makes each match
authoritative.

## 5. Algorithm 1 — parse and validate the body

Tokenise the ISO 15022 block 4 into its `:16R:`/`:16S:` sequences and
`:<tag>::<qualifier>//<value>` fields (the message type is read from the SWIFT
Block 2 application header, not the body). Validate each field we consume against
a **field catalog** — format, character set, and, for status fields, the
enumerated code domain — sourced from the public renderings. A field outside its
documented format or a qualifier outside its domain is an anomaly (§8), surfaced,
never guessed at. First-order the messages are assumed valid; the validation path
is designed in and exercised by deliberately malformed fixtures.

## 6. Algorithm 2 — correlate and fold the lifecycle

1. Browse the **instruction audit** and index the instructions by `SEME`.
2. Browse the **response audit** and group responses under their instruction by
   `RELA`, ordered by the queue manager's put-timestamp.
3. For each instruction, **fold** its response stream from INSTRUCTED, advancing
   **monotonically** along the state machine. A single MT548 may carry several
   status qualifiers; each is applied in documented precedence, so one message can
   advance more than one hop. A re-sent status already reached is a **no-op**, not
   an anomaly. A status implying a *backward* or *unreachable* transition is
   flagged (§8), never silently applied. A failing instruction may still recover
   to SETTLED within the window.
4. The put-timestamp of each accepted transition gives the **latency** between
   lifecycle milestones — instructed→acknowledged, acknowledged→matched,
   matched→settled — read directly from the message descriptors, no timers.

## 7. Algorithm 3 — scope by the settlement window, name the attention sets

Because the lifecycle spans days, scoping is by the **open settlement window**, not
the calendar day. An instruction is in scope until it reaches a terminal state or
ages out of the horizon. The collector then produces:

- the **state distribution** — how many instructions in each of the six states;
- the **attention sets**, each an instruction named by its `SEME` with its age:
  the **in-flight-and-aging** set (chase candidates), the **rejected** set, and
  the **failing** set;
- the **aged-out** set — non-terminal instructions at the edge of the retention
  window, the ones most at risk;
- the lifecycle **latencies** and their degradation over the window.

Because retention covers the settlement horizon, a confirmation arriving on T+2
still finds its T instruction in the audit and folds correctly — the failure mode
a 24-hour window would cause (a real confirmation mis-flagged as an orphan) does
not occur.

## 8. Anomalies — flagged, never swallowed, never a crashed run

The collector is a **graceful-degradation boundary**. A message that will not
tokenise, a field violating its catalog, a qualifier outside its domain, a
backward/unreachable transition, or an orphan `RELA` — each becomes a flagged
anomaly on a single unified channel, with the offending reference recorded, and
the run **continues**. In a healthy flow this signal is zero; a non-zero value is
a reason to look, not a dropped message and not an aborted pass.

## 9. The collector: streaming, stateful, decoupled

The simplest form is a small, read-only collector that, on each pass, browses the
two audit queues, runs Algorithms 1–3, and publishes the state distribution, the
named attention sets, the lifecycle latencies, and the anomalies. That batch form
is correct and is where the prototype started — but it re-reads the *entire*
retained window on every pass, and at production volume that does not scale (§11).

The production form is a **streaming, stateful collector** that reads each message
once. It **cold-loads** the retained window at startup — one full browse-and-fold —
then **maintains the settlement picture incrementally**: on each pass it browses
only the *new* messages since the last (a persistent MQI browse cursor:
`BROWSE_FIRST` once, then `BROWSE_NEXT`) and folds them into an in-memory
per-instruction state. After each pass it publishes a pre-computed **snapshot**;
the metrics endpoint serves that snapshot in milliseconds, so **the scrape is
decoupled from the fold** — a monitoring poll never triggers the expensive work,
however large the window. The single-threaded ingest loop is deliberate: measured
steady-state volume is a few messages per second, so parallelism buys nothing it
must pay for in complexity (§11).

Crucially, the **statelessness property is preserved**: the collector persists
nothing — the audit queues *are* the durable state — so a crash or a queue-manager
failover is recovered by rebuilding in memory from a fresh cold-load. Recovery time
*is* the cold-load time, which the collector publishes as a first-class metric.

**Where it runs is a deliberate, security-aware choice.** By default it runs as a
**client**, off-cluster, so the settlement processing can sit inside the firewall,
out of the DMZ, away from the queue manager — the realistic enterprise placement.
As a lever for when latency demands it and policy allows, the same collector can
run **local to the queue manager in bindings mode**, trading placement for an
order-of-magnitude faster browse (§11). The code is identical; only the connection
and deployment differ.

The data-driven core — the field catalog, the state-transition table, and the
correlation rule — *is* the documentation, and it is what a re-implementer elsewhere
reproduces; the streaming/deployment machinery is straightforward once those tables
and the audit queues are in place.

## 10. Evidence: a live settlement run

A realistic settlement batch was driven **live** against a real **Native-HA**
queue manager, over a **mutual-TLS** application channel, through the `GW.SEC.*`
streaming audit queues above — SWIFT bodies put through the live queues, the queue
manager streaming the audit copies, and the collector folding them off the live
audit queues. Twelve instructions: seven settling cleanly, one rejected at
validation, one failing to settle, one acknowledged and in-flight, two aging with
no response yet, plus one injected orphan response.

Read straight off the live audit queues:

| Signal | Value |
|---|---|
| Instructions | 12 |
| Messages scanned (instructions + responses) | 39 |
| **State distribution** | **SETTLED 7 · ACKNOWLEDGED 1 · INSTRUCTED 2 · REJECTED 1 · FAILED 1** |
| Rejected set | 1 — named by its reference |
| Failing set | 1 — named by its reference |
| Aging (in-flight) set | 3 — named, with age |
| **Anomalies** | **1 orphan** — the injected response whose reference matched no instruction; malformed / field-violation / transition all 0 |
| Lifecycle latency: instructed→acknowledged | 3.0 s (from the put-timestamps) |
| Lifecycle latency: acknowledged→matched | 4.0 s |
| Lifecycle latency: matched→settled | 3.0 s |

The distribution and the attention sets match the driven scenario exactly, the
lifecycle latencies were computed from the queue manager's own put-timestamps, and
the injected orphan was caught as an anomaly rather than folded. The board
rendered from this live collection is included alongside this document
(`docs/images/settlement-live-board.html`).

The parasite boundary held throughout: only the `GW.SEC.*` objects were created;
the queue manager, the existing transmission queue and channel to the
counterparty, and everything else were declare-and-verified, never mutated.

## 11. Scale, performance, and recovery

Every scaling decision here was forced by a **measurement**, not a hunch — the
figures below are all from live runs against the Native-HA queue manager.

**The wall the batch form hits.** A full browse-and-fold pass is **O(N)** in the
*total retained* message count, and settlement is multi-day, so the retained window
is large. Measured, the batch pass runs at **~1,400 msg/s**: 6.8 s at 10k, 19 s at
25k, 36 s at 50k, **76 s at 100k** — already past a 30 s monitoring scrape, and a
fully-replied multi-day window (~400k messages) is ~5 minutes. Profiling one pass
is decisive: **97.4 % of the time is IO** — per-message browse round-trips — and
only **2.6 % is compute** (parse + fold). Two conclusions follow and shaped the
whole design: the bottleneck is **re-reading messages**, not computing metrics; and
**the language is irrelevant** — a rewrite in a faster language would optimise the
2.6 %. The answer is architectural: read each message once (§9).

**Hardening the streaming form.** At hundreds of thousands of messages, two more
costs surfaced — each fixed without a redesign:

- *Whole-delta browse.* Returning the entire new delta as one list spikes memory and
  produces one long fold that starves the metrics endpoint. Fixed with **bounded
  browse batches** (a cap per pass) that **publish a partial snapshot between
  batches**, plus a cooperative yield so the browse (which releases the interpreter
  lock during IO) keeps the endpoint responsive.
- *Re-fold per publish.* Re-folding the entire in-memory state on every publish is
  O(state). Fixed with an **incremental fold cache** — each instruction's folded
  state is advanced in place as messages arrive, so publishing is O(Δ), verified
  byte-identical to the batch fold.

With both in place the collector **stays live and populates the board
progressively** through a hundreds-of-thousands cold-load, at **bounded memory** —
the case that previously blanked the dashboard now fills it in front of you.

**Client vs. bindings — the placement lever, quantified.** The client-mode cost is
almost entirely the TCP/TLS **round-trip per browse** (~650 µs/message). Running
the collector **local to the queue manager in bindings mode** (shared-memory IPC)
was measured at **~14,300 msg/s vs. ~1,400 msg/s client — ~10×** — which collapses a
351 s client cold-reload to **~35 s**. So the security-driven default (client,
inside the firewall) and the performance option (bindings, on the queue manager) are
now both characterised with a number; you choose placement knowing the exact trade.

**Recovery.** Because the collector persists nothing, recovery is a rebuild from the
queues. Against a live **Native-HA failover**, the client reconnected **transparently
with no metrics gap** — better than the designed crash-then-reload — and maintained
**exactly-once** ingestion across the reconnect (no re-read, no double-count). The
cold-reload path is the safety net; the common case is seamless. The retained window
is bounded by `CAPEXPRY`, so both queue depth and rebuild cost stay bounded as volume
grows.

**Correctness under stress.** The collector is stateless by construction — a
`kill -9` mid-scan and a restart re-derive a **byte-identical** result — and the
whole pipeline is covered to **100 % branch and line** with zero exemptions (the
box-only pieces are unit-tested by mocking the MQ/socket boundary, not skipped).

## 12. Observability — the settlement picture, and the collector watching itself

The published snapshot drives a Grafana board (code-generated, deployed by this
repo's own automation, scraped by Prometheus). It shows two layers:

- **The settlement picture.** The state-distribution hero (the funnel from
  instructed to settled, with the two terminal negatives), the named attention sets
  — **aging / replay candidates**, **rejected**, **failing** — the per-hop
  **lifecycle latencies** (mean and p95, from the queue manager's own
  put-timestamps), and the **anomalies by kind**. Series are labelled by queue
  manager, so a failover is a continuous line on the board, not a gap.
- **The collector watching itself.** A self-metrics section makes the recovery and
  liveness story visible: **recovery time** (the cold-reload duration — the number
  operations cares about after a restart or failover), the **incremental fold cost**
  off the scrape path, the **ingest rate**, the **sweep backlog** (is the loop
  keeping up?), and the **snapshot publish-age** — so a stalled collector shows up
  as *staleness*, not silence. This is unusually complete for a component of this
  kind: the watcher is itself observed.

## 13. Beyond MT540–548 — the deferred generalization

This recommendation is built to the **public ISO 15022 MT540–548** rendering. The
ISO 20022 successors — `sese.023` (instruction), `sese.024` (status advice),
`sese.025` (confirmation) — express the *same* settlement lifecycle in XML with
free, published schemas; the state machine, the correlation model, and the
observability carry over unchanged, with only the parser and the field catalog
re-pointed at the XML. That mapping is documented but deliberately **not** built
here. The design is likewise shaped so a **different message family with a
different state machine** could be added as a third parallel example — its own
parser tables, its own lifecycle, its own board — without disturbing this one.

---

*Provenance: every mechanism, table, and algorithm in this document was
implemented as a working prototype and validated live against a real Native-HA
IBM MQ queue manager over a mutual-TLS channel, with the settlement audit
exercised through real streaming audit queues. The figures in §10–§11 are measured
output from those runs — the state distribution and the attention sets, the O(N)
scan-cost curve, the client-vs-bindings browse rates, and the failover recovery.
The streaming collector, its scale-hardening, and its observability are all built
and covered to 100 % branch and line. Status codes and field structures are
grounded in public ISO 15022 renderings cross-checked against the free ISO 20022
schemas, cited in `docs/swift-settlement-reference.md`; no copyrighted SWIFT
Reference Guide text is reproduced.*
