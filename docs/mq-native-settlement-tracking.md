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
measured output from that run, not an illustration.

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

## 9. The collector: what to build

One small, read-only collector that, on a schedule, browses the two audit queues,
runs Algorithms 1–3, and publishes the state distribution, the named attention
sets, the lifecycle latencies, and the anomalies. It holds no state that must
survive a restart — the audit queues are the state. Its output feeds an operations
dashboard. The data-driven core — the field catalog, the state-transition table,
and the correlation rule — *is* the documentation, and it is what a re-implementer
elsewhere reproduces; the surrounding code is straightforward once those tables
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

## 11. Scale

The cost profile is the request/reply recommendation's, unchanged: the fold and
the correlation are cheap; the cost is the **browse** of the audit queues, bounded
by the settlement window's volume via `CAPEXPRY` and reduced by running the
collector local to the queue manager and browsing incrementally. Body parsing adds
a fixed per-message cost proportional to message size, dwarfed by the browse
round-trip. The collector reports the number of messages scanned beside the
wall-clock so the cost stays quantified as volume grows.

## 12. Beyond MT540–548 — the deferred generalization

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
exercised through real streaming audit queues. The figures in §10 are measured
output from that run. Status codes and field structures are grounded in public
ISO 15022 renderings cross-checked against the free ISO 20022 schemas, cited in
`docs/swift-settlement-reference.md`; no copyrighted SWIFT Reference Guide text is
reproduced.*
