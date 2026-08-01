# COA/COD × streaming-queue gating spike — findings

**Epic:** #105 (COA/COD transport-milestone telemetry, now repurposed into a
risk-documentation epic) · **Task 3** (gating spike, spec §4)
**Status:** **COMPLETE — definitive NO-GO.** Track 1 (documentation research) is
conclusive on its own: **two independent showstoppers** each block the design. Track 2
(a live experiment) would confirm the streaming-strip showstopper empirically but is
**not required** for the conclusion. A well-characterised negative result is the
accepted terminal outcome for this spike (spec §4, §13.2).

## Purpose & topology

Epic #105 wants MQ-native COA (Confirmation On Arrival) and COD (Confirmation On
Delivery) reports to expose two hidden transport milestones — `ARRIVED` (arrived on
the counterparty's landing queue) and `DELIVERED` (destructively consumed by the
responder) — decomposing the opaque `instructed→acknowledged` leg. This spike
resolves the one genuinely undocumented-in-our-experience interaction: **what
COA/COD do when the destination queue carries a streaming-queue attribute.** The
outbound path is:

```
app PUT ─▶ GW.SEC.INSTRUCT (QALIAS)
             └─resolves to▶ GW.SEC.INSTRUCT.AUDIT (QLOCAL, STREAMQ … STRMQOS(MUSTDUP)) ── the retained ORIGINAL, on OUR QM
                              └─streamed MUSTDUP copy▶ GW.SEC.INSTRUCT.REMOTE (QREMOTE) ─▶ xmit queue ─▶ SVCQM landing queue ─▶ responder destructive GET
```

The app sets `MQRO_COA | MQRO_COD` (plain), `ReplyToQ = GW.SEC.REPORT`,
`ReplyToQMgr = <our QM>` on the single put. In streaming-queue terms the message put
to `GW.SEC.INSTRUCT.AUDIT` is the **original**; the copy delivered to
`GW.SEC.INSTRUCT.REMOTE` (and onward to SVCQM) is the **streamed message**.

---

## Track 1 — documentation findings

Sourced from public IBM MQ documentation. Each answer separates **[DATA]** (what the
docs state) from **[JUDGMENT]** (our reasoning on top). Where the docs do not address
the interaction, that is called out explicitly as **[DOCS SILENT]** rather than
inferred as if documented.

### Showstopper 1 (the "headline finding") — report options are stripped from the streamed copy

**[DATA]** The IBM MQ *Streaming queues configuration* page states that the streamed
copy is a near-duplicate of the original "including all of the message descriptor
fields, including the message ID and correlation ID," but then lists exceptions.
Specifically: *"If any of the following report options are set on the original
message, they are not enabled on the streamed message,"* and the enumerated options
are **Activity reports, Expiration reports, Exception reports, Confirmation on arrival
(COA), and Confirmation on delivery (COD)."*
Source: https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=queues-streaming-configuration
(mirrored at the 9.2.x and 9.4.x equivalents of the same topic).

**[JUDGMENT]** This is the load-bearing result for the whole epic. The **retained
original** on `GW.SEC.INSTRUCT.AUDIT` keeps its `MQRO_COA | MQRO_COD` bits; the
**streamed copy** that travels to SVCQM has those bits cleared by the queue manager.
So SVCQM never receives a report *request* on the copy it lands — which means the two
milestones the epic actually wants (`ARRIVED` from an SVCQM COA, `DELIVERED` from an
SVCQM COD) are **not** produced by this topology as designed. Track 2 must confirm
this empirically, but the documentation points clearly at it.

### Showstopper 2 (independent) — the single `ReplyToQ` is already committed to SWIFT response routing

This showstopper was identified after the streaming-strip finding above. It is
**independent** of showstopper 1: it would block the design even if streaming did not
strip the report options.

**[DATA]** An MQMD carries exactly **one** `ReplyToQ` / `ReplyToQMgr` pair, and every
report message a queue manager generates — COA, COD, exception, expiration — is routed
to that one pair. There is no per-report-type reply destination: the queue manager has
nowhere but `ReplyToQ`/`ReplyToQMgr` to send a COA or COD.
Source: https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd
· *Types of message* (report routing): https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message

**[DATA — context, not IBM docs]** In this gateway the SWIFT counterparty
application requirement **mandates** that outbound instructions specify
`ReplyToQ`/`ReplyToQMgr`, and the counterparty uses that pair to route its
(long-lived) **business responses** — MT548 status messages and the like — back to us.
`ReplyToQ` is therefore already committed to the SWIFT **response** queue
`GW.SEC.REPLY`. This is an application/counterparty convention in this context, **not**
an MQ law — but it holds firmly here and is not ours to change (parasite/boundary rule).

**[JUDGMENT]** Because there is only one `ReplyToQ` and it is already bound to
business-response routing, a **dedicated report queue (`GW.SEC.REPORT`) is
infeasible**. Any COA/COD that fired would be routed to `ReplyToQ` = `GW.SEC.REPLY` —
the application's business-response queue — so the app would be exposed to report
messages and would have to recognise and handle them alongside genuine MT548 responses.
Even the single *useless* local COA that showstopper 1 leaves intact (our QM,
~`SENT` time, a local artifact of no milestone value) would land on `GW.SEC.REPLY`,
polluting the business queue for zero value. There is no non-intrusive way to isolate
transport reports from the app's response stream under a single `ReplyToQ`. This does
not change the verdict — showstopper 1 already blocks the outbound SVCQM reports — but
it independently rules out the "just add a dedicated report queue" mitigation.

### 1. Is a COA generated for arrival on the LOCAL audit queue (the retained original on our QM)?

**[DATA]** The QALIAS `GW.SEC.INSTRUCT` resolves its `BASEQ` to the local queue
`GW.SEC.INSTRUCT.AUDIT`, so that local queue is the **resolved destination** of the
app's put. Per the MQMD field details, a COA report "indicates that the message has
reached its target queue, and is generated by the queue manager." When the put is in a
unit of work and the destination is a local queue, "the COA report message generated
by the queue manager can be retrieved only if the unit of work is committed."
Source: https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd
and *Types of message*: https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message

**[DATA — DOCS SILENT on the exact combination]** No IBM page we found states whether
enabling `STREAMQ` on the destination local queue changes COA generation *for the
original message that lands on that queue*. The streaming docs govern the *streamed
copy*, not the original. On "original-message COA when the local destination happens
to carry a STREAMQ attribute," the docs are silent.

**[JUDGMENT]** The original message is a normal local-queue arrival that retains its
report options, so we expect **one COA, generated by OUR QM**, on the original landing
in `GW.SEC.INSTRUCT.AUDIT` (retrievable after the put's UOW commits). This is a
`SENT`-adjacent *local artifact*, not the `ARRIVED` milestone.

### 2. Does the streamed MUSTDUP copy on the QREMOTE/xmit queue get its COA suppressed under the transmission-queue exclusion until it lands on SVCQM's local queue?

**[DATA]** A COA report "is not generated if the `Format` field in the message
descriptor is `MQFMT_XMIT_Q_HEADER` or `MQFMT_DEAD_LETTER_HEADER`," which "prevents a
COA report being generated if the message is put on a transmission queue, or is
undeliverable and put on a dead-letter queue."
Source: https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd

**[JUDGMENT]** The transmission-queue exclusion is the *normal* mechanism that would
defer a COA past the xmit queue so it fires on the remote QM's local landing queue.
**But here it is moot**: per the headline finding, the streamed copy has already had
its COA/COD options stripped by the streaming queue manager, so there is no report
request to defer. The copy that reaches SVCQM's landing queue carries **no**
`MQRO_COA`, so no `ARRIVED` COA is generated there — not because of the xmit exclusion,
but because the request bit is simply absent on the copy.

### 3. Do the MQMD Report / ReplyToQ / ReplyToQMgr fields propagate onto the MUSTDUP duplicate unchanged, so the remote QM (SVCQM) sees the request?

**[DATA]** The **report options** in the `Report` field (COA, COD, exception,
expiration, activity) are explicitly **not** enabled on the streamed copy (headline
finding, streaming-configuration page). The docs describe the copy as retaining the
message descriptor "including the message ID and correlation ID," and note "there are
some message descriptor fields that are not retained on the streamed message" without,
in the excerpts we could retrieve, enumerating every field.
Source: https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=queues-streaming-configuration

**[DOCS SILENT]** We did **not** find an IBM statement that separately confirms or
denies whether the `ReplyToQ` / `ReplyToQMgr` *character* fields are physically copied
onto the streamed message. On that precise point the documentation is silent.

**[JUDGMENT]** The question is largely moot for the epic: with the COA/COD *option
bits* stripped, SVCQM has no reason to generate a report regardless of whether
`ReplyToQ`/`ReplyToQMgr` survive — a report is only produced when a report option is
set. So SVCQM "does not see the request" in the sense that matters. `MsgId` *is*
copied, so **if** a report were ever generated from the copy it would still correlate
(`report.CorrelId → instruction MsgId`); but under this topology no such report is
expected.

### 4. Anything on how streaming queues interact with report generation at all?

**[DATA]** The only direct statement we found is the streaming-configuration rule that
COA, COD, exception, expiration, and activity report options set on the original are
not enabled on the streamed copy (headline finding). Related streaming restrictions
(no chains/loops of streaming queues; `STREAMQ` not allowed on `USAGE(XMITQ)`;
subscription-destination restrictions) are documented but are not about report
generation.
Sources: https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=queues-streaming-configuration
· https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=queues-streaming-queue-restrictions

**[DOCS SILENT]** Beyond "report options are not enabled on the streamed copy," IBM's
docs do **not** discuss the end-to-end interaction of streaming with a two-QM
(remote-QM) COA/COD round trip. The niche combination this epic exercises — original
retained locally, copy streamed to a QREMOTE then a remote QM, reports requested on
the put — is **not** described anywhere we located. Treat any behaviour beyond the
single cited rule as unverified until Track 2.

---

## Opening hypothesis (from spec §4 — to be confirmed/refuted by Track 2)

> Because the streamed copy shares the `MsgId`, expect **a COA per arrival** — one on
> the local audit queue (`GW.SEC.INSTRUCT.AUDIT`, ~t0) and one on SVCQM's landing
> queue (t1) — plus one COD on the responder's destructive get.

**Track 1 assessment of the hypothesis (judgment, pending live confirmation):** the
documentation **predicts the hypothesis will be partially refuted.** The stripping of
report options from the streamed copy means we expect to observe **only the local COA
(our QM, ~t0)** and **neither the SVCQM `ARRIVED` COA nor the `DELIVERED` COD**. In
other words, the streaming topology likely prevents COA/COD from composing into the
`ARRIVED`/`DELIVERED` milestones the way the hypothesis assumed. This is exactly the
kind of well-characterised negative result the spike is allowed to terminate on — see
the definitive NO-GO conclusion below. Combined with the independent `ReplyToQ`
collision (showstopper 2), the spike terminates NO-GO without a live run; Track 2 would
only turn this documented prediction into an observed fact, and is not required.

---

## Track 2 — live experiment procedure (OPTIONAL — not required for the conclusion)

**Not required.** The no-go conclusion stands on Track 1 alone. This procedure is
retained only so that, if a human ever wants the streaming-strip (showstopper 1)
confirmed empirically, the steps exist. It confirms **only** showstopper 1; showstopper
2 (the shared `ReplyToQ`) is a structural fact that no live run can change.

**Caveat from showstopper 2.** In production `ReplyToQ` is bound to `GW.SEC.REPLY`, so
the `GW.SEC.REPORT` queue in step 1 could never actually receive production reports. Any
Track 2 run must therefore point `ReplyToQ` at a **throwaway** report queue purely to
observe streaming-strip behaviour in isolation — it does not represent a shippable
routing.

**Do not run this without authorization: it touches the shared live lab (SVCQM and the
inter-QM link are declare-and-verify, never mutated).** Track 1 does not run it.

**Precondition (loud, not silent):** verify the #65 round-trip still works end-to-end
(`gateway_run` → QALIAS → SVCQM → responder → replies) before enabling reports. A
missing round-trip is a precondition failure to surface, not to work around.

Steps:

1. **Provision the report queue we own.** Deploy the `gw-sec-queues` role to create
   `GW.SEC.REPORT` (terminal QLOCAL, CAPEXPRY on the SEC horizon). Confirm it did not
   touch the QM or SVCQM (config diff = the transparency proof, spec §11). Confirm
   `GW.SEC.REPORT` is empty before the run.
2. **Send exactly ONE instruction with reports enabled.** Run the gateway requester
   for a single instruction with `request_reports` enabled, so the put sets
   `MQRO_COA | MQRO_COD`, `ReplyToQ = GW.SEC.REPORT`, `ReplyToQMgr = <our QM>`. One
   instruction keeps the report ledger unambiguous.
3. **Browse the report ledger and record every report.** Use `runmqsc`
   (`DISPLAY QSTATUS(GW.SEC.REPORT) CURDEPTH`) plus a browse dump — `amqsbcg
   GW.SEC.REPORT <QM>` or `dmpmqmsg -m <QM> -i GW.SEC.REPORT` — and record, **per
   report**:
   - **Feedback** value → COA (`MQFB_COA`) vs COD (`MQFB_COD`);
   - **generating queue manager** — read `PutApplName` / the report's origin fields and
     the `MQMD.PutDate`/`PutTime`; a report generated by *our* QM vs SVCQM is the
     discriminator;
   - **put time** (`PutDate`/`PutTime`) to order the reports on a timeline;
   - **CorrelId** (should equal the instruction `MsgId`, confirming correlation).
4. **Distinguish a local-origin COA from an SVCQM-origin COA.** The discriminators are
   **(a) generating QM** — the report's origin identifies which queue manager emitted
   it (ours for the local audit-queue arrival; SVCQM for a landing-queue arrival) — and
   **(b) put time** — the local COA fires at ~t0 (original committed on our QM), an
   SVCQM COA only after inter-QM transmission (t1 > t0). If only one COA appears and it
   is our-QM/~t0, that confirms the Track 1 prediction (no SVCQM report from the
   streamed copy).
5. **Record vs hypothesis.** Note count, feedback types, generating QMs, and put times
   against the opening hypothesis; state whether the SVCQM COA and the COD appeared.
6. **Decide the milestone mapping** (fill the table below) and the **go/no-go**.

---

## Milestone-mapping table (skeleton — filled by Track 2)

| observed report | Feedback | generating QM | maps to milestone (ARRIVED / DELIVERED / local-artifact) | evidence (citation id / experiment-run id) |
|---|---|---|---|---|
| _pending live run_ | _pending_ | _pending_ | _pending_ | _pending_ |
| _pending live run_ | _pending_ | _pending_ | _pending_ | _pending_ |
| _pending live run_ | _pending_ | _pending_ | _pending_ | _pending_ |

Track 1 **expected** shape of this table (judgment, to be confirmed): a single row —
COA / `MQFB_COA` / **our QM** / **local-artifact (SENT-adjacent)** — with the
`ARRIVED` and `DELIVERED` rows **absent** because the streamed copy carries no report
request.

---

## Conclusion — definitive NO-GO

**Verdict: NO-GO.** COA/COD transport telemetry **cannot** be added
**non-intrusively and guarantee-preservingly** over the streaming-audit design. The
spike is complete; a well-characterised negative result is the accepted terminal
outcome (spec §4, §13.2), and this one rests on **two independent reasons**, either of
which is sufficient on its own:

- **(a) Streaming strips the report options** (showstopper 1). IBM MQ clears the
  COA/COD option bits from the streamed copy that reaches SVCQM, so **no SVCQM
  `ARRIVED`/`DELIVERED` report is ever generated**. The two milestones the epic wanted
  are simply not produced by this topology.
- **(b) The single `ReplyToQ` is shared with SWIFT response routing** (showstopper 2).
  Because there is only one `ReplyToQ`/`ReplyToQMgr` and it is already committed to the
  business-response queue `GW.SEC.REPLY`, reports **cannot be isolated to a dedicated
  queue** and would land in — and pollute — the application's reply stream.

**The only "fix" is rejected.** The lone way to obtain the milestones would be an
**application dual-put** onto the transmit path (reports requested on a non-streamed
put that the app writes itself). That is rejected because it:

1. **moves audit completeness out of the queue manager's `MUSTDUP` guarantee into
   best-effort application code** — the exact opposite of this repo's MQ-native,
   one-UOW design (a crash between puts becomes silent loss, the failure mode the whole
   lab exists to eliminate);
2. **requires the application to hand-assign a shared `MsgId`** across the two puts,
   re-introducing application logic the streaming design deliberately removed; and
3. **still lands the reports in the app's reply queue** `GW.SEC.REPLY` — showstopper 2
   is not escaped by the dual-put, so even this rejected workaround fails the
   non-intrusiveness bar.

**Terminal actions.** Record this finding, do **not** invent a workaround silently, cut
the downstream ingest/metrics tasks that depended on SVCQM reports, and carry the result
into the epic's risk-documentation retrospective. **Track 2 is not required** for this
conclusion; it would only turn the streaming-strip prediction (a) into an observed fact
and can change nothing about (b).

---

## Sources

- Streaming queues configuration (report options not enabled on the streamed copy;
  descriptor/MsgId copy; CAPEXPRY on the streamed copy):
  https://www.ibm.com/docs/en/ibm-mq/9.3.x?topic=queues-streaming-configuration
- Streaming queue restrictions:
  https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=queues-streaming-queue-restrictions
- Field details for MQMD (COA on target queue; UOW-commit retrieval; COA suppressed for
  `MQFMT_XMIT_Q_HEADER` / `MQFMT_DEAD_LETTER_HEADER`):
  https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd
- Types of message (report messages — COA/COD semantics):
  https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message
- Report (MQLONG) reference (report-option bits):
  https://www.ibm.com/docs/SSFKSJ_9.2.0/com.ibm.mq.ref.dev.doc/q097680_.htm

_Note: IBM's `ibm.com/docs` pages block automated fetching (HTTP 403); the verbatim
statements above were captured via web search over those same pages. A human should
re-read the two primary pages (streaming-queues configuration; MQMD field details)
directly to confirm the exact wording before any go/no-go decision, per this project's
"sources are load-bearing" policy._
