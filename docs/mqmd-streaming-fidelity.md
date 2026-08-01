# MQMD fidelity across the streaming-audit copy — original vs. streamed

**Status:** documentation + web research, **now confirmed by two live lab runs**
(issue #108 and issue #187, both 2026-08-01). The empirical plan in §5 was
executed against the shared lab (browse-only): one probe put through
`GW.SEC.INSTRUCT`, its retained original on `GW.SEC.INSTRUCT.AUDIT` diffed
field-by-field against the streamed copy `SVCQM` received, matched by `MsgId`.
**Result: only ONE MQMD field differs — `Expiry` — and it differs favourably**
(the copy becomes `MQEI_UNLIMITED`). Every other MQMD field measured
**byte-/value-identical**. See **§1.4 Empirical confirmation** for the measured
diff; the previously `UNCERTAIN` / "verify" rows in §3 are now marked
**MEASURED**.

A **second run (issue #187, 2026-08-01)** — pymqi client over mutual TLS from the
app-client, `amqsbcg` on both queues — then exercised the three cases the
single-message probe could not: **report options** (`MQRO_COA | MQRO_COD`),
**a grouped MQMD v2 message**, and an **MQRFH2 named property**. It measured
report options **stripped** from the streamed copy (only a local COA from our own
QM is produced — the capstone streaming-strip proof, R6), and grouping fields and
the MQRFH2 property **preserved byte-identically** (R4, R5). See the new **§1.5
Empirical confirmation — report & structured-message experiments** for the
measured results. **Net finding, now confirmed across scalar fields, grouping,
and properties: streaming preserves the entire MQMD/message except `Expiry`
(→ `MQEI_UNLIMITED`, favourable) and report options (stripped).**

**Epic:** #105 (repurposed to a strategic-risk documentation epic). Companion to
`epics/105-coa-cod-telemetry/spec.md` §3/§5 and §4 (the streaming/COA-COD spike).

> **Reading the citations.** `docs.ibm.com` returns HTTP 403 to automated
> fetchers, so the IBM primary pages below were reached via web *search* over
> their content, corroborated against the feature author's IBM Community blog
> and the IBM Redbooks/perf report. **A human should re-read the two primary IBM
> pages directly** — "Streaming queues configuration"
> (`topic=queues-streaming-configuration`, 9.3.x / `topic=configuring-streaming-queues`,
> 9.4.x) and "Field details for MQMD" (`topic=descriptor-field-details-mqmd`,
> 9.4.x) — before anyone relies on a row here operationally. Full URLs in §7.

---

## 1. Purpose, topology, and the key premise

### 1.1 Purpose

This repo's audit mechanism is an MQ-native streaming copy: the app puts one
instruction, and the queue manager — with **zero application logic** — writes a
retained ORIGINAL to our local audit queue and streams a COPY onward to the
counterparty. The two are *near-identical by design*, but "near" is doing real
work. This note is a rigorous, cited, field-by-field account of exactly how the
STREAMED COPY differs from the retained ORIGINAL, and what each difference means
for a **SWIFT securities-settlement counterparty** that consumes the copy.

### 1.2 Topology (from CLAUDE.md and #105 spec §2)

```
app MQPUT
   │
   ▼
GW.SEC.INSTRUCT            (QALIAS)                 ← app's target name
   │  resolves to
   ▼
GW.SEC.INSTRUCT.AUDIT      (QLOCAL, STREAMQ … STRMQOS(MUSTDUP))
   │                        = the RETAINED ORIGINAL  ← we browse this; never leaves our QM
   │  queue manager streams a duplicate to STREAMQ target
   ▼
GW.SEC.INSTRUCT.REMOTE     (QREMOTE)                 = the STREAMED COPY
   │  resolves to
   ▼
xmit queue ─► channel ─► SVCQM landing queue ─► responder (destructive get)
                                    ▲
                    the counterparty consumes THE STREAMED COPY here
```

`STRMQOS(MUSTDUP)` means the queue manager will **fail the original put** if it
cannot also queue the duplicate — the audit copy and the transmitted copy are
atomic with respect to each other. (The alternative, `BESTEF`, would let the
original succeed even if the streamed copy could not be delivered; we do not use
it, and the difference is itself a fidelity property worth stating.)

### 1.3 The key premise

> **The counterparty never sees what the application put. It sees the queue
> manager's streamed derivative of it.** The retained ORIGINAL on
> `GW.SEC.INSTRUCT.AUDIT` is what we reconcile and replay against; the STREAMED
> COPY on the wire is a queue-manager-generated message that is *documented to
> match the original except for a small, enumerated set of fields*. Anything the
> counterparty authorizes on, routes on, audits on, or expires on is read from
> the **copy** — so every field where copy ≠ original is a potential
> counterparty-visible behaviour change that the application cannot see and did
> not intend.

**[DATA]** IBM's own framing: *"In most cases, the copy of the message delivered
to the second queue is a duplicate of the original message, including all of the
message descriptor fields, including the message ID and correlation ID … there
are some message descriptor fields that are not retained on the streamed
message."* (Streaming queues configuration; corroborated by the feature author's
blog, which lists **Message ID, Correlation ID, Message Properties, and
Application Data as identical**.)

**[JUDGMENT]** The documented exception set is small (Expiry, report options).
The residual risk is therefore concentrated: (a) the one field IBM *tells* us is
rewritten and can be silently re-rewritten by a queue attribute (Expiry via
CAPEXPRY), and (b) the fields the docs describe only in aggregate ("all
descriptor fields") where a second, recurring IBM phrasing conflates streaming
with pub/sub and muddies the origin-context guarantee (§3, §4).

### 1.4 Empirical confirmation (live lab, 2026-08-01)

**[DATA — measured, issue #108].** The §5 plan was executed against the shared
lab, browse-only, no mutation. One probe instruction was put through the
`GW.SEC.INSTRUCT` QALIAS. The **retained original** was captured with `amqsbcg`
on `GW.SEC.INSTRUCT.AUDIT` (queue manager `NHAUAPP`); the **streamed copy** was
captured with `amqsbcg` as `SVCQM` received it. The two messages were matched by
**`MsgId`** (byte-identical on both) and their MQMDs diffed field by field.

**Result: exactly ONE MQMD field differs — `Expiry` — and it differs in the
desired direction.**

| MQMD field | Retained original (`GW.SEC.INSTRUCT.AUDIT`) | Streamed copy (`SVCQM`) | Verdict |
|---|---|---|---|
| **Expiry** | `2589276` (~3 days — the AUDIT queue's `CAPEXPRY`) | `-1` (`MQEI_UNLIMITED`) | **DIFFERS — favourably.** The counterparty receives an instruction with **no expiry**, matching the no-expiry requirement. |

**Measured byte-/value-identical on the streamed copy vs. the original** (every
other MQMD field, plus payload):

- `MsgId` (**byte-identical** — the load-bearing correlation/replay guarantee),
  `CorrelId`
- Origin context: `PutApplName` (`amqsput`), `PutDate` (`20260801`), `PutTime`
  (`20081793`), `PutApplType`
- Identity context: `UserIdentifier` (`mqm`), `AccountingToken`,
  `ApplIdentityData`
- `Persistence`, `Priority`, `Encoding`, `CodedCharSetId` (`1208`), `Format`
  (`MQSTR`), `BackoutCount`
- `ReplyToQ` / `ReplyToQMgr` (`NHAUAPP`)
- Grouping/segmentation (single-message probe): `GroupId`, `MsgSeqNumber`,
  `Offset`, `MsgFlags`
- Application payload

**Method.** `amqsbcg` on both queues, messages matched by `MsgId`, MQMDs diffed
field by field (§5.4). Single-message probe, so the grouping/segmentation block
is confirmed identical **for the single-message case only** — the
grouped/segmented-message case remains documentation-only (see §3 rows 25–29 and
§4 #5). The `Expiry` result also confirms the §4 #1 mechanism directly: the
stream target imposed **no** `CAPEXPRY`, so the copy is `MQEI_UNLIMITED`; the
`2589276` on the original is the **AUDIT** queue's `CAPEXPRY`, which does **not**
propagate to the streamed copy. The design invariant to preserve is therefore
**no `CAPEXPRY` on the stream target `GW.SEC.INSTRUCT.REMOTE`** (or whatever it
resolves to on the path).

### 1.5 Empirical confirmation — report & structured-message experiments (2026-08-01)

**[DATA — measured, issue #187].** A second authorized browse-only run
(2026-08-01, live lab `NHAUAPP` ↔ `SVCQM`) exercised the three cases the
single-message probe of §1.4 could not: **report options**, a **grouped MQMD v2
message**, and an **MQRFH2 named property**. Because these need custom MQMD /
report / group / property control that `amqsput` cannot express, each message was
put with a **pymqi client** on the app-client, client-connecting to `NHAUAPP` over
`APP.SVRCONN` with **mutual TLS**; the streamed copy was then read with `amqsbcg`
on the `SVCQM` landing queue and, for reports, on the report queue.

**R6 — report options (`MQRO_COA | MQRO_COD`): the capstone.** One instruction was
put through the `GW.SEC.INSTRUCT` QALIAS with `Report = MQRO_COA | MQRO_COD`,
`ReplyToQ = GW.SEC.PROBE.REPORT`, `ReplyToQMgr = NHAUAPP`. Result:

- **Exactly ONE report arrived**, on the `ReplyToQ` — `MsgType = 4`
  (`MQMT_REPORT`), **`Feedback = 259` (`MQFB_COA`)**, `CorrelId` = the original
  instruction's `MsgId`, and **`PutApplName = 'NHAUAPP'`** — i.e. generated by
  **our own queue manager**, at ≈ send time.
- The streamed copy **did reach `SVCQM`** (its landing-queue depth incremented),
  but **`SVCQM` generated ZERO reports** — the report-landing queue stayed at
  depth 1 (the single local COA), with **no** `SVCQM` `ARRIVED`-COA and **no**
  `SVCQM` `DELIVERED`-COD.

**[DATA → JUDGMENT]** This is the direct empirical proof of the streaming-strip
finding (Deliverable A): **streaming strips the report options from the streamed
copy**, so the only report is a **useless LOCAL COA from our own QM** (≈ send
time), landing on the `ReplyToQ`. In production that `ReplyToQ` is the app's
**business-reply** queue `GW.SEC.REPLY` (R7), so a stray local COA would land in
the business-response stream. There is no counterparty-side arrival or delivery
telemetry from this topology. **R6 → CONFIRMED (live).**

**R4 — grouped message.** One MQMD v2 message was put with
`GroupId = 0xAA × 24`, `MsgSeqNumber = 7`, `MsgFlags = MQMF_MSG_IN_GROUP (8)`.
The streamed copy on `SVCQM` showed **`GroupId = X'AAAA…AA'` (24 bytes),
`MsgSeqNumber = 7`, `MsgFlags = 8` — byte-identical**. The grouping/segmentation
block survives streaming intact for a genuine group member, not merely for the
single-message case. **R4 → CONFIRMED preserved.**

**R5 — MQRFH2 named property.** One message was put with `Format =
MQFMT_RF_HEADER_2` and an MQRFH2 carrying a `<usr><SwiftSettleRef>ABC123XYZ</SwiftSettleRef></usr>`
folder. The streamed copy on `SVCQM` showed **`Format = 'MQHRF2'`** and the body
carried **`<usr>…SwiftSettleRef…</usr>` intact** — the named property survives
streaming. **Scope note:** this is the **inline-RFH2** case (property carried as
RFH2 in the message data); the **message-handle / `PROPCTL`-separated** property
representation was **not** separately tested, and remains subject to the §6
`PROPCTL`-reshaping caveat. **R5 → CONFIRMED preserved (inline-RFH2 case).**

**Net conclusion (now confirmed across scalar fields, grouping, and properties):**
streaming preserves the **entire MQMD/message except `Expiry`** (→ `MQEI_UNLIMITED`,
favourable — §1.4) **and report options** (stripped — R6). Grouping (R4) and the
inline MQRFH2 property (R5) join the scalar/context fields (§1.4) as measured
preserved.

---

## 2. How to read the table

- **Status** — one of: `copied identically` · `altered` · `reset` · `UNCERTAIN`.
  Rows resolved by the live run (§1.4) are tagged **MEASURED** with their measured value.
- **Evidence** — the citing source, or `[DOCS SILENT]` where no public IBM text
  states the behaviour for the queue-to-queue `STREAMQ` case specifically.
- **Remote-app-dependency risk (H/M/L/None)** — **[JUDGMENT]**: how likely a
  SWIFT securities-settlement counterparty is to depend on this field *and* be
  affected by the way streaming treats it. A field can be `copied identically`
  and still be flagged if the copy's fidelity is only weakly documented.
- **[DATA]** = what the docs state. **[JUDGMENT]** = the risk read on top.

Throughout, "the original" = the retained message on `GW.SEC.INSTRUCT.AUDIT`;
"the copy" = the streamed message that reaches `SVCQM`.

---

## 3. MQMD field-by-field: original vs. streamed copy

| # | MQMD field | Status | Evidence (cite / `[DOCS SILENT]`) | Risk | One-line counterparty rationale **[JUDGMENT]** |
|---|-----------|--------|-----------------------------------|:----:|-----------------------------------------------|
| 1 | **StrucId** (`"MD  "`) | copied identically | Structural constant; not an app value. Streaming doc: all descriptor fields duplicated. | None | Structure tag; no counterparty ever reads it as data. |
| 2 | **Version** | copied identically | Same as above. Version reflects which MQMD fields are present, not payload. | None | A v2 original yields a v2 copy; group/segment fields ride on v2. No settlement dependency. |
| 3 | **Report** | **altered (reset) — MEASURED** | **[DATA — measured, §1.5, issue #187]** A put with `MQRO_COA \| MQRO_COD` produced **exactly one** report — a **local COA** (`Feedback 259`/`MQFB_COA`, `PutApplName 'NHAUAPP'`) from **our own QM**; **`SVCQM` generated ZERO reports**. Confirms the streaming doc + author blog: *"report options that cause the queue manager to generate new messages are removed"* (Activity, Expiration, Exception, COA, COD **not enabled** on the copy). | **Med** | Measured: the streamed leg carries **no** report bits, so **no COA/COD is requested at `SVCQM`** — the only report is a useless local COA (≈ send time) on our own QM. This is the crux of the #105 spike: reports must be requested on the *original put*, and the counterparty leg carries none. See R6 in the risk register. |
| 4 | **MsgType** (`MQMT_DATAGRAM`) | copied identically | `[DOCS SILENT]` on MsgType specifically; falls under "all descriptor fields duplicated". | Low | Counterparty expects a datagram; unchanged. Confirm empirically (§5). |
| 5 | **Expiry** | **altered — MEASURED** | **[DATA — measured, §1.4]** original `2589276` (~3d, AUDIT's `CAPEXPRY`) → copy `-1` (`MQEI_UNLIMITED`). Confirms the streaming doc: set to `MQEI_UNLIMITED` regardless of the original, *unless* the stream target carries `CAPEXPRY` (it did not). | **HIGH** | Our requirement is **no expiry**; the copy measured **UNLIMITED** — the desired direction. **The residual risk is a `CAPEXPRY` set on `GW.SEC.INSTRUCT.REMOTE` (or the resolved target) silently re-imposing an expiry** — hence the invariant: **no `CAPEXPRY` on the stream target**. See §4 #1. |
| 6 | **Feedback** | copied identically | `[DOCS SILENT]`; datagram carries `MQFB_NONE`. Reports (not this message) carry feedback. | None | Instruction is not a report; Feedback is `NONE` on both. |
| 7 | **Encoding** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `Encoding` = original. | Low | If SWIFT parses numeric fields by Encoding, it matches — measured identical. |
| 8 | **CodedCharSetId** (CCSID) | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy CCSID = original (`1208`, UTF-8). No streaming transcoding observed. | **Med** | SWIFT MT text is charset-sensitive; the copy landed at CCSID `1208`, equal to the original. (Any channel `CONVERT(YES)` effect would be a transmission-leg artefact, not streaming — R9.) |
| 9 | **Format** (e.g. `MQSTR`) | copied identically | "All descriptor fields duplicated." | Low | Counterparty dispatches on Format; unchanged. Note: on the xmit queue the message is wrapped in an `MQXQH` (Format `MQXMIT`); that is the transmission envelope, not a change to the copy's own MQMD (§5 caveat). |
| 10 | **Priority** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `Priority` = original. Confirms the "`DEFPRTY` of the secondary queue has no effect" clause ⇒ priority is the **original's**. | Low | Priority ordering preserved end-to-end from what the app set; measured identical. |
| 11 | **Persistence** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `Persistence` = original. Confirms the "`DEFPSIST` of the secondary queue does not affect the copy" clause ⇒ persistence is the **original's**. | **Med** | Settlement instructions must be **persistent**; the copy measured equal to the original's persistence, not the target-queue default. |
| 12 | **MsgId** | **copied identically — MEASURED (byte-identical)** | **[DATA — measured, §1.4]** `MsgId` on the retained original **byte-equals** `MsgId` on the streamed copy (the messages were matched by it). Refutes the pub/sub-conflation doubt (§4 #3) for `STREAMQ`. | **HIGH** (as an asset) | The load-bearing guarantee for #105's correlation (`report.CorrelId → instruction MsgId → SEME`) **and** for replay — **measured intact**. Counterparty may also dedupe on MsgId. |
| 13 | **CorrelId** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `CorrelId` = original. | **Med** | If SWIFT threads on CorrelId, it survives; measured identical. |
| 14 | **BackoutCount** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** `BackoutCount` identical on copy vs. original (both `0` on the fresh probe). Confirms the reasoned expectation. | Low | Counterparty's own consumer increments *its* BackoutCount on *its* queue; our copy starts clean at `0`, which is correct. |
| 15 | **ReplyToQ** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `ReplyToQ` = original. | **Med** | Copied to the counterparty; since report *options* are stripped (row 3), SVCQM has a ReplyToQ but no trigger to use it on the streamed leg. Carries the *business* (MT548) reply address. |
| 16 | **ReplyToQMgr** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `ReplyToQMgr` = original (`NHAUAPP`). | **Med** | Business replies (MT548) route back on `ReplyToQMgr`; measured to carry our QM name (`NHAUAPP`) — the mechanism #105 §2 relies on. |
| 17 | **UserIdentifier** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `UserIdentifier` = original (`mqm`). The context-conflation concern (§4 #3) is **refuted** for `STREAMQ`: identity context is copied, not re-stamped. | **HIGH** | **A SWIFT counterparty may authorize/authenticate on `UserIdentifier`.** Measured identical, so an authorization decision on the copy matches the app's intent. |
| 18 | **AccountingToken** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `AccountingToken` = original. Moves with the identity block. | Low–Med | Part of identity context; measured identical alongside #17. |
| 19 | **ApplIdentityData** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `ApplIdentityData` = original. | Low–Med | Free-form identity string; measured to survive. |
| 20 | **PutApplType** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `PutApplType` = original. Not re-stamped despite the QM-initiated put. | **Med** | Counterparty routing/audit by origin type sees the app's value, not a QM/channel type. |
| 21 | **PutApplName** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `PutApplName` = original (`amqsput`). **REFUTES** the re-stamp concern: the origin context is copied from the original, not re-stamped for the streaming put. | **HIGH** | **A SWIFT counterparty commonly routes, filters, or audits on `PutApplName`.** Measured identical — counterparty sees the true originator. |
| 22 | **PutDate** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `PutDate` = original (`20260801`) — reflects the **original's** put instant, not the streaming put. | **Med–High** | Counterparty aging by `PutDate/PutTime` matches ours; no cross-clock shift. |
| 23 | **PutTime** | **copied identically — MEASURED** | **[DATA — measured, §1.4]** copy `PutTime` = original (`20081793`). Pairs with PutDate. | **Med–High** | Settlement SLA / "stale instruction" logic on the copy reads the original's put time. |
| 24 | **ApplOriginData** | copied identically *(per aggregate docs)* — **verify** | Origin context; `[DOCS SILENT]` specific. | Low | Free-form origin string; low counterparty dependence, but measure with the origin block. |
| 25 | **GroupId** | **copied identically — MEASURED (grouped)** | **[DATA — measured, §1.5, issue #187]** A grouped MQMD v2 put (`GroupId = 0xAA×24`) streamed to `SVCQM` with **`GroupId = X'AAAA…AA'` (24 bytes) — byte-identical**. (Single-message case also measured identical, §1.4.) | **Med** | **If SWIFT uses message groups/segmentation**, GroupId must be preserved; **measured byte-identical for a genuine group member**. |
| 26 | **MsgSeqNumber** | **copied identically — MEASURED (grouped)** | **[DATA — measured, §1.5, issue #187]** grouped put with `MsgSeqNumber = 7` streamed to `SVCQM` with **`MsgSeqNumber = 7`**. (Single-message case also identical, §1.4.) | **Med** | Group sequencing; **measured identical for a genuine group member** — the group reassembles correctly at the counterparty. |
| 27 | **Offset** | **copied identically — MEASURED** | **[DATA — measured, §1.4/§1.5]** copy `Offset` = original; no accidental segmentation on either probe. Explicit segmentation (multi-segment split) not exercised. | Low–Med | No accidental segmentation observed; the explicit multi-segment case is not exercised. |
| 28 | **MsgFlags** | **copied identically — MEASURED (grouped)** | **[DATA — measured, §1.5, issue #187]** grouped put with `MsgFlags = MQMF_MSG_IN_GROUP (8)` streamed to `SVCQM` with **`MsgFlags = 8` — identical**. (Single-message case also identical, §1.4.) | **Med** | Carries group/segment semantics; **measured identical for a genuine `MQMF_MSG_IN_GROUP` member**. |
| 29 | **OriginalLength** | copied identically *(per aggregate docs)* — **verify** | Meaningful only for segments/report messages; `[DOCS SILENT]` specific. | Low | For a non-segmented datagram this is `MQOL_UNDEFINED`; no counterparty dependence expected. Measure (§5). |
| — | **Message properties / MQRFH2** (named property) | **copied identically — MEASURED (inline RFH2)** | **[DATA — measured, §1.5, issue #187]** An MQRFH2 `<usr><SwiftSettleRef>ABC123XYZ</SwiftSettleRef></usr>` folder (`Format = MQFMT_RF_HEADER_2`) streamed to `SVCQM` with **`Format = 'MQHRF2'`** and the `<usr>…SwiftSettleRef…</usr>` folder **intact in the body**. Corroborates the author blog (**"Message Properties"** identical). | **Med** | Measured survival for the **inline-RFH2** case. The **message-handle / `PROPCTL`-separated** representation is not separately tested — §6 — where a channel or `PROPCTL` could still reshape how a named property appears to the counterparty independently of streaming. |

### Summary of what changes

**Measured (live lab, §1.4 + §1.5): exactly two things change on the streamed
copy — `Expiry` (→ `MQEI_UNLIMITED`, favourable) and `Report` options (stripped).**
Every other measured field — now including grouping and an inline MQRFH2 property
— is copied identically.

- **Altered by streaming (MEASURED):** `Expiry` — original `2589276` (AUDIT
  `CAPEXPRY`) → copy `-1` (`MQEI_UNLIMITED`), the desired direction (§1.4); and
  **`Report` options stripped** — a `MQRO_COA | MQRO_COD` put produced only a
  local COA from our own QM, **zero reports at `SVCQM`** (§1.5, R6).
- **Copied identically (MEASURED):** `MsgId` (byte-identical), `CorrelId`,
  `PutApplName`, `PutApplType`, `PutDate`, `PutTime`, `UserIdentifier`,
  `AccountingToken`, `ApplIdentityData`, `Persistence`, `Priority`, `Encoding`,
  `CodedCharSetId` (`1208`), `Format` (`MQSTR`), `BackoutCount`, `ReplyToQ`,
  `ReplyToQMgr`; the grouping block **`GroupId`, `MsgSeqNumber`, `MsgFlags`**
  (measured byte-identical for a genuine group member, §1.5, R4) and `Offset`;
  and an **inline MQRFH2 named property** (`SwiftSettleRef`, §1.5, R5); plus the
  application payload.
- **Still documentation-only / not exercised:** the **explicit multi-segment**
  (segmented) case, `ApplOriginData`, `OriginalLength`, and the
  **message-handle / `PROPCTL`-separated** property representation (§6 — only the
  inline-RFH2 property case is measured).

---

## 4. Priority risk list (the fields most likely to bite)

1. **Expiry re-imposed by `CAPEXPRY` on the remote/target queue — HIGH ·
   MEASURED benign.** *Altered by design* (→ `MQEI_UNLIMITED`), which is what we
   want; the live probe **measured the copy at `-1` (`MQEI_UNLIMITED`)** — the
   stream target carried **no** `CAPEXPRY` (the `2589276` on the original is the
   AUDIT queue's `CAPEXPRY`, which does not propagate). The residual risk is a
   `CAPEXPRY` later added to the stream target silently re-imposing a finite
   expiry the application never set — a settlement instruction could **expire in
   transit**. *Invariant / action:* declare-and-verify that neither
   `GW.SEC.INSTRUCT.REMOTE` nor whatever it resolves to on the path carries a
   `CAPEXPRY`; treat any non-`MQEI_UNLIMITED` expiry on the copy as a loud
   finding. (Boundary rule: we only *verify* the remote queue's attribute; we do
   not mutate it.)

2. **Origin-context put fields (`PutApplName`, `PutDate`, `PutTime`,
   `PutApplType`) — HIGH · REFUTED (measured identical).** The live probe
   measured all four **copied from the original**, not re-stamped for the
   streaming put (`PutApplName` = `amqsput`, `PutDate` = `20260801`, `PutTime` =
   `20081793`). A SWIFT counterparty that routes, filters, or audits on
   `PutApplName`, or ages on `PutDate/PutTime`, sees the app's true values. The
   highest-value uncertain rows — **resolved.**

3. **Identity context (`UserIdentifier`) fidelity — HIGH · CONFIRMED preserved.**
   The recurring IBM phrasing ("each message has a new message identifier and the
   context fields … are different") **conflates queue streaming with
   publish/subscribe**; the live probe **measured `UserIdentifier` (`mqm`),
   `AccountingToken`, and `ApplIdentityData` identical** on the copy — so for
   `STREAMQ` the identity context is preserved, not re-stamped. A counterparty
   that authorizes on `UserIdentifier` acts on the app's true identity.

4. **Report options stripped — MED · CONFIRMED (live, §1.5).** The copy carries
   **no** Activity/Expiration/Exception/COA/COD bits. The live probe put
   `MQRO_COA | MQRO_COD` and measured **exactly one** report — a **local COA**
   (`Feedback 259`, `PutApplName 'NHAUAPP'`) from **our own QM** — and **zero**
   reports at `SVCQM`. This is correct and expected (it is why #105 requests
   reports on the *original* put), but now **measured**: nothing the counterparty
   leg does emits a report *because of bits on the copy*. Anyone assuming "the
   wire message will trigger a COA at the counterparty" is wrong — measured wrong.
   See R6 in the risk register.

5. **Message groups / segmentation (`GroupId`, `MsgSeqNumber`, `Offset`,
   `MsgFlags`) — MED · CONFIRMED preserved (grouped, §1.5).** The live probe put a
   genuine MQMD v2 group member (`GroupId = 0xAA×24`, `MsgSeqNumber = 7`,
   `MsgFlags = MQMF_MSG_IN_GROUP`) and measured all three **byte-identical** on the
   streamed copy — the group reassembles correctly at the counterparty. The
   remaining unexercised case is **explicit multi-segment** segmentation; measure
   it if segmentation (as distinct from grouping) comes into play.

6. **Message properties / MQRFH2 — MED · CONFIRMED preserved (inline RFH2, §1.5).**
   An inline MQRFH2 `<usr>SwiftSettleRef</usr>` property measured intact on the
   streamed copy (`Format 'MQHRF2'`). The **message-handle / `PROPCTL`-separated**
   representation remains untested — see §6.

---

## 5. Empirical confirmation plan (EXECUTED — issue #108, 2026-08-01; results in §1.4)

> **This plan was run.** The measured diff is summarised in **§1.4** and folded
> into the §3 rows (tagged **MEASURED**). The procedure below is retained as the
> method of record. Outcome: one field differs (`Expiry`, favourably); all others
> measured identical for the single-message probe.

**Goal:** turn every `UNCERTAIN` / "verify" row into a measured result by diffing
the ORIGINAL's MQMD against the STREAMED COPY's MQMD, field by field, for the
*same* instruction (same MsgId).

**Precondition (boundary rule):** this is an authorized run inside the shared lab
box, with `request_reports`/streaming behaviour under our control. Do **not**
mutate the QM or SVCQM; only browse.

### 5.1 Put exactly one instruction

Use `gateway_run` (the #65 requester) to put a **single** securities instruction
through the `GW.SEC.INSTRUCT` QALIAS, so exactly one original and one streamed
copy exist to compare. (One message keeps the diff unambiguous.)

### 5.2 Capture the RETAINED ORIGINAL

`amqsbcg` browses non-destructively and prints the full MQMD:

```sh
# original, on our QM's audit queue
amqsbcg GW.SEC.INSTRUCT.AUDIT <OUR_QM> > /tmp/mqmd-original.txt
```

### 5.3 Capture the STREAMED COPY

**Caveat (important, and honest):** `GW.SEC.INSTRUCT.REMOTE` is a **QREMOTE
definition**, not a local queue — you **cannot** `amqsbcg` it directly. The
streamed copy resolves to a **transmission queue** and is delivered to the
**SVCQM landing queue**. Two accurate ways to see the copy's MQMD:

**Option A — browse the transmission queue on our QM (before it drains).** The
message on the xmit queue is wrapped in an `MQXQH` transmission header; the MQMD
**embedded inside the `MQXQH`** is the streamed copy's descriptor. Find the xmit
queue that our QM resolves `GW.SEC.INSTRUCT.REMOTE` to (its `XMITQ`, or the
`RQMNAME`-derived default), then:

```sh
# find where the QREMOTE points
echo "DISPLAY QREMOTE(GW.SEC.INSTRUCT.REMOTE) RNAME RQMNAME XMITQ" | runmqsc <OUR_QM>
# browse the resolved transmission queue (stop/hold the channel first so it doesn't drain)
amqsbcg <RESOLVED_XMIT_QUEUE> <OUR_QM> > /tmp/mqmd-streamed-xmit.txt
```

**Option B (preferred, cleanest) — capture on the SVCQM landing queue.** This is
the copy exactly as the counterparty receives it, MQXQH already stripped by the
channel:

```sh
# on the SVCQM side, browse the landing queue the responder reads
amqsbcg <SVCQM_LANDING_QUEUE> <SVCQM> > /tmp/mqmd-streamed-landing.txt
```

Prefer Option B for the counterparty-truth diff; use Option A when you need the
copy's MQMD *before* the channel (to separate streaming effects from channel
effects such as `CONVERT`). Capturing **both** also isolates whether any change
is a streaming artefact or a transmission-leg artefact.

### 5.4 Diff the two MQMDs field by field

`amqsbcg` prints each MQMD field on its own labelled line, so a structured diff
is straightforward:

```sh
diff <(grep -E 'MsgId|CorrelId|Persistence|Priority|Expiry|Report|Feedback|Encoding|CodedCharSetId|Format|MsgType|BackoutCount|ReplyToQ|ReplyToQMgr|UserIdentifier|AccountingToken|ApplIdentityData|PutApplType|PutApplName|PutDate|PutTime|ApplOriginData|GroupId|MsgSeqNumber|Offset|MsgFlags|OriginalLength' /tmp/mqmd-original.txt) \
     <(grep -E 'MsgId|CorrelId|Persistence|Priority|Expiry|Report|Feedback|Encoding|CodedCharSetId|Format|MsgType|BackoutCount|ReplyToQ|ReplyToQMgr|UserIdentifier|AccountingToken|ApplIdentityData|PutApplType|PutApplName|PutDate|PutTime|ApplOriginData|GroupId|MsgSeqNumber|Offset|MsgFlags|OriginalLength' /tmp/mqmd-streamed-landing.txt)
```

(For Option A, the embedded MQMD sits inside the `MQXQH`; read the *inner*
descriptor, not the `MQXMIT`-format outer header.)

### 5.5 Turn each row into a result

For every field, record: original value, streamed value, **same/different**, and
promote the table row from `UNCERTAIN`/"verify" to `copied identically` /
`altered` / `reset` with the measured evidence. Pay special attention to the
context block (rows 17–24) and grouping block (rows 25–29). Also verify the
`CAPEXPRY` precondition explicitly:

```sh
echo "DISPLAY QLOCAL(<RESOLVED_STREAM_TARGET>) CAPEXPRY" | runmqsc <QM>
echo "DISPLAY QREMOTE(GW.SEC.INSTRUCT.REMOTE) CAPEXPRY" | runmqsc <OUR_QM>
```

A non-`NOLIMIT` `CAPEXPRY` anywhere on the path is the §4 #1 risk realized —
record it loudly.

### 5.6 What "done" looks like — ACHIEVED (issue #108, 2026-08-01)

Every measurable row in §3 now has a measured value (§1.4); §4 items 1–3 are
resolved (1 benign, 2 refuted, 3 confirmed) and item 5 is partial (single-message
measured identical; grouped case still documentation-only). The single-message
probe leaves only the grouped/segmented case and message properties (§6)
unexercised. **This note is no longer doc-research only — its core claim (one
field differs, favourably) is empirically confirmed.**

---

## 6. Message properties (MQRFH2) — a distinct verification

**[DATA — measured, §1.5, issue #187]** An MQRFH2 message with a
`<usr><SwiftSettleRef>ABC123XYZ</SwiftSettleRef></usr>` folder
(`Format = MQFMT_RF_HEADER_2`) was put through the gateway; the streamed copy on
`SVCQM` carried `Format = 'MQHRF2'` and the `<usr>…SwiftSettleRef…</usr>` folder
**intact in the message body**. So for the **inline-RFH2** case, a named property
**survives streaming** — corroborating the feature author, who lists
**"Message Properties"** among the fields identical between original and copy.

**[JUDGMENT]** The measured case is the property carried **as RFH2 in the message
data**. The **message-handle / `PROPCTL`-separated** representation was **not**
separately tested, and property survival is still a **separate** guarantee from
MQMD fidelity that a channel or `PROPCTL` can reshape **if SWIFT uses named
properties** (e.g. for selectors, routing keys, or business metadata carried
outside the MT body):

- Whether properties travel as an **MQRFH2 in the body** or as **message-handle
  properties** depends on `PROPCTL` on the queues/channel along the path — and
  `PROPCTL` can *reshape* how properties appear to the counterparty (inline RFH2
  vs. handle vs. stripped) **independently of streaming**. A property that is
  "identical" as streamed can still look different to the counterparty after a
  channel applies `PROPCTL`.
- *Action if properties are in use:* extend the §5 diff to dump properties
  (`amqsbcg` shows an RFH2 in the body; for handle properties use a
  property-aware browser or `PROPCTL(FORCE)` to render them inline), and diff
  original vs. landing-queue copy. Verify `PROPCTL` on `GW.SEC.INSTRUCT.AUDIT`,
  the xmit path, and the SVCQM landing queue (declare-and-verify the ones we do
  not own).

If SWIFT does **not** use named properties, this reduces to confirming the MT
body (application data) is byte-identical, which row-level app-data fidelity
(§3) already covers.

---

## 7. Sources

Primary IBM (re-read directly — 403 to automated fetchers):

- Streaming queues configuration (9.3.x):
  https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=queues-streaming-configuration
- Configuring streaming queues (9.4.x):
  https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=configuring-streaming-queues
- Field details for MQMD (9.4.x):
  https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd
- CAPEXPRY — Limit message expiry time (9.4.x):
  https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=objects-capexpry-limit-message-expiry-time

Corroborating (fetchable):

- Matthew Whitehead (streaming-queue feature author), *New Streaming Queue feature
  for MQ 9.2.3*, IBM Community, 2021-07-26 — lists Message ID, Correlation ID,
  Message Properties, Application Data as identical; report options that generate
  new messages removed; expiry → unlimited or CAPEXPRY:
  https://community.ibm.com/community/user/integration/blogs/matthew-whitehead1/2021/07/26/new-streaming-queue-feature-for-mq-923
- Paul Harris, *Streaming queues and message expiry*, IBM Community, 2021-12-22:
  https://community.ibm.com/community/user/blogs/paul-harris1/2021/12/22/streaming-queues-and-message-expiry
- Matthew Whitehead, *Stream queues with CAPEXPRY*, IBM Community, 2022-04-30:
  https://community.ibm.com/community/user/blogs/matthew-whitehead1/2022/04/30/stream-queues-with-capexpry
- Roger Lacroix (Capitalware), *IBM MQ Message Duplication* — documents that
  **pub/sub** duplication yields a **new** MsgId (the conflation source; contrast
  with `STREAMQ`, which preserves MsgId):
  https://www.capitalware.com/rl_blog/?p=6536
- IBM MQ V9.2.3 Streaming Queues Performance Report v1.1 (Dec 2021):
  https://ibm-messaging.github.io/mqperf/MQ%20V9.2.3%20Streaming%20Queues%20Performance%20Report%20V1.1.pdf
- IBM `ibm-messaging/mq-streaming-queues` samples:
  https://github.com/ibm-messaging/mq-streaming-queues

MQMD field reference (for field semantics — Report, context, grouping):

- Types of message / report messages (9.2.x):
  https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message
- Message groups / segmentation (GroupId, MsgSeqNumber, Offset, MsgFlags):
  https://www.ibm.com/docs/en/SSFKSJ_9.3.0/develop/q023060_.html
