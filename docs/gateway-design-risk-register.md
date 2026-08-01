# Streaming-audit gateway — strategic risk register

**Epic:** #105 (repurposed to a strategic-risk documentation epic) · **Issue:** #180
**Status:** living register. **No live queue manager was touched to write it.**

## Purpose

This register is the standing, living record of the **strategic risks in the
streaming-audit gateway design** — the class of problem where *the counterparty
does not receive the message the application put; it receives the queue manager's
streamed derivative of it* — plus the adjacent design risks that the two research
deliverables surfaced. It exists so that a risk is never carried implicitly: each
one is written down with a severity, a status, the concrete empirical check that
resolves it, and why it matters to the counterparty or to the audit guarantee.

It is deliberately **not** a design or a plan. It is the risk ledger those
documents are measured against, and it stays open: `To-validate` rows become
`Confirmed` (or are refuted and rewritten) as the authorized lab run in
[**The unifying validation**](#the-unifying-validation) produces measured evidence.

### The core premise

> The audit mechanism is an MQ-native streaming copy: the app puts one instruction,
> and the queue manager — with **zero application logic** — writes a retained
> **ORIGINAL** to our local audit queue (`GW.SEC.INSTRUCT.AUDIT`) and streams a
> **COPY** onward to the counterparty (via `GW.SEC.INSTRUCT.REMOTE` → SVCQM). The
> two are near-identical *by design*, but "near" is load-bearing. Everything the
> counterparty **authorizes on, routes on, audits on, or expires on** is read from
> the **copy** — so every field where copy ≠ original is a potential
> counterparty-visible behaviour change that the application cannot see and did not
> intend. We reconcile and replay against the original; the counterparty acts on the
> derivative.

IBM documents the streamed copy as a duplicate of the original "including all of
the message descriptor fields, including the message ID and correlation ID," with a
**small, enumerated** set of exceptions (report options stripped; Expiry reset).
The residual risk is concentrated in (a) the fields IBM *tells* us are rewritten and
that a queue attribute can silently re-rewrite, and (b) the fields the docs describe
only in aggregate ("all descriptor fields"), where a recurring IBM phrasing conflates
`STREAMQ` with publish/subscribe and muddies the origin/identity-context guarantee.
See Deliverable B (`docs/mqmd-streaming-fidelity.md`) for the field-by-field account
and Deliverable A (`docs/coa-cod-streaming-findings.md`) for the report-stripping
finding.

### How to read an entry

- **Severity** — High / Med / Low: the counterparty-facing or audit-guarantee impact
  if the risk is real and unmitigated.
- **Status** — `Confirmed` (established by docs/experiment), `To-validate` (docs
  silent or conflicting; must be measured), or `Documented` (understood and recorded,
  no open action).
- **Source** — Deliverable A, Deliverable B, or the primary IBM page.
- Every `To-validate` row names **What to test** — almost always the same lab
  experiment: put one instruction, `amqsbcg` the retained original and the streamed
  copy, and diff the MQMDs field by field (see the closing section).

---

## R1 — Expiry silently re-imposed by `CAPEXPRY` on the stream target

- **Severity:** High · **Status:** To-validate · **Source:** Deliverable B §3 row 5, §4 #1;
  IBM *Streaming queues configuration*; IBM *CAPEXPRY — Limit message expiry time*.
- **Description.** Streaming sets the copy's `Expiry` to `MQEI_UNLIMITED` regardless of
  the original — which satisfies our **no-expiry** requirement for a settlement
  instruction — **unless** a `CAPEXPRY` is set on the stream target
  (`GW.SEC.INSTRUCT.REMOTE` or whatever local queue it resolves to along the path),
  whose value then becomes the copy's expiry. This override is silent: nothing in the
  application path signals it.
- **What to test.** A **config check**, not a message diff:
  `DISPLAY QREMOTE(GW.SEC.INSTRUCT.REMOTE) CAPEXPRY` on our QM and
  `DISPLAY QLOCAL(<resolved stream target / SVCQM landing queue>) CAPEXPRY` on the
  resolving QM. Any value other than `NOLIMIT` is the risk realized. Corroborate by
  reading the copy's `Expiry` on the landing queue (`amqsbcg`) — it must be
  unlimited. Boundary rule: we **verify** the remote/target attribute; we never mutate it.
- **Why it matters.** A settlement instruction could quietly acquire a finite expiry it
  never had and **expire in transit** with no application-visible cause — a silent loss
  of a live instruction, exactly the failure mode the audit design exists to prevent.

## R2 — Origin-context put fields re-stamped on the streamed copy

- **Severity:** High · **Status:** To-validate · **Source:** Deliverable B §3 rows 20–23, §4 #2
  (`[DOCS SILENT]` for `STREAMQ`).
- **Description.** The streamed copy is a **queue-manager-initiated put**. The public docs
  do not state whether the origin-context fields — `PutApplName`, `PutDate`, `PutTime`,
  `PutApplType` — are copied from the original or **re-stamped** for the streaming put.
  The aggregate "all descriptor fields duplicated" statement sits unresolved against the
  new-put reality.
- **What to test.** The lab MQMD diff (original on `GW.SEC.INSTRUCT.AUDIT` vs. copy on the
  SVCQM landing queue): compare `PutApplName` / `PutApplType` for identity, and
  `PutDate` / `PutTime` for whether they reflect the original put instant or the streaming
  put instant.
- **Why it matters.** A SWIFT counterparty that **routes, filters, or audits on
  `PutApplName`**, or **ages instructions on `PutDate`/`PutTime`**, would act on values
  different from what the application produced. Our own `#105` `SENT` metric stamps from the
  *original's* put-time and is safe; the exposure is entirely on the counterparty's side of
  the wire, invisible to us. These are the highest-value uncertain rows — resolve first.

## R3 — Identity-context (`UserIdentifier`) fidelity under the docs conflation

- **Severity:** Med/High · **Status:** To-validate · **Source:** Deliverable B §3 rows 17–19, §4 #3.
- **Description.** The identity-context block (`UserIdentifier`, `AccountingToken`,
  `ApplIdentityData`) *leans* "copied" under the aggregate docs and the feature author's
  blog. But a recurring IBM-adjacent phrasing — *"each message has a new message identifier
  and the context fields of the MQMD are different from those in the original message"* —
  **conflates `STREAMQ` with publish/subscribe** (where the copy genuinely gets a new MsgId
  and fresh context). Because both phrasings coexist in IBM-adjacent material, identity
  context must be **measured, not assumed**.
- **What to test.** The lab MQMD diff: confirm `UserIdentifier` (and the rest of the identity
  block, which moves with it) on the copy equals the original's, rather than the streaming QM's
  identity.
- **Why it matters.** A counterparty that **authorizes or authenticates on the identity
  context** would make an authorization decision on the copy that differs from the
  application's intent — a security-relevant divergence, not merely a cosmetic one.

## R4 — Message groups / segmentation fields under `STREAMQ`

- **Severity:** Med · **Status:** To-validate · **Source:** Deliverable B §3 rows 25–29, §4 #5
  (`[DOCS SILENT]` for `STREAMQ`).
- **Description.** The grouping/segmentation block — `GroupId`, `MsgSeqNumber`, `Offset`,
  `MsgFlags`, `OriginalLength` — falls under "all descriptor fields duplicated," but the docs
  give no `STREAMQ`-specific statement. Streaming operates per *physical* message, so a
  logical group's integrity across the copy is unverified.
- **What to test.** The lab MQMD diff for the grouping fields; and confirm the instruction is
  **not accidentally segmented** on the path (`Offset`/`MsgFlags` on a single-segment datagram).
- **Why it matters.** Conditional: it matters **only if SWIFT uses grouped or segmented
  messages** — but if it does, these fields are **essential**, because a broken `GroupId` or
  sequence reassembles the group wrongly (or not at all) at the counterparty. Low probability,
  high consequence where it applies.

## R5 — MQRFH2 named message-property survival to the counterparty

- **Severity:** Med · **Status:** To-validate · **Source:** Deliverable B §3 (properties row), §6.
- **Description.** The feature author lists **Message Properties** among the fields identical
  between original and streamed copy, so the default expectation is that RFH2 / handle-based
  properties survive streaming. This is a **distinct** guarantee from MQMD fidelity: whether
  properties reach the counterparty as an **inline MQRFH2 in the body** or as **message-handle
  properties** depends on `PROPCTL` on the queues and channel along the path, and `PROPCTL` can
  reshape how properties appear **independently of streaming**.
- **What to test.** Only if **SWIFT uses named properties** (selectors, routing keys, business
  metadata outside the MT body): extend the lab diff to dump properties (`amqsbcg` renders an
  inline RFH2; use a property-aware browser or `PROPCTL(FORCE)` for handle properties), and diff
  original vs. landing-queue copy. Verify `PROPCTL` on `GW.SEC.INSTRUCT.AUDIT`, the xmit path,
  and the SVCQM landing queue (declare-and-verify the ones we do not own). If SWIFT uses no named
  properties, this reduces to confirming the MT body is byte-identical (already covered by
  app-data fidelity).
- **Why it matters.** A property that is "identical" as streamed can still look different to the
  counterparty after a channel applies `PROPCTL` — silently changing selector/routing behaviour
  on values the application set.

## R6 — COA/COD report options stripped from the streamed copy

- **Severity:** High (for the COA/COD transport-telemetry feature) · **Status:** Confirmed (by docs;
  live confirmation pending) · **Source:** Deliverable A (headline finding); IBM *Streaming queues
  configuration*; Deliverable B §3 row 3, §4 #4.
- **Description.** IBM MQ **strips** the report options (Activity, Expiration, Exception, **COA**,
  **COD**) from the streamed copy: *"If any of the following report options are set on the original
  message, they are not enabled on the streamed message."* The retained original keeps its
  `MQRO_COA | MQRO_COD` bits; the copy that travels to SVCQM has them cleared. SVCQM therefore never
  receives a report *request* on the copy it lands — so the `ARRIVED` (SVCQM COA) and `DELIVERED`
  (SVCQM COD) milestones the original telemetry design wanted **are not produced by this topology**.
  This is the finding that ended the original COA/COD-on-the-streamed-put idea; it is recorded here
  as a **design risk of the streaming mechanism itself**, not merely a feature footnote.
- **What to test.** The live experiment in Deliverable A Track 2 (still pending an authorized run):
  put **one** instruction with `request_reports` enabled and browse `GW.SEC.REPORT`; expect **only**
  a single local COA (our QM, ~t0, a `SENT`-adjacent local artifact) and **neither** the SVCQM
  `ARRIVED` COA nor the `DELIVERED` COD. Discriminate origin by generating QM and put-time.
- **Why it matters.** Any design that assumes "the wire message will trigger a COA/COD at the
  counterparty" is wrong under streaming. If `ARRIVED`/`DELIVERED` are still wanted, the report
  request must ride a message path that is **not** the streamed copy — a different architecture
  than the single-put-through-the-QALIAS, needing its own spec change.

## R7 — The single MQMD `ReplyToQ` is committed to SWIFT response routing

- **Severity:** Med · **Status:** Confirmed · **Source:** Deliverable A §3; Deliverable B §3 rows 15–16.
- **Description.** There is exactly **one** `ReplyToQ` / `ReplyToQMgr` pair in the MQMD, and it is
  committed to the counterparty's **business** response routing (the MT548 status advice path back to
  our QM). It therefore **cannot** be repurposed to also carry a separate report / observability reply
  address on the same message — a report ledger has to be a queue we own and route to independently
  (`GW.SEC.REPORT`, evaluated only on arrival back at our QM), not a second use of the one `ReplyToQ`.
- **What to test.** Confirm on the lab diff that the copy's `ReplyToQ`/`ReplyToQMgr` carry the values
  the business (MT548) reply path expects and resolve to our QM. (No separate report reply can share
  the field.)
- **Why it matters.** It bounds the design: any report/telemetry return path must be provisioned as a
  separately-owned queue with its own routing, because the one MQMD reply slot is already spent on
  SWIFT responses. Overloading it would break business reply routing.

## R8 — `MsgId` preservation — the load-bearing correlation/replay guarantee

- **Severity:** High · **Status:** To-validate (docs affirm; conflation demands measurement) ·
  **Source:** Deliverable B §3 row 12, §4 #3.
- **Description.** IBM's streaming docs state **explicitly** that the duplicate includes "the message
  ID and correlation ID," and `MsgId` equality is the single guarantee the whole design rests on:
  correlation (`report.CorrelId → instruction MsgId → SEME`), reconciliation, and **replay** all assume
  original and copy share a `MsgId`. The same pub/sub-vs-`STREAMQ` conflation that clouds R3 ("each
  message has a new message identifier") means this must not be left merely assumed.
- **What to test.** The lab MQMD diff: `MsgId` on the retained original must byte-equal `MsgId` on the
  streamed copy at the landing queue. `CorrelId` likewise.
- **Why it matters.** If `MsgId` were *not* preserved, the audit/replay premise and `#105` correlation
  break entirely — this is the one row where the risk is "the foundation is false." Measuring it is
  cheap insurance on the design's keystone. (Recorded as a risk-to-confirm precisely because it is
  load-bearing, not because the docs doubt it.)

## R9 — CCSID / `CONVERT` transcoding on the transmission leg

- **Severity:** Med · **Status:** Documented (transmission-leg property, not a streaming artifact) ·
  **Source:** Deliverable B §3 rows 7–9 (`Encoding`, `CodedCharSetId`, `Format`).
- **Description.** Streaming performs **no** transcoding: the copy's `CodedCharSetId`/`Encoding` equal
  the original's before the channel touches it. However, a channel with `CONVERT(YES)` will transcode
  SWIFT MT text on the **transmission** leg — the same as for any normal remote put. This is **not** a
  streaming artifact, but it is a real counterparty-visible difference between what the app put and what
  the counterparty reads, so it belongs in the register for completeness.
- **What to test.** Two-point capture in the lab diff: `amqsbcg` the copy on the **xmit queue** (Option
  A, inside the `MQXQH`) *and* on the **SVCQM landing queue** (Option B). Equal CCSID at the copy pre-
  channel isolates streaming from channel effects; any change between the two points is a `CONVERT`
  effect, not streaming. Declare-and-verify channel `CONVERT`/CCSID on the path.
- **Why it matters.** SWIFT MT text is charset-sensitive; an unexpected transcode can corrupt or subtly
  alter the instruction body the counterparty parses. Attributing any change correctly (streaming vs.
  channel) prevents chasing the wrong layer.

## R10 — Persistence inherited from the original, not the target queue default

- **Severity:** Med · **Status:** Documented (favourable, verify) · **Source:** Deliverable B §3 rows 10–11.
- **Description.** IBM's streaming clause states the secondary queue's `DEFPSIST`/`DEFPRTY` have **no
  effect** on the streamed copy — so both `Persistence` and `Priority` come from the **original's** MQMD,
  not the stream-target queue default. This is favourable (no surprise downgrade from the target queue),
  but it makes correct application behaviour the sole guarantor of a persistent copy.
- **What to test.** The lab MQMD diff: confirm the copy is `Persistence = MQPER_PERSISTENT`, i.e. that the
  app/QALIAS actually yields a persistent put; and that `Priority` matches the original.
- **Why it matters.** Settlement instructions must be **persistent** — a non-persistent copy could be lost
  on a counterparty restart. The inheritance rule means the risk lives entirely in whether the application
  puts persistent, not in the target queue — so the check is on our own put, and it is a real one.

---

## The unifying validation

Most of these risks resolve with **one authorized lab experiment**:

1. **Put exactly one instruction** through the `GW.SEC.INSTRUCT` QALIAS (via the `#65`
   `gateway_run` requester), so precisely one retained original and one streamed copy exist
   to compare. One message keeps the diff unambiguous.
2. **Capture the retained original.** `amqsbcg GW.SEC.INSTRUCT.AUDIT <OUR_QM>` — a
   non-destructive browse that prints the full MQMD.
3. **Capture the streamed copy.** **Caveat:** `GW.SEC.INSTRUCT.REMOTE` is a **QREMOTE
   definition, not a local queue — you cannot `amqsbcg` it directly.** The copy resolves to a
   transmission queue and is delivered to the SVCQM landing queue. Capture it either on the
   **xmit queue** on our QM (the MQMD sits *inside* the `MQXQH` transmission header — read the
   inner descriptor) or, preferred, on the **SVCQM landing queue** (the copy exactly as the
   counterparty receives it, `MQXQH` already stripped). Capturing both isolates streaming
   effects from transmission-leg (`CONVERT`) effects.
4. **Diff the two MQMDs field by field** and promote each `To-validate` row to a measured
   `copied identically` / `altered` / `reset` result: R2 (origin context), R3 (identity
   context), R4 (grouping), R8 (`MsgId`/`CorrelId`), R10 (persistence/priority) all fall out of
   this single diff. R5 (properties) extends the diff to RFH2/`PROPCTL` if SWIFT uses named
   properties.
5. **Add the two config checks** that are not message diffs: R1 (`CAPEXPRY` on the stream-target
   path — the loudest finding if non-`NOLIMIT`) and R6/R7 (the COA/COD report experiment and the
   `ReplyToQ` inspection from Deliverable A).

Boundary rule throughout: this is an **authorized browse-only** run inside the shared lab box —
**do not mutate the QM or SVCQM**; only browse and `DISPLAY`. A failed precondition or a
non-default attribute is a **loud finding, never a silent default**. Until that run produces
measured evidence, every `To-validate` row above remains a documented-but-open risk.
