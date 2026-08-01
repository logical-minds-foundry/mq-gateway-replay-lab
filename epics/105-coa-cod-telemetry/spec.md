# Epic #105 — Strategic-risk audit of the streaming-audit gateway design — spec

**Status:** **Repurposed.** Began as "COA/COD transport-milestone telemetry"; the gating spike proved that idea a dead end, and the epic was repurposed into a **strategic-risk identification, documentation, and validation** effort for the streaming-audit gateway design. Documentation deliverables largely complete; empirical confirmations pending an authorized lab run.
**Epic:** logical-minds-foundry/mq-gateway-replay-lab#105
**Slug:** `coa-cod-telemetry` — **historical** (kept deliberately; it records how this work began). Do not rename.
**Relationship to #65:** This epic **audits #65's streaming-audit architecture** for a class of risk that architecture carries, rather than adding a feature to it.

---

## 1. Origin — why this epic exists, and why it changed

This epic started as an attempt to bolt MQ-native **COA/COD** report messages onto #65's streaming-audit gateway to decompose the opaque `instructed→acknowledged` latency leg into *network / their-queue / their-app* — a genuinely valuable observability win (especially `sent→arrived`, which would let an operator **rule the round-trip network in or out at a glance**).

A gating spike (issue #163) was run *before* building the collector. It found **two independent showstoppers**:

1. **Streaming strips the report request.** IBM's *Streaming queues configuration* documents that report options (Activity, Expiration, Exception, **COA, COD**) are **not enabled on the streamed copy**. Our outbound instruction reaches the counterparty (SVCQM) only as that streamed copy — so SVCQM never receives the request and never generates the `ARRIVED`/`DELIVERED` milestones the feature needs. The only report that fires is a useless local COA (our QM, ≈ send time).
2. **The single `ReplyToQ` is already taken.** COA/COD route to the one `MQMD.ReplyToQ`, which the SWIFT counterparty requires for routing its (long-lived) business responses. So reports cannot be isolated to a dedicated queue; they would land on the app's business-response queue and force the app to handle them.

The only "fix" — an application dual-put to the transmit path — was rejected because it moves audit completeness from the queue manager's `MUSTDUP` guarantee into best-effort application code, needs the app to hand-assign a shared MsgId, and *still* dumps reports into the app's reply queue. That violates the design's two non-negotiables: **completeness by construction** and **non-intrusive observation** (§3).

**The productive realization:** the spike exposed a deeper, general truth — **the counterparty does not receive the message we put; it receives MQ's *streamed derivative*.** COA/COD is just the first instance of a whole class of "silent divergence" risks. Documenting that class is far more valuable than the original feature. Hence the repurpose.

## 2. Mission

**Identify, document, and (where the lab permits) empirically validate the strategic risks lurking in the subtle details of the streaming-audit gateway design** — with the field-level divergence between the retained original and the streamed copy as the central theme.

## 3. The design constraints this epic defends

The audit-gateway design rests on two principles that COA/COD violated and that frame every risk here:

- **Completeness by construction.** The audit copy's correctness is **enforced by the queue manager** (`STREAMQ … STRMQOS(MUSTDUP)` — no copy, no put), never by application code. Any mechanism that moves completeness into the app is rejected.
- **Non-intrusive observation.** The observability tools observe the data flow **without the app changing its message contract or its handling**. Anything that forces the app to set fields, do extra puts, or process new messages is rejected.

## 4. Deliverables

- **A — "COA/COD ⊥ streaming"** (`docs/coa-cod-streaming-findings.md`, issue #163). The cited incompatibility write-up and the two showstoppers; the origin finding. *Complete.*
- **B — "MQMD original-vs-streamed fidelity deep-dive"** (`docs/mqmd-streaming-fidelity.md`, issue #172). Field-by-field: what streaming copies, alters, or resets between the retained original and the streamed copy the counterparty receives; each cited, each risk-rated for a SWIFT securities-settlement counterparty. *Complete (empirical rows pending).* **Flagship deliverable.**
- **C — "Strategic risk register"** (`docs/gateway-design-risk-register.md`, issue #180). The living register synthesized from A and B: each risk with description / what-to-test / why-it-matters / severity / status. *Complete (seeded).*

## 5. The headline risks found (see B and C for the full set)

- 🔴 **Origin-context re-stamping** — the streamed copy is a queue-manager-initiated put; IBM docs are **silent** on whether `PutApplName`/`PutDate`/`PutTime`/`PutApplType` are copied or re-stamped. A counterparty that routes/audits on those could act on values different from what the app put. *The most consequential new find.*
- 🔴 **Expiry via `CAPEXPRY`** — the streamed copy's `Expiry` becomes `MQEI_UNLIMITED` (which satisfies the no-expiry requirement) **unless** a `CAPEXPRY` on the stream target silently re-imposes a finite expiry.
- 🟡 **`UserIdentifier` fidelity** — docs lean "copied" for `STREAMQ`, but a documented pub/sub-vs-`STREAMQ` conflation means it must be measured if the counterparty authorizes on identity.
- 🟡 **Message groups/segmentation** and **`MQRFH2` property survival** — docs-silent; matter only if SWIFT uses them, but then they are essential.
- ✅ **Preserved (documented):** MsgId, CorrelId, message properties, application data; Persistence and Priority (from the original, not the secondary queue).

## 6. Method & scope

- **Documentation is cited and honest.** Every claim separates **[DATA]** (what IBM docs state) from **[JUDGMENT]** (our risk read); where the docs are silent it says **[DOCS SILENT]** and defers to empirical measurement. IBM's `docs.ibm.com` blocks automated fetch (403), so the primary pages were reached via search and corroborated; **a human should re-read the primary pages before relying on any row operationally.**
- **Empirical validation is a single lab experiment** (issue #108, repurposed): put one instruction, `amqsbcg` the retained original on `GW.SEC.INSTRUCT.AUDIT`, capture the streamed copy on the **SVCQM landing queue** (note: `GW.SEC.INSTRUCT.REMOTE` is a QREMOTE, not directly browsable), and **diff the two MQMDs field by field** — promoting every "to-validate" row to a measured result and confirming the CAPEXPRY precondition. This session has no MQ access; the run is performed in the lab.
- **Out of scope:** any implementation of COA/COD (reverted, #173); any change to #65's architecture; any mutation of the QM or SVCQM.

## 7. Success criteria

1. Deliverables A, B, C exist, are cited, and separate data from judgment.
2. The MQMD deep-dive (B) covers every field with a copied/altered/reset/uncertain status and a counterparty-risk rating.
3. The risk register (C) enumerates each risk with a concrete "what to test."
4. The empirical lab diff (issue #108) has been run (or is clearly queued as the one remaining live task), promoting the "to-validate" rows to measured results.
5. The retrospective (#109) records the reasoning — including the two design constraints (§3) — so the "why" survives.

## 8. Relationship to #65 and the future

This epic is a **risk audit of #65's streaming-audit mechanism**, not a change to it. Its most actionable output is a short list of things #65 (or any streaming-audit gateway) must verify before trusting that "the counterparty receives what we sent." The original COA/COD idea remains **revivable** only as a *standalone, non-streaming* variant (e.g. relevant to the v1 request/reply flow), explicitly outside this design's constraints.
