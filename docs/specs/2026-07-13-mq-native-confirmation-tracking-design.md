# Design: MQ-native confirmation-tracking recommendation

**Date:** 2026-07-13
**Epic:** #49 (resilient-mq-gateway) — this is folded in as the capstone deliverable.
**Status:** approved in brainstorming; ready for implementation planning.

## Goal

Produce a single, generic, client-facing **recommendation document** that shows an
external engineering audience how to track message confirmations and enable replay
by leveraging MQ-native **audit queues** — and does so with the credibility of a
working prototype that was built and validated in this lab.

The document is the primary deliverable. Two supporting pieces of work exist only
to make it land: a fresh live run that produces the hero dashboard screenshot, and
an `archive` → `AUDIT` rename so naming matches IBM's own convention across code,
dashboard, and document.

## Audience and purpose

- **Primary readers:** the client's implementing software engineers *and* their
  leads — one document that makes the case to a skeptic and hands a builder a
  ready-to-implement blueprint.
- **The document persuades, then instructs.** It opens by making the case against a
  hand-rolled log, then over-delivers on the algorithmic detail the engineers
  actually want.
- **Provenance is the spine.** The reader must come away certain this was *built and
  validated in a lab against real Native HA MQ*, not merely researched. We do not
  disclose implementation language or code — we state that a working prototype and
  a proven algorithm exist, and we let the evidence (hard numbers + the dashboard
  screenshot) earn that claim.

## Hard constraints

1. **Emailed through the client's gateway → prose + algorithms only. No code.**
   The only literal technical syntax permitted is:
   - **MQSC config snippets** (object definitions), and
   - the **dashboard screenshot** (an embedded image).
   Every computation is expressed as a described, serial algorithm — numbered steps
   referencing message-header fields — never a code block in any language.
2. **Absolute anonymization (repo rule).** Fully generic vocabulary throughout:
   "the application team", Counterparty A/B, invented queue/host names
   (`GW.CPB.REQUEST`, etc.). The committed artifact contains no client-identifiable
   content. The author maps generic names to the real estate mentally when
   presenting; there is no committed mapping.
3. **`AUDIT` naming.** Adopt IBM's own streaming-queue example vocabulary: the
   archive copies are **audit queues**. Rename `archive` → `AUDIT` everywhere —
   code, dashboard queue labels, and document — so all three are consistent.
   Operating principle: follow the conventional recommendation unless there is a
   strong reason not to.

## Document structure (Approach A: Problem → Principle → Blueprint → Evidence)

Location: `docs/mq-native-confirmation-tracking.md` (new, generic, committed).

1. **Executive summary** — the ask in one paragraph; closes with the provenance
   claim (built and validated in a lab against real Native HA MQ).
2. **The problem** — replay requires knowing *which* messages are unconfirmed,
   stated neutrally in the reader's terms.
3. **The core insight** — stop hand-rolling a log; let the queue manager write an
   audit copy of every message and every confirmation, under one unit of work, with
   the same MsgId.
4. **The blueprint** — MQSC object definitions (audit queues, `STREAMQ`/`STRMQOS`,
   `CAPEXPRY`) and the invariants: MsgId preserved into the audit copy; the CorrelId
   echo makes the confirmation match authoritative.
5. **Algorithm 1 — per-request response time.** Browse both audit queues; match each
   outbound message to its confirmation via MsgId↔CorrelId; compute latency from the
   message-descriptor put timestamps (confirmation put date/time minus request put
   date/time). Described as numbered steps referencing header fields.
6. **Algorithm 2 — reconciliation.** Collect the set of outbound MsgIds and the set
   of confirmation CorrelIds from the two audit queues; the set difference is the
   unconfirmed set; report both the **count of pending confirmations** and the
   **specific unconfirmed messages** (MsgId, sent-time, age). Scoped to the current
   trading day (self-clearing via `CAPEXPRY`). Note the scale lever (local bindings
   + incremental high-water-mark) as a described refinement.
7. **Algorithm 3 — anomaly detection (unexpected confirmations).** The mirror of
   reconciliation: a confirmation whose CorrelId matches **no** sent MsgId (an
   orphan reply), or a **duplicate** confirmation for a MsgId already confirmed.
   Framed explicitly as a *should-never-happen* exception — unlike a missing
   confirmation, which is a benign, expected glitch (a message lost in transit),
   an orphan/duplicate reply signals a genuine correctness fault and is flagged
   as an exception. Same browse, opposite set difference (confirmation CorrelIds
   with no matching sent MsgId) plus a duplicate check.
8. **The collector** — what the client's engineers build, described conceptually;
   explicitly noted as prototyped and proven in the lab. No language, no code.
9. **Evidence** — the last-hour dashboard screenshot plus hard numbers from real
   runs.
10. **Scale** — where the bottleneck actually is, with measured numbers.
11. **Questions for the application team** — the Phase-1 payload (forwarding,
    volumes, topology, recovery objective, data-at-rest, HA appetite).

The provenance thread surfaces in three places: the summary (the claim), the
collector section (proven implementation), and the evidence section (the data that
earns it).

## Evidence to marshal (all measured in the lab)

- **Reconciliation, live end-of-day:** sent / confirmed / unconfirmed, mean confirm
  latency, and the specific unconfirmed set by MsgId + age — read straight off the
  audit queues.
- **Anomaly detection, live:** the board surfaces the injected orphan/duplicate
  reply as an *unexpected confirmation* exception — demonstrating the should-
  never-happen fault is caught, not silently ignored.
- **Scale (measured):** the diff is ~0.5 µs/header (a 1M-trade day reconciles in
  ~1.1 s of CPU); the client-mode `MQGET(BROWSE)` round-trip (~640 µs/header) is the
  real bottleneck (~21 minutes for a full-archive 1M-trade scan) — hence the
  local-bindings + incremental recommendation.
- **Platform provenance:** ran against real **Native HA** (3-node group in sync)
  over **mutual TLS 1.3**.
- **Hero figure (new, produced today):** the *last-hour* dashboard screenshot from a
  fresh live run.

## The last-hour scenario (feeds the hero figure)

A new simulation scenario representing the **last hour of a trading day**, run live
against `NHAUAPP` to capture a real board:

- steady / healthy flow, then
- response times **degrade** (a slowdown), then
- a **~10-minute block** (no confirmations arrive), then
- **recovery**, but
- **2–3 trades never get a reply**, left unconfirmed at end of day, and
- near end-of-day, **one injected anomaly** — an orphan reply (a confirmation whose
  CorrelId matches no sent MsgId) or a duplicate confirmation — so the board also
  demonstrates Algorithm 3's exception signal.

This lets the board show (a) the latency-degradation curve, (b) the specific small
unconfirmed set, and (c) the unexpected-confirmation exception — enough to
demonstrate what the dashboard highlights without running a literal full day. The
observability panel shows the last hour, not a full day. This differs from the
existing `TROUBLED_DAY` scenario (which leaves the whole outage unconfirmed); the new
one recovers almost everything and leaves a handful, plus the single anomaly.

## Epic #49 task breakdown

Folded into epic #49 as issues (created via `vrg-gh` **after** this spec is
confirmed, so the tasks are fixed first). A spec/plan is added under
`epics/49-.../` in the `.github` repo.

1. **`AUDIT` rename** — `archive` → `AUDIT` across code, dashboard queue labels, and
   naming, so the screenshot and the document are consistent. Also remove the stale
   "two-put-under-syncpoint / streaming queues deliberately not used" hard rule from
   `CLAUDE.md`, which contradicts the real streaming-queue design.
2. **Last-hour scenario + anomaly + live run + screenshot** — add the new scenario
   (including the injected orphan/duplicate reply); extend reconciliation with the
   Algorithm 3 unexpected-confirmation signal and surface it on the board; run it
   live against `NHAUAPP`; capture the dashboard screenshot for the document.
3. **Write the recommendation document** — `docs/mq-native-confirmation-tracking.md`
   per the structure and constraints above (three algorithms).

**Order:** rename first (so the screenshot is consistent) → run + capture → write
the document and drop in the real figure.

## Success criteria

- A committed, fully generic `docs/mq-native-confirmation-tracking.md` that a
  competent MQ engineer who has never met the author could build from.
- Contains no code — only prose, the three described algorithms, MQSC snippets, and
  one dashboard screenshot.
- Makes the provenance unmistakable: proven algorithm + proven implementation,
  backed by measured numbers and a real board.
- `AUDIT` naming is consistent across code, dashboard, and document.
- Epic #49 carries the three issues above.

## Out of scope

- A literal full-day (multi-hour) run.
- Disclosing implementation language, code, or the lab's tooling internals.
- Any client-identifiable content.
- Building the collector *for* the client — the document describes it; they build it.

## Correction (2026-07-13): the remote-queuing topology is mandatory

The initial lab (and the first draft of §4) modelled the counterparty as a **local
consumer of a local queue** — no transmission queue, no channel, no remote queue
manager. That is uninteresting and proves nothing about production: the counterparty is
always across a channel on another queue manager. The design and the recommendation are
corrected to the remote topology, and the lab **must prove it on real remote queuing**
before the write-up is circulated (docs are a guide; truth is proven on real iron).

**IBM streaming-queue facts (sourced, cached under the lab's `build/refs/ibm-docs/`):**

- You **cannot** define `STREAMQ` on a transmission queue (`USAGE(XMITQ)`) nor on a
  remote-queue definition — source of a stream is always an ordinary local queue.
  ([restrictions](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=queues-streaming-queue-restrictions))
- A local queue's `STREAMQ` **target may be a remote queue**, delivering the duplicate to
  another queue manager.
  ([remote/alias](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=queues-streaming-remote-alias))
- The streamed copy preserves the message descriptor including **MsgId and CorrelId**.
  ([configuration](https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=queues-streaming-configuration))

**Corrected mechanism.**

- **Outbound.** The app puts to `GW.CPB.REQUEST` — a **`QALIAS`** onto
  `GW.CPB.REQUEST.AUDIT`, so the audit, the retention, and the streaming are all invisible
  to the app (transparency restored). `GW.CPB.REQUEST.AUDIT` is the local queue that **is
  the outbound audit** (retained original); its `STREAMQ` target is a **remote-queue
  definition** `GW.CPB.REQUEST.REMOTE` → the counterparty QM `SVCQM`, over the lab's
  *existing* XMITQ + sender channel. One put, `STRMQOS(MUSTDUP)`: the queue manager retains
  the original locally and transmits the **duplicate** to the counterparty. Must-duplicate
  guarantees the duplicate reaches the **XMITQ** (not that the channel has delivered) —
  correct, since an untransmitted message is rightly unconfirmed.
- **Inbound.** Confirmations arrive across the return channel onto the **application**
  reply queue `GW.CPB.REPLY` (drained by the gateway), which streams a **duplicate** to
  the local **audit** `GW.CPB.REPLY.AUDIT`.
- **The `CAPEXPRY` asymmetry (the crux).** Retention runs through two *different*
  behaviors:
  - **Outbound:** `CAPEXPRY(864000)` on the **primary** (`GW.CPB.REQUEST.AUDIT`) must cap the
    **retained original** to 24 h. The **streamed copy** to the counterparty gets
    `MQEI_UNLIMITED` (the secondary is the remote queue, which has no `CAPEXPRY`) — correct
    for delivery. This is the **inverse** of what IBM documents (`CAPEXPRY` on the
    *secondary* caps the *streamed* copy); the primary-caps-the-retained-original behavior
    is **not documented for this direction and must be proven live**.
  - **Inbound:** `CAPEXPRY(864000)` on the **secondary** (`GW.CPB.REPLY.AUDIT`) caps the
    **streamed copy** — the standard, documented behavior. Trusted, but verify.
- **Still a single put per side.** Zero audit *logic*; the only cost is lost transparency
  (the app knowingly puts to the audit-named queue). Cheaper than the two-put alternative,
  which is why the streaming pattern (A) is chosen over two-put-under-syncpoint (B).
- Reconciliation browses `GW.CPB.REQUEST.AUDIT` (outbound audit) and `GW.CPB.REPLY.AUDIT` (inbound
  audit).

**Load-bearing live proofs (gate everything).**
1. ✅ **PROVEN (MQ 9.4.5, on `SVCQM`, 2026-07-13).** `CAPEXPRY` on the outbound primary
   expires the **retained original** (scratch queue `CAPEXPRY(100)`=10 s: T0 original
   `Expiry 100`, after 20 s `No more messages`/`CURDEPTH(0)`), while the **streamed copy**
   stays unlimited (`Expiry -1`, still present). The undocumented inversion holds.
2. **MsgId preserved end-to-end** to `SVCQM` across the real channel — *pending the remote
   leg*.
3. `MUSTDUP` retain+transmit genuinely atomic over the remote link — *pending the remote leg*.
4. ✅ **PROVEN (same test).** Streaming fires on a put via **`QALIAS` resolution** (put to
   the alias → original on the primary *and* duplicate on the stream target). Transparency
   confirmed.
IBM's docs held for what was tested (no doc bug to file). Proofs 2–3 require the full
remote wiring (QREMOTE → `SVCQM` over the existing channel + our queue on `SVCQM`).

**Impact.** The three algorithms, the collector, the observability, and reconciliation
are **transport-agnostic and unchanged**. What changes: (1) the outbound MQ objects
(a QREMOTE stream target reusing the existing XMITQ/sender channel to `SVCQM`; the
outbound audit being the put queue itself); (2) we **reuse the lab's existing counterparty
QM `SVCQM` and inter-QM channels** — we add only our own `GW.CPB.*` queues to `NHAUAPP`
and `SVCQM` and run our responder on the `SVCQM` side (no new QMs/channels, parasite rule
intact); (3) `QueueSet`/reconcile/simulation change so the outbound audit is the put
queue and the stream target is remote — the code that currently streams `send →
send_audit` locally is the local shortcut being replaced.

This correction **blocks** the last-hour live run and the finalisation of the document.
