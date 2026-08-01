# MQMD fidelity across the streaming-audit copy — original vs. streamed

**Status:** documentation + web research. **No live queue manager was touched.**
Every row marked `UNCERTAIN` or `[DOCS SILENT]` must be turned into a measured
result by the empirical plan in §5 before this note is treated as final.

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

---

## 2. How to read the table

- **Status** — one of: `copied identically` · `altered` · `reset` · `UNCERTAIN`.
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
| 3 | **Report** | **altered (reset)** | **[DATA]** Streaming doc + author blog: *"report options that cause the queue manager to generate new messages are removed"* (Activity, Expiration, Exception, COA, COD **not enabled** on the copy). | **Med** | If SWIFT (or we) rely on COA/COD/exception reports being triggered *by the copy on SVCQM*, they still fire — because SVCQM re-reads the report bits from the copy. **But those bits are stripped, so no reports are requested by the streamed leg.** This is the crux of the #105 spike: reports must be requested on the *original put*, and the counterparty leg carries none. |
| 4 | **MsgType** (`MQMT_DATAGRAM`) | copied identically | `[DOCS SILENT]` on MsgType specifically; falls under "all descriptor fields duplicated". | Low | Counterparty expects a datagram; unchanged. Confirm empirically (§5). |
| 5 | **Expiry** | **altered** | **[DATA]** Streaming doc: set to **`MQEI_UNLIMITED`** regardless of the original — *unless* the secondary/stream target queue has **`CAPEXPRY`**, whose value then becomes the copy's expiry. (CAPEXPRY doc; Harris blog.) | **HIGH** | Our requirement is **no expiry**; UNLIMITED is what we want. **The risk is a `CAPEXPRY` set on `GW.SEC.INSTRUCT.REMOTE` (or the resolved target) silently re-imposing an expiry on the copy the app never asked for** — a settlement instruction could quietly expire in transit. See §4 #1. |
| 6 | **Feedback** | copied identically | `[DOCS SILENT]`; datagram carries `MQFB_NONE`. Reports (not this message) carry feedback. | None | Instruction is not a report; Feedback is `NONE` on both. |
| 7 | **Encoding** | copied identically | Author blog: application data identical ⇒ its numeric encoding must match. | Low | If SWIFT parses numeric fields by Encoding, it must match — and does. Verify (§5). |
| 8 | **CodedCharSetId** (CCSID) | copied identically | Author blog: app data + descriptor duplicated. **[JUDGMENT]** No transcoding is performed by streaming. | **Med** | SWIFT MT text is charset-sensitive. Any channel `CONVERT(YES)` transcoding happens on the *transmission* leg (same as for a normal remote put) and is **not** a streaming artefact — but confirm the copy's CCSID equals the original's before the channel touches it (§5). |
| 9 | **Format** (e.g. `MQSTR`) | copied identically | "All descriptor fields duplicated." | Low | Counterparty dispatches on Format; unchanged. Note: on the xmit queue the message is wrapped in an `MQXQH` (Format `MQXMIT`); that is the transmission envelope, not a change to the copy's own MQMD (§5 caveat). |
| 10 | **Priority** | copied identically | **[DATA]** Streaming doc: **`DEFPSIST` and `DEFPRTY` of the secondary queue have no effect** on the streamed message ⇒ priority comes from the **original's** MQMD, not the target queue default. | Low | Priority ordering preserved end-to-end from what the app set. No surprise from the target queue's `DEFPRTY`. |
| 11 | **Persistence** | copied identically | **[DATA]** Same clause: `DEFPSIST` of the secondary queue does not affect the copy ⇒ persistence is the **original's**. | **Med** | Settlement instructions must be **persistent**; a non-persistent copy could be lost on restart. Good news: it inherits the original's persistence, **not** the target queue default — so as long as the app puts persistent, the copy is persistent. Verify the app/QALIAS actually yields persistent (§5). |
| 12 | **MsgId** | copied identically | **[DATA]** Streaming doc **explicitly**: the duplicate includes *"the message ID and correlation ID."* Author blog lists Message ID as identical. | **HIGH** (as an asset) | This is the load-bearing guarantee for #105's correlation (`report.CorrelId → instruction MsgId → SEME`) **and** for replay. If it were *not* preserved, the whole audit/replay premise breaks. Counterparty may also dedupe on MsgId. **Confirm empirically — a single doc phrasing elsewhere ("each message has a new message identifier") conflates streaming with pub/sub (§4 #3); do not let that go unmeasured.** |
| 13 | **CorrelId** | copied identically | **[DATA]** Same explicit clause + author blog. | **Med** | If SWIFT threads on CorrelId, it must survive; docs say it does. Verify (§5). |
| 14 | **BackoutCount** | **reset (→ 0)** | `[DOCS SILENT]` for `STREAMQ` specifically. **[JUDGMENT]/[DATA]** BackoutCount is QM-maintained and is `0` on any freshly-put message; the streamed copy is a fresh put that has never been backed out, so it is `0` — and the original's own BackoutCount (also `0` unless it has been backed out on the audit queue) does not carry meaning to the counterparty. | Low | Counterparty's own consumer increments *its* BackoutCount on *its* queue; our copy starts clean at `0`, which is correct. Flag only because "0" is an assumption, not an IBM statement — measure it (§5). |
| 15 | **ReplyToQ** | copied identically | "All descriptor fields duplicated"; author blog (descriptor duplicated). | **Med** | If the app sets `ReplyToQ = GW.SEC.REPORT` (for #105 reports) it is copied — but since report *options* are stripped (row 3), SVCQM has a ReplyToQ but no trigger to use it on the streamed leg. Ensure ReplyToQ/ReplyToQMgr semantics are what the counterparty expects for any *business* reply (MT548). Verify (§5). |
| 16 | **ReplyToQMgr** | copied identically | Same as ReplyToQ. QM name is normally stamped at put time from the original context. | **Med** | Business replies (MT548) route back on `ReplyToQMgr`; must resolve to our QM. This is the mechanism #105 §2 relies on. Confirm the copy carries our QM name (§5). |
| 17 | **UserIdentifier** | copied identically *(per aggregate docs)* — **verify** | **[DATA]** Falls under "all descriptor fields duplicated" and the author blog's silence on any context change. `[DOCS SILENT]` on context fields *specifically* for `STREAMQ`; a competing IBM phrasing says "context fields … are different" (§4 #3). | **HIGH** | **A SWIFT counterparty may authorize/authenticate on the identity context (`UserIdentifier`).** If streaming re-stamped it to the streaming QM's identity, an authorization decision on the copy would differ from the app's intent. Docs *lean* copied, but the context-conflation makes this a **must-measure** row. |
| 18 | **AccountingToken** | copied identically *(per aggregate docs)* — **verify** | Same basis as UserIdentifier; identity context. `[DOCS SILENT]` specific to `STREAMQ`. | Low–Med | Rarely a counterparty authorization input, but part of identity context; measure alongside #17 since they move together. |
| 19 | **ApplIdentityData** | copied identically *(per aggregate docs)* — **verify** | Same identity-context basis; `[DOCS SILENT]` specific. | Low–Med | Free-form identity string; if SWIFT keys on it, it must survive. Measure with the context block (§5). |
| 20 | **PutApplType** | **UNCERTAIN** | `[DOCS SILENT]` for `STREAMQ`. Origin context. The streamed message is a **queue-manager-initiated put**, so PutApplType *could* be re-stamped (e.g. to a QM/channel type) rather than reflecting the original application. Aggregate docs say "all fields duplicated"; not independently confirmed. | **Med** | If the counterparty routes/audits by *who* put the message, a re-stamped origin type would mislead. Genuinely uncertain — **measure (§5).** |
| 21 | **PutApplName** | **UNCERTAIN** | `[DOCS SILENT]` for `STREAMQ`; origin context; same QM-initiated-put concern as PutApplType. | **HIGH** | **A SWIFT counterparty commonly routes, filters, or audits on `PutApplName`.** If streaming re-stamps it (to the QM name, a channel, or blanks), counterparty routing/audit sees a different originator than the app. This is one of the highest-value UNCERTAIN rows — **measure first (§5).** |
| 22 | **PutDate** | **UNCERTAIN** | `[DOCS SILENT]` for `STREAMQ`; origin context. Duplicate is a new put → PutDate might reflect the streaming put instant, or be copied from the original. Aggregate "all fields" statement vs. new-put reality unresolved. | **Med–High** | #105 stamps `SENT` from the **original's** put-time on the audit queue, so *our* metric is safe. **But** if the counterparty timestamps/ages instructions by the copy's `PutDate/PutTime`, and streaming re-stamps them, their aging differs from ours — and any cross-clock reasoning shifts. Measure (§5). |
| 23 | **PutTime** | **UNCERTAIN** | Same as PutDate. | **Med–High** | Pairs with PutDate; settlement SLAs and "stale instruction" logic on the counterparty side may read it. Measure (§5). |
| 24 | **ApplOriginData** | copied identically *(per aggregate docs)* — **verify** | Origin context; `[DOCS SILENT]` specific. | Low | Free-form origin string; low counterparty dependence, but measure with the origin block. |
| 25 | **GroupId** | copied identically *(per aggregate docs)* — **verify** | `[DOCS SILENT]` specific to `STREAMQ`; grouping fields are ordinary MQMD fields ⇒ under "all fields duplicated". | **Med** | **If SWIFT uses message groups/segmentation**, GroupId must be preserved or the group breaks at the counterparty. Streaming operates per *physical* message; measure that GroupId survives (§5, and §4 #5). |
| 26 | **MsgSeqNumber** | copied identically *(per aggregate docs)* — **verify** | Same grouping basis; `[DOCS SILENT]` specific. | **Med** | Group sequencing; only matters if groups are used, but then it is essential. Measure (§5). |
| 27 | **Offset** | copied identically *(per aggregate docs)* — **verify** | Same; segmentation. `[DOCS SILENT]` specific. | Low–Med | Only relevant under segmentation (not on z/OS). Our instructions are single-segment; confirm no accidental segmentation (§5). |
| 28 | **MsgFlags** | copied identically *(per aggregate docs)* — **verify** | Grouping/segmentation flags (`MQMF_*`); `[DOCS SILENT]` specific. | **Med** | Carries "in group / last in group / segment" semantics; must match GroupId/seq. Measure with the grouping block (§5). |
| 29 | **OriginalLength** | copied identically *(per aggregate docs)* — **verify** | Meaningful only for segments/report messages; `[DOCS SILENT]` specific. | Low | For a non-segmented datagram this is `MQOL_UNDEFINED`; no counterparty dependence expected. Measure (§5). |
| — | **Message properties / MQRFH2** (message handle props) | copied identically | **[DATA]** Author blog lists **"Message Properties"** as identical between original and copy. | **Med** | See §6 — if SWIFT uses **named properties** (selectors, routing), their survival through streaming is a **distinct** guarantee worth its own verification, especially where a channel or `PROPCTL` could reshape RFH2. |

### Summary of what changes

- **Altered / reset by streaming (documented):** `Report` (options stripped),
  `Expiry` (→ `MQEI_UNLIMITED`, or `CAPEXPRY`), `BackoutCount` (→ 0, reasoned not
  cited).
- **Explicitly preserved (documented):** `MsgId`, `CorrelId`, message properties,
  application data; and — via the "`DEFPSIST`/`DEFPRTY` have no effect" clause —
  `Persistence` and `Priority` come from the **original**, not the target queue.
- **UNCERTAIN (docs silent for `STREAMQ`; measure):** origin/identity **context**
  block — `PutApplType`, `PutApplName`, `PutDate`, `PutTime` (highest concern),
  and by association `UserIdentifier`, `AccountingToken`, `ApplIdentityData`,
  `ApplOriginData`; plus the **grouping/segmentation** block (`GroupId`,
  `MsgSeqNumber`, `Offset`, `MsgFlags`, `OriginalLength`).

---

## 4. Priority risk list (the fields most likely to bite)

1. **Expiry re-imposed by `CAPEXPRY` on the remote/target queue — HIGH.**
   *Altered by design* (→ `MQEI_UNLIMITED`), which is what we want, **but** a
   `CAPEXPRY` on the stream target silently overrides it and gives the copy a
   finite expiry the application never set. A settlement instruction could
   **expire in transit** with no application-visible cause. *Action:* declare-and-
   verify that neither `GW.SEC.INSTRUCT.REMOTE` nor whatever it resolves to on the
   path carries a `CAPEXPRY`; treat any non-`MQEI_UNLIMITED` expiry on the copy as
   a loud finding, never a silent default. (Boundary rule: we only *verify* the
   remote queue's attribute; we do not mutate it.)

2. **Origin-context put fields (`PutApplName`, `PutDate`, `PutTime`,
   `PutApplType`) — HIGH/UNCERTAIN.** The copy is a queue-manager-initiated put,
   and the public docs do not state whether these are copied from the original or
   re-stamped for the streaming put. A SWIFT counterparty that **routes, filters,
   or audits on `PutApplName`**, or **ages instructions on `PutDate/PutTime`**,
   would see values that differ from what the app produced if these are
   re-stamped. Highest-value UNCERTAIN rows — resolve empirically first.

3. **Identity context (`UserIdentifier`) fidelity — HIGH/verify.** Docs *lean*
   "copied," but a recurring IBM phrasing ("each message has a new message
   identifier and the context fields of the MQMD are different from those in the
   original message") **conflates queue streaming with publish/subscribe / a new
   put** — precisely the ambiguity flagged in the task. In pub/sub the copy *does*
   get a new MsgId (Capitalware/Lacroix confirms this for the pub/sub case); for
   `STREAMQ` the authoritative statement and the feature author both say MsgId and
   context are preserved. **Because the two phrasings coexist in IBM-adjacent
   material, the identity context must be *measured*, not assumed** — especially
   if the counterparty authorizes on `UserIdentifier`.

4. **Report options stripped — MED (known, by design).** The copy carries **no**
   Activity/Expiration/Exception/COA/COD bits. This is correct and expected (it is
   why #105 requests reports on the *original* put), but it means: nothing the
   counterparty leg does will emit a report *because of bits on the copy*. Anyone
   assuming "the wire message will trigger a COA" is wrong — it will not, unless
   the original put set them and they survived, which by this rule they do **not**.

5. **Message groups / segmentation (`GroupId`, `MsgSeqNumber`, `Offset`,
   `MsgFlags`) — MED/verify.** If SWIFT ever uses grouped or segmented messages,
   these must survive streaming intact or the group reassembles wrongly (or not at
   all) at the counterparty. Docs are silent on the `STREAMQ`-specific handling of
   the grouping block; measure it if groups are in play. (Our instructions are
   expected single-message; confirm no accidental segmentation.)

6. **Message properties / MQRFH2 — MED (distinct question).** See §6.

---

## 5. Empirical confirmation plan (authorized lab run — NOT done here)

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

### 5.6 What "done" looks like

Every row in §3 has a measured value; §4 items 1–3, 5 are confirmed or refuted
with amqsbcg output attached; the `report-ready` gate for this note can then be
run. **Until then this note is doc-research only.**

---

## 6. Message properties (MQRFH2) — a distinct verification

**[DATA]** The feature author lists **"Message Properties"** among the fields
identical between original and streamed copy, so the *default* expectation is
that RFH2/handle-based properties survive streaming.

**[JUDGMENT]** This is nonetheless a **separate** guarantee from MQMD fidelity and
deserves its own check **if SWIFT uses named properties** (e.g. for selectors,
routing keys, or business metadata carried outside the MT body):

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
