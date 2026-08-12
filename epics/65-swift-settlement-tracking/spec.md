# Epic #65 — Confirmation Tracking v2: SWIFT MT540–548 settlement-lifecycle tracking (body-parsing) — spec

**Status:** Design approved; pushback complete (6 findings resolved); ready for planning
**Epic:** logical-minds-foundry/mq-gateway-replay-lab#65
**Relationship to v1:** Parallel, independent successor to
`docs/mq-native-confirmation-tracking.md` (the v1 header/CorrId deliverable). v1
is **not** refactored, replaced, or deprecated — it remains a complete, valid
solution for the standard MQ request/reply paradigm. v2 solves a different
paradigm (SWIFT securities settlement) that v1's mechanism cannot address.

---

## 1. Motivation — why v1's mechanism does not carry over

v1 tracks confirmations by the **MQ message headers**: the queue manager mints a
request `MsgId`, the counterparty echoes it back as the reply `CorrelId`, and
reconciliation is a set-difference over those two header fields. It never reads a
message body. For a symmetric request/reply flow that is exactly right, and v1
proved it live against real Native-HA MQ.

SWIFT securities-settlement messages do not correlate that way:

- The relationship between an instruction and the messages that report on it is
  carried **inside the message body** (ISO 15022 reference fields), not in the MQ
  descriptor. The MQ `CorrelId` echo v1 depends on is not the correlation key here.
- The lifecycle is **not binary**, and it spans **multiple business days**. A
  single instruction is followed, over the settlement cycle, by a *stream* of
  responses — one or more status advices (MT548), each possibly carrying several
  status qualifiers, and finally a settlement confirmation (MT544–547) — moving the
  instruction through a multi-state lifecycle. "Sent vs confirmed" cannot express
  this.

v2 therefore **parses the SWIFT message body** to (a) correlate each response to
its originating instruction and (b) derive an explicit settlement state. The MQ
streaming-queue audit architecture that captures the traffic is reused unchanged
(§8); everything new is in how the bodies are interpreted.

## 2. Thesis and boundaries

- **Correlation and state come from message *contents*, not headers.** This is
  the entire pivot.
- **Account-owner viewpoint.** v2 models the flow from the **account owner's**
  side: *we* originate the MT540–543 instructions, and the counterparty (the
  **account servicer / CSD**) acknowledges, matches, and confirms them via MT548
  and MT544–547. This role is load-bearing for the message direction (§7/§8) and
  for how the corpus is generated (§10); a servicer or CSD viewpoint would route
  the messages differently and is out of scope.
- **Settlement-cycle scoped, not calendar-day scoped.** Securities settlement is
  inherently **multi-day** (an instruction sent on T typically settles on T+1/T+2,
  and a failing instruction can roll further). Retention and reporting therefore
  follow the **open settlement window** — a horizon of a few business days (≈ T+2/
  T+3 plus a fails buffer), sized in the plan — not a rolling 24 h. An instruction
  stays in scope until it reaches a terminal state (SETTLED / REJECTED / FAILED)
  or ages out of the horizon. This is the one place v2 deliberately departs from
  v1's same-day model (§4.3, §7).
- **Reporting / observability tool, not a remediation tool.** v2 provides live and
  end-of-day *visibility* into a day's batch of settlement instructions: the state
  distribution, the named attention sets (aging, rejected, failed), and the
  timestamp-derived latency/SLA metrics. The replay / resend / re-instruction
  actions are **completely out of band** — v2 makes **no** assumption about how
  anyone reacts to a REJECTED or FAILED instruction. Consequently v2 does **not**
  carry forward v1's automated-resend `recovery.py`; there is no putter on any live
  path.
- **Documented to the letter, via a gating research spike.** Every state
  transition is driven by an **explicitly documented** MT548 status qualifier; no
  transition, qualifier, or field semantic is invented or assumed. Because this
  reference detail is load-bearing, it is produced **first**, by a gating
  reference-grounding spike (§16.1) whose committed, cited output — the field
  catalog, the qualifier value domains, and the reachability graph — is
  human-reviewed **before any parser or state-machine code is written**. Values
  quoted in this spec that are not yet source-verified are marked **(verify)**;
  the spike resolves each against a cited public source.
- **Source-fallback policy.** Where a directly-cited **free MT rendering** does not
  enumerate a needed code or domain, derive it from the **free ISO 20022 code
  list** (the published ExternalCodeSets / XSDs) and record the ISO 15022 ↔ 20022
  mapping **as an explicit, labelled inference** (not presented as a primary
  source); where neither yields it, **mark the path unsupported** and omit it from
  the corpus rather than guess. Nothing is asserted from memory.
- **100% public, zero proprietary.** Built only to public standards and public
  field renderings. Nothing institution-specific — no counterparty rules, no
  proprietary systems, no names, no copyrighted SWIFT Reference Guide text.
  Generic vocabulary only (Counterparty A/B, invented `GW.SEC.*` object names).
- **MT540–548 only; ISO 20022 noted, not built.** The ISO 20022 successors
  (sese.023 instruction / .024 status / .025 confirmation) are the documented
  generalization path and are described as a field-level mapping in the
  deliverable — deliberately **not** implemented (see §16, §18).
- **Data-driven spine.** The parser, the state machine, and the field-validation
  rules are **declarative tables/catalogs**, not hand-coded per message type. The
  tables *are* the documentation and the anomaly detector (§7).

## 3. The message family (ISO 15022 Category 5, Securities)

The instruction messages and their reporting messages:

| MT | Role | Direction (from our gateway's view) |
|----|------|-------------------------------------|
| 540 | Receive Free — instruction | outbound instruction |
| 541 | Receive Against Payment — instruction | outbound instruction |
| 542 | Deliver Free — instruction | outbound instruction |
| 543 | Deliver Against Payment — instruction | outbound instruction |
| 544 | Receive Free — **confirmation** | inbound response |
| 545 | Receive Against Payment — **confirmation** | inbound response |
| 546 | Deliver Free — **confirmation** | inbound response |
| 547 | Deliver Against Payment — **confirmation** | inbound response |
| 548 | Settlement **Status / Processing Advice** | inbound response |

544–547 pair with 540–543 by settlement direction (receive/deliver, free/against
payment). For lifecycle tracking, all four confirmations mean the same thing — the
instruction **settled** — and the specific number is retained for display only.

## 4. The settlement lifecycle state machine

Six named states: four progressive, two terminal-negative.

```text
INSTRUCTED ──▶ ACKNOWLEDGED ──▶ MATCHED ──▶ SETTLED         (lifecycle complete, positive)
     │              │              │
     └──────────────┴──────────────┴──▶ REJECTED / FAILED    (lifecycle complete, negative)
```

- **INSTRUCTED** — an MT540–543 instruction is present in the outbound audit;
  nothing has come back. (The v1 "sent/unconfirmed" analog.)
- **ACKNOWLEDGED** — the counterparty/CSD has accepted the instruction for
  processing; it is pending settlement. An instruction sitting here that has not
  yet matched is what the industry calls "unmatched" — **not** a separate state,
  simply "ACKNOWLEDGED, lifecycle not complete."
- **MATCHED** — the two sides agree; a settlement-matching milestone.
- **SETTLED** — a confirmation (MT544–547) has arrived. **Terminal-positive** — the
  true "confirmed."
- **REJECTED** — rejected at validation; **never accepted** into the flow.
  **Terminal-negative.** Operationally distinct from FAILED: a rejection means the
  instruction never entered processing.
- **FAILED** — accepted (often matched), due to settle, but settlement **failed**
  (e.g. lack of securities or cash). **Terminal-negative.** Note the real-world
  nuance that a "failing" instruction can still settle on a later day; within the
  **settlement window** (§4.3) v2 tracks such an instruction until it either
  reaches SETTLED or ages out of the horizon — it does not model indefinite
  cross-day re-settlement beyond the window.

"Lifecycle complete" = SETTLED, REJECTED, or FAILED. Everything else is in flight
and, as it ages, a candidate for the attention sets (§12) — but v2 only *reports*
it (§2).

### 4.1 Transition table (driven by documented MT548 qualifiers)

The intermediate and terminal-negative transitions are driven by the MT548 status
block (`:16R:STAT … :16S:STAT`), which carries a status qualifier `:25D:` and a
reason `:24B:`. SETTLED is driven purely by the arrival of an MT544–547.

| Trigger (parsed from body) | → State | Source status (my current reading — **verify**) |
|---|---|---|
| MT540–543 present in the instruction audit | **INSTRUCTED** | n/a (presence) |
| MT548 processing status = accepted/pending | **ACKNOWLEDGED** | `:25D::IPRC//PACK` **(verify)** |
| MT548 matching status = matched | **MATCHED** | `:25D::MTCH//MACH` (vs `NMAT` unmatched) **(verify)** |
| MT544 / 545 / 546 / 547 confirmation | **SETTLED** | message type (terminal +) |
| MT548 processing status = rejected | **REJECTED** | `:25D::IPRC//REJT` **(verify)** |
| MT548 settlement status = failing | **FAILED** | `:25D::SETT//` failing / `:24B::PENF//<reason>` **(verify)** |

The specific qualifier code strings above are **my current reading and are marked
(verify)**; the implementation grounds each one against a cited public source
(§19) before it is encoded. If a source shows a different or additional code, the
table follows the source — the table is data, edited to match documentation, not
asserted from memory.

### 4.2 Deriving the current state (the fold)

For each instruction, its response stream (all 548s and any 544–547, grouped by
correlation reference — §5) is ordered by MQ put-time and **replayed through the
transition table** starting from INSTRUCTED. The fold is **monotonic along the
reachability graph** (§4.3), which makes it robust to the untidy shape of real
traffic:

- **A message may carry several status qualifiers.** One MT548 commonly carries
  multiple `:25D:` blocks at once (e.g. processing *accepted* **and** matching
  *matched* **and** settlement *pending*). The fold extracts *all* of a message's
  statuses and applies each in documented precedence — one message can therefore
  advance more than one hop.
- **Advancement is monotonic.** A status is applied only if it moves the
  instruction *forward* along the reachability graph. The current state is the
  furthest state reached.
- **Repeats are no-ops, not anomalies.** A re-advice of a status the instruction
  has already reached (a redundant or re-sent 548) changes nothing and is **not**
  flagged.
- **Out-of-order arrival is tolerated** by put-time ordering; a status that would
  move *backward* or is **unreachable** from the current state is the anomaly
  (§13), never a silent state change.

The put-time of each *accepted, advancing* transition gives the per-hop timestamp
used for the lifecycle-latency metrics (§12).

### 4.3 Reachable-transition set (produced by the gating spike — §16.1)

Which in-flight states may legally transition to REJECTED vs FAILED, and whether
progression is strictly linear or admits documented skips (e.g. straight to
MATCHED without a separately-observed ACKNOWLEDGED when one 548 carries both), is
resolved from the public documentation by the **gating reference spike** and
encoded as the **reachability graph** the fold enforces. The graph therefore does
double duty: it is both the state model *and* the validity check every folded
status is tested against (§4.2). The grounded graph is **human-reviewed before any
fold/state-machine code is written**.

### 4.4 The settlement window (scoping, replacing v1's "today")

Because a lifecycle spans days (§2), scoping is by the **open settlement window**,
not the calendar day:

- **In scope:** any instruction whose put-time falls within the settlement horizon
  and which has **not** yet reached a terminal state — plus terminal instructions
  still within the horizon, so end-of-day boards can show the day's completions.
- **Ages out:** an instruction that reaches the horizon (≈ T+2/T+3 + a fails
  buffer, sized in the plan) without a terminal state is reported as **stale /
  aged-out** — a distinct attention category, not a silent drop and not a
  manufactured orphan.
- **Late responses stay correlated.** Because retention covers the horizon (§7), a
  settlement confirmation arriving on T+2 still finds its T instruction in the
  window and folds correctly — the multi-day failure mode that a 24 h window would
  cause (a real confirmation mis-flagged as an orphan) does not occur.

The horizon length is a single configured value (the plan sizes it, and the
`CAPEXPRY`/`MAXDEPTH` math in §7 follows from it), so the same code serves a
T+1 or a T+2 market.

## 5. Correlation model (reference-based, in the body)

The body-level analog of v1's `MsgId ↔ CorrelId` echo:

- Each message carries its own **sender's reference** — `:20C::SEME//` (Sender's
  Message Reference) in the general information block. **(verify field/qualifier)**
- Each response (MT548, MT544–547) carries, in its linkages block
  (`:16R:LINK … :16S:LINK`), a **related reference** — `:20C::RELA//` — whose value
  **is the originating instruction's `SEME`**. **(verify)**

The collector indexes instructions by `SEME` and groups responses by `RELA`. A
response whose `RELA` matches no sent instruction is an **orphan** anomaly (§13) —
the direct analog of v1's Algorithm-3 orphan reply.

Amendment/cancellation chains may route through a **previous reference**
(`:20C::PREV//`) **(verify)**; if the public documentation shows this is used for
the lifecycle we track, the correlation layer handles it. Otherwise it is recorded
as out of scope (§16). v2 does **not** attempt **economic matching** (ISIN +
settlement date + quantity + counterparty) — that is how a CSD pairs the two
counterparties' independent sides, it is fuzzy, and it is not what v2 tracks. v2
follows *our* instruction through *its* lifecycle via the reference echo.

## 6. Architecture — `gwlab.swift`, parallel to v1

A new subpackage; **v1 modules are not touched.** Data-driven spine (declarative
tokenizer + tables/catalogs).

```text
gwlab/swift/
  tokenizer.py     SWIFT envelope + block-4 parser: reads the MT number from
                   Block 2, then tokenizes block 4 → uniform field tree
                   (:16R:/:16S: blocks; :NN[a]:qualifier//value fields)
  fields.py        field-spec catalog: per field/qualifier — format spec,
                   character set, enumerated value domain (sourced from public docs)
  correlate.py     SEME/RELA reference linking: instruction ↔ its response stream
  lifecycle.py     the 6-state transition + reachability table; the fold
                   (response sequence → current state + per-hop timestamps)
  collect.py       the read-only collector: browse both audit queues, correlate,
                   fold, emit the day report
  observe.py       state-distribution / latency / SLA metrics + the anomaly set
  dashboard.py     the v2 board (state-machine oriented)
  responder.py     emits realistic 548 / 544–547 sequences for a driven instruction
  corpus/          realistic sample message flows — one per documented lifecycle path
```

Reused unchanged from v1: `messaging.py` (`fake_messaging` ↔ `pymqi_messaging`),
`config`, the streaming-queue pattern, and the exporter/CLI scaffolding. v2 adds
its subcommands under the existing `gwlab` CLI (working name: a `swift` command
group), so one tool drives both labs.

**Why data-driven (approach A, chosen over hand-coded per-type):** it is the only
option that simultaneously satisfies *documented-to-the-letter* (the tables are the
documentation), *flag-don't-crash anomalies* (out-of-catalog is the signal),
*re-implementable elsewhere* (the deliverable publishes the tables), and *future
third message family* (new tables + corpus + dashboard, no rewrite). The cost is
upfront structure; that structure is itself a deliverable.

## 7. MQ objects & audit topology (structurally identical to v1)

Two audit queues, the same proven streaming pattern, own names:

- **`GW.SEC.INSTRUCT.AUDIT`** — retained originals of the MT540–543 instructions we
  sent; streams the duplicate onward to the counterparty. `CAPEXPRY` caps it to the
  **settlement horizon** (§4.4) — a few business days, **not** v1's 24 h — so a
  late (T+2) confirmation still finds its instruction in the window.
- **`GW.SEC.STATUS.AUDIT`** — a streamed audit copy of **every inbound response**
  (all MT548 status advices and MT544–547 confirmations); `CAPEXPRY` on the same
  horizon.

Because the horizon is a multiple of v1's window, the `MAXDEPTH` and disk sizing
grow by that multiple: the queue-file must hold the horizon's peak instruction and
response volume, sized in the plan (the same simple arithmetic v1 documented,
scaled by the number of days in the horizon).

Reuses the lab's existing counterparty queue manager and inter-QM channels; v2
adds **only its own** `GW.SEC.*` queues (parasite rule intact — never mutate or
restart the lab's QM). The collector browses only these two audit queues, never a
live path, so it never contends with throughput. Exact MQSC object definitions are
produced in the plan and proven in the live-run validation (#68).

**Coexistence with v1 on the shared lab box.** v1 already deploys a per-QM board +
Prometheus exporter; v2 adds its own. v2's exporter **port** and dashboard **path/
artifact name** must be distinct from v1's so the two boards run side by side
without collision (specified in the plan's deploy task).

## 8. Data flow

```text
instruction (MT541 …) ─put─▶ GW.SEC.INSTRUCT.AUDIT (retained) ─stream─▶ counterparty
                                                                            │
over the settlement cycle (T … T+2) the counterparty emits, e.g.:
      548 accepted  →  548 matched  →  545 confirmation
                                                                            │
      each arrives ─stream─▶ GW.SEC.STATUS.AUDIT (audit copy)  ◀────────────┘

collector (read-only):
  browse GW.SEC.INSTRUCT.AUDIT → instructions indexed by :20C::SEME//
  browse GW.SEC.STATUS.AUDIT   → responses grouped by :20C::RELA//
  per instruction: replay its response stream (put-time order) through the
                   transition + reachability table → current state + per-hop times
  report: state distribution · in-flight/aging set · REJECTED set · FAILED set ·
          lifecycle latencies · anomalies · scan cost
```

The structural difference from v1: **many responses fold into one instruction's
lifecycle**, versus v1's one-reply-per-request header match.

## 9. Parser & field-spec catalog

The tokenizer reads the block-4 structure into a uniform field tree without
knowing any message type. The catalog (`fields.py`) records, for every field and
qualifier v2 consumes: the **format spec** (e.g. `16x`, `4!c`, `:4!c//35x`), the
**character set** (SWIFT `x` / `z` / `n` / `c` / `a` sets), and the **enumerated
value domain** where one exists — each entry **sourced from public documentation
and cited**. The catalog serves three consumers at once: the parser's validation
rules, the corpus generator's fidelity spec, and a published table in the
deliverable.

## 10. Sample-message corpus & lifecycle paths (a first-class deliverable)

A generated set of realistic message flows, **one per documented lifecycle path**,
each message **format-valid** against the §9 catalog. This is a genuine research
effort, not a fixture afterthought:

- Enumerate the **documented, reachable** lifecycle paths (driven by §4.3): clean
  settle; rejected-at-validation; matched-then-failing; acknowledged-but-unmatched
  and aging; plus the anomaly cases (orphan `RELA`, malformed field, undocumented
  qualifier, illegal transition).
- Extract field formats, character sets, and value domains (§9) so the samples read
  as realistic traffic, not toy strings.
- The corpus is the fixture set for both the test suite (§14) and the live
  simulation that drives the hero board (#68).

## 11. Observability & dashboard

A day's batch, live and end-of-day, state-machine oriented:

- **State distribution (hero)** — count in each of the six states; a funnel/bar of
  INSTRUCTED → ACKNOWLEDGED → MATCHED → SETTLED with REJECTED and FAILED broken out.
- **Lifecycle latencies** — inter-state timings from the MQ put-timestamps:
  instructed→acknowledged, acknowledged→matched, matched→settled; mean and tail,
  and degradation over the day.
- **Attention sets (named, reporting only)** — the **in-flight-and-aging** set
  (chase candidates), the **REJECTED** set, and the **FAILED** set; each an
  instruction by its `SEME`, with age and last-known state. No action is taken.
- **Anomalies** — orphan / malformed / undocumented-qualifier / illegal-transition
  counts (the v1 Algorithm-3 analog), each flagged, never folded into a healthy
  state.
- **Scan cost** — messages/headers scanned vs wall-clock, carried forward from v1's
  scale thread, so reconciliation cost at scale stays quantified.

## 12. Anomaly path & the no-silent-failures stance

Every one of these is **surfaced** (flagged and logged with the offending
reference), never swallowed and never a hard crash of the run:

- a message that will not tokenize;
- a field violating its catalog format or character set;
- a qualifier value outside the documented domain;
- a transition not in the reachability set (out-of-order / illegal);
- a response `RELA` matching no sent instruction (orphan).

The **collector is the graceful-degradation boundary**: it parses each browsed
message inside a guard, converts any tokenize failure or field violation into a
flagged anomaly on a **single unified anomaly channel** (kinds: malformed /
field-violation / orphan / transition), and **continues** — one bad message never
aborts the run. All kinds flow through to the report, the metrics, and the board.

First-order, v2 assumes a valid corpus; the anomaly path is **designed-in** and
exercised by the anomaly fixtures. This is the v2 realization of the project's
"no silent failures" rule: a parse or state fault becomes a visible signal, not a
dropped message and not a crashed run.

## 13. Testing strategy

Test-driven against the fake harness (`fake_messaging`), so all lifecycle and
failure paths are deterministic and fast:

- unit tests for the tokenizer (block structure, field extraction);
- catalog validators (format / char-set / value-domain acceptance and rejection);
- each individual transition, and each full lifecycle path via the corpus;
- correlation (SEME/RELA grouping, orphan detection);
- an end-to-end collector test over a simulated settlement window (multi-day),
  including late (T+2) confirmations and aged-out instructions;
- then the **live run** (#68): drive a realistic day through real `GW.SEC.*`
  streaming audit queues on the lab QM and capture the hero board.

## 14. Deliverable

A v2 design markdown (working location `docs/`, mirroring v1's
`mq-native-confirmation-tracking.md`): prose → worked example → technical spec,
re-implementable minimally elsewhere. It publishes the state/transition table, the
field-spec catalog, and the correlation model, and includes the **ISO 20022
field-level mapping** (sese.023/.024/.025) as the noted, deferred generalization
and the **future third message family** extension point.

## 15. Scope — explicitly out

- Any remediation / replay / resend / re-instruction **action** (out of band).
- **ISO 20022 XML** parsing (noted as a mapping only; not built).
- **Economic matching** (ISIN + date + quantity + counterparty).
- **Re-settlement beyond the settlement horizon** — a failing instruction that has
  not settled by the end of the horizon ages out (§4.4) rather than being tracked
  indefinitely across the calendar.
- Any client-identifiable content; any copyrighted SWIFT Reference Guide text.

## 16. Open research items — resolved by the gating spike, from public sources

### 16.1 The gating reference-grounding spike (epic's first implementation task)

Before any parser, state-machine, or corpus code, a spike produces a **committed,
cited SWIFT-settlement reference**: the field-spec catalog (§9), the qualifier
value domains, the correlation fields, and the **reachability graph** (§4.3). It is
**human-reviewed** before downstream code begins, and it applies the
**source-fallback policy** (§2): cited free MT rendering → else free ISO 20022 code
list with a labelled 15022↔20022 mapping → else mark the path unsupported. Nothing
downstream is built on an unreviewed or uncited value. The items it must resolve:

1. The exact `:25D:` qualifier code strings for accepted / matched / rejected /
   failing (the §4.1 **(verify)** rows), and any additional documented codes.
2. The **reachability graph** (§4.3): which in-flight states may transition to
   REJECTED vs FAILED, and whether progression is strictly linear or admits
   documented skips / combined-status 548s.
3. The exact correlation fields (§5): `:20C::SEME//` / `:20C::RELA//`, and whether
   `:20C::PREV//` participates in the tracked lifecycle.
4. Field formats, character sets, and value domains for every consumed field (§9).
5. The reason-code vocabulary (`:24B:`) worth surfacing on the board vs collapsing.

## 17. Sources (public, to be cited inline as items in §16 are resolved)

- ISO 20022 message definitions (free, royalty-free, with XSD schemas) — sese.023
  / .024 / .025: <https://www.iso20022.org/iso-20022-message-definitions>
- Public MT540–548 field-structure renderings (e.g. iotafinance MT548):
  <https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT548.html>
- IBM MQ streaming-queue documentation (already cached under `build/refs/ibm-docs/`
  from v1) for the audit topology (§7).

Copyrighted SWIFT MT Reference Guide text is **not** reproduced; formats are
implemented from public renderings.

## 18. Success criteria

- A working, **live-runnable** v2 prototype (parallel to v1, v1 untouched): drive a
  day's MT540–548 batch through real `GW.SEC.*` streaming audit queues and watch a
  live state-machine dashboard.
- Every state transition traces to a **cited, documented** MT548 qualifier; no
  invented semantics; the reachability set is source-grounded and human-reviewed.
- A realistic, format-valid **sample corpus** exercising every documented lifecycle
  path plus the anomaly cases.
- Anomalies (malformed / undocumented / illegal-transition / orphan) are **flagged,
  never swallowed or crashed**.
- Observability surfaces state distribution, lifecycle latencies, the named
  attention sets, anomalies, and scan cost — meaningful, not decorative.
- A committed, fully generic **v2 design markdown**, re-implementable elsewhere,
  with the ISO 20022 mapping and the third-message-family extension point noted.
- 100% public and generic: no proprietary or client-identifiable content anywhere.

## 19. Relationship to v1 and the future third message family

v1 (header/CorrId) and v2 (body-parsing) are **two parallel worked examples** of
the same underlying MQ streaming-audit + read-only-collector pattern, each fit to a
different correlation paradigm. The architecture is deliberately shaped so a **third
message family** — a different message type with a different state machine — could
be added later as a third parallel example (its own parser tables, corpus,
lifecycle, and dashboard) without disturbing v1 or v2. That third example, and the
ISO 20022 generalization, are noted, not built.
