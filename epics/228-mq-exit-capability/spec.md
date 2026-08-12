# Epic #228 — MQ message-exit engineering capability — spec

**Status:** **Design captured; parked / long-horizon.** Not active work. This
epic records a fully-formed design and a strategic capability so it is not lost;
realistic pickup is autumn 2026 or whenever an exit use-case (this one or
another) makes the investment worthwhile.
**Epic:** logical-minds-foundry/mq-gateway-replay-lab#228
**Slug:** `mq-exit-capability`
**Lineage:** direct successor to **#105** (COA/COD telemetry — definitive NO-GO
for a pure MQSC/application design) and an extension of **#65**'s
streaming-audit architecture. Establishes the capability **#105 ruled out of
scope** and **#49 deferred** ("no new vendor products, no bolt-ons, no 'magic'
exits unless proven").

> **Anonymization notice.** This document describes a **generic trading
> back-office message-gateway problem**. All identifying detail is removed and
> must stay removed: no employer name, no named individuals, no internal
> host/queue labels, no counterparty identities. The requesting organization is
> *"the application team"*; counterparties are *"Counterparty A / B / …"*.
> Generic product/technology terms (MQ, streaming queues, channel exits, "the
> gateway") are fine.

---

## 1. Origin — why this epic exists

Epic #105 asked whether MQ-native **COA** (Confirmation On Arrival) and **COD**
(Confirmation On Delivery) reports could decompose the opaque
`instructed → acknowledged` settlement leg into `ARRIVED` (message reached the
counterparty's landing queue) and `DELIVERED` (destructively consumed by the
counterparty). The gating spike (#163) found **two independent showstoppers**,
both since confirmed live (`docs/coa-cod-streaming-findings.md`,
`docs/mqmd-streaming-fidelity.md`):

1. **Streaming strips the report request.** IBM documents that report options
   (Activity, Expiration, Exception, **COA, COD**) are **not enabled on the
   streamed copy**. Our outbound instruction reaches the counterparty (SVCQM)
   only as that streamed copy, so SVCQM never receives the request and never
   generates the milestones. The only report that fires is a useless local COA
   (our QM, ≈ send time).
2. **The single `ReplyToQ` is already taken.** COA/COD route to the one
   `MQMD.ReplyToQ`, which the counterparty requires for routing its long-lived
   **business responses**. Reports cannot be isolated to a dedicated queue; they
   would land on the app's business-response queue.

**The NO-GO is correct — for a pure MQSC/application design.** It is *not* a
statement about what is achievable if we are willing to insert our own code into
the queue manager's message processing. That is exactly the door #105 left
closed and this epic opens: **IBM MQ channel/message exits.**

## 2. Mission

Establish, prove in the lab, and document the org's **capability to build,
deploy, and support IBM MQ channel/message exits** — treating exits as a
deliberate, first-class tool for correcting missing features and design
constraints in MQ. The **first worked example** is a symmetric pair of message
exits that revives COA/COD over the streaming-audit design **with zero
application changes**. The capability is the payload; COA/COD is the
demonstrator that proves it end-to-end.

## 3. The two-exit design (the demonstrator)

The mechanism is symmetric — set the report request on the way out, separate the
report responses on the way back. The asymmetry that makes it work: reports and
business replies **cannot** be separated as a *request* (one `ReplyToQ`), but are
**trivially** separable once they *exist* (by `MsgType`/`Feedback`).

### 3.1 Outbound — sender-channel message exit (re-assert the request)

- **[DATA]** A message exit at the **sending** end of a message channel is called
  *after the MCA has got the message from the transmission queue*, has access to
  the `MQXQH` transmission header **including the embedded message descriptor**,
  and may modify the message. (IBM: *Message exit*; *Channel-exit programs for
  messaging channels*.)
- **Action:** on the streamed copy (post-strip, pre-wire), `OR` the report bits
  back onto the embedded MQMD: `Report |= MQRO_COA | MQRO_COD`. Leave `Expiry` at
  `MQEI_UNLIMITED` (streaming already set it there; that is the desired
  direction — see `mqmd-streaming-fidelity.md` §1.4 — so it is **not**
  reasserted).
- **[JUDGMENT]** Because the **application no longer sets the bits**, the app's
  put to the audit queue carries no report request, so **no premature local COA**
  fires on our own QM. The only reports generated are SVCQM's genuine `ARRIVED`
  COA and `DELIVERED` COD.

### 3.2 Inbound — receiver-channel message exit (separate the responses)

- **[DATA]** A message exit at the **receiving** end is called *before the MCA
  puts the message on its destination queue*, with the same `MQXQH`/MQMD access.
  The `MQXQH` carries `RemoteQName`/`RemoteQMgrName` — the fields the receiving
  MCA uses to place the message.
- **Action:** if `MQMD.MsgType == MQMT_REPORT` and
  `Feedback ∈ {MQFB_COA, MQFB_COD}`, rewrite `MQXQH.RemoteQName` to a dedicated
  report queue we own; otherwise leave the message untouched so genuine business
  replies flow to the business reply queue as designed.
- **[JUDGMENT — verify at build time]** The pieces are individually documented
  (the exit runs before the put; it may modify the `MQXQH`; the MCA routes on
  `RemoteQName`), and content-based redirection is a long-standing exit pattern,
  but IBM does not state "rewrite `RemoteQName` to redirect" in one sentence.
  Treat the redirect as **strongly supported, confirm live**. Clean alternative:
  an **API exit** on `MQPUT` that rewrites `MQOD.ObjectName` before the receiving
  MCA's put — same effect, different layer.

### 3.3 Net result

`ARRIVED`/`DELIVERED` milestones land in the dedicated report queue for the
observability tool; the application **neither sets report flags nor ever sees a
report**. Both #105 showstoppers are addressed: (1) by re-asserting the request
on the streamed copy; (2) by separating the responses on the return leg.

## 4. Why the two timestamps are worth this much effort

Settlement is a **long-horizon request/reply** lifecycle. In T+1 (and T+2)
regimes the acknowledgement leg can take minutes, hours, or up to a day. When the
first acknowledgement is slow, the single most valuable diagnostic is a
**deterministic statement of whether the message even reached the counterparty's
site**. An **instantaneous COA** provides exactly that: it rules the round-trip
network and the counterparty's landing queue **in or out at a glance**, from data
generated **at their site**. COD then marks destructive delivery. These two
timestamps are, in the operator's words, priceless for the first-leg
troubleshooting the streaming design otherwise leaves opaque — which is why
the #105 NO-GO was a genuine loss worth revisiting.

## 5. Strategic payload — the capability, not the feature

Exits let you **customize, and correct, missing features and design flaws in
MQ**. That is a durable, marketable MQ skill, and the org currently has no
tooling, patterns, or proven capability for it. This epic bootstraps it:

- **Exit build/deploy tooling** (see §6 — the cross-org Vergil dependency).
- **A reusable exit skeleton + guardrails** proven in the lab.
- **A "when do we embrace exits?" decision framework** (§8), so the capability is
  applied with eyes open rather than reached for casually.

**Precedent (anonymized).** Roughly 25 years ago, at a large financial
institution, the author's team built and ran a full suite of MQ **security
exits** in production to assert enterprise Kerberos/security-principal
integration — early enough that defects in the exit interfaces were reported back
to IBM as first-in-the-field findings. It worked well and ran stably, but
required a dedicated ~2-person C/C++ team to build and maintain. The lesson
carried forward: exits are powerful and supportable **if** you commit the
engineering discipline; they are not a casual add-on.

**Why this lab is the right place to acquire it.** The methodology is: *build and
learn a technology at home, in a lab like this, before supporting it in
production.* The resiliency lab plus this bolt-on is an ideal, low-risk
environment to develop exit tooling and skill against a live HA/DR queue manager.
This particular use case (COA/COD) is also **relatively simple** as exits go —
tens of lines of logic each — which makes it a good first rung before harder exit
use cases.

## 6. Dependency — Vergil C/C++ build support (cross-org, Track 0)

Exits are **compiled native code** (a shared object loaded into the MCA/queue
manager process). The Vergil tool suite cannot currently build or compile
C/C++. **Track 0, the gating first step, is to create and land a Vergil-tooling
epic that adds C/C++ build+compile support** — assume **C++ as the primary
target, with pure C as a special case** of that environment.

- **[BOUNDARY] Cross-org, referenced not linked.** That epic lives in
  `vergil-project` (a different org). Per the epic framework's placement law,
  epics/tasks are **never linked across orgs**; this epic **references** it and
  gates on it. It is **not** minted as part of this parked epic; it is created
  when #228 is picked up (or sooner if another driver appears).

## 7. Constraints & doctrine

### 7.1 The self-owning bolt-on rule (corrected — formerly "parasite / boundary")

The prior wording was read as "never touch the underlying lab," which is wrong.
The corrected rule:

- The bolt-on **may change the underlying lab whatever it needs to.** The rule is
  **not** "don't touch it."
- What it **must** do: **automate 100%** of (a) its own setup and (b) **every
  change it makes to the underlying lab**, and **own** those changes so they
  **replay cleanly on a from-scratch rebuild**. **Reproducibility is the goal.**
- Changes that must live in the **underlying/generic lab's** code stay **generic
  and general-purpose** — the base lab never carries bolt-on-specific knowledge,
  only generic capabilities the bolt-on builds on (e.g. "exits are enabled/
  build-able here").
- Automated **teardown** is good-citizen nice-to-have, **lower priority** than
  setup + reproducibility.
- **Why:** this is a **scale-of-one** environment (one human). The rule enforces
  disciplined, fully-automated, reproducible config ownership — not protection of
  other tenants. Adding an `MSGEXIT` to an existing lab channel is fine; it may
  invalidate an underlying test, which is acceptable because it is automated and
  owned. The bolt-on's own Ansible + `gwlab` CLI **are** that ownership
  mechanism.

### 7.2 Demonstrator scope — instrument the real path (owned + automated)

Under the corrected rule, the demonstrator **instruments the real SVCQM
streaming path**: the bolt-on's own Ansible/`gwlab` automation adds the
sender + receiver `MSGEXIT`, the dedicated report queue, and any needed channel
attributes to the running lab, **owns** those changes, and they replay on a
from-scratch rebuild. This is the highest-fidelity proof — it produces the real,
priceless COA/COD milestones — and it is now rule-compatible.

### 7.3 Other constraints

- **Anonymization (absolute).** §anonymization notice.
- **Reproducibility gate.** Every lab change replays on a cold rebuild.
- **Cited & honest.** `[DATA]` (docs state) vs `[JUDGMENT]` (our read) separated;
  `docs.ibm.com` 403s automated fetch, so a human re-reads primary IBM pages
  before operational reliance (this project's "sources are load-bearing" policy).

## 8. The "when do we embrace exits?" decision framework (Track E deliverable)

Adopting exits as a capability forces an infrastructure-level choice with real
costs. The framework must state them plainly:

- **Platform lock-in.** Exits are compiled per-platform and differ substantially
  across them. This becomes a **Linux** solution, at best portable to other Unix
  with effort. We deliberately do not chase cross-platform.
- **MQ Appliance non-support.** Exits are **not supported on MQ Appliances** —
  a hard boundary that alone disqualifies exits for many estates.
- **In-process risk.** An exit runs in the MCA/queue-manager address space; a
  defect can crash or hang the channel. **Memory safety is availability.**
- **Engineering cost.** Native code with encoding/CCSID correctness, 64-bit build
  matching, thread-safety, and per-upgrade retesting — historically a small
  dedicated team (§5).
- **Recommendation stance.** For the **current client's production**, the
  recommendation is **against**: high cost, high risk, and appliance/portability
  boundaries. This epic is **lab capability-building**, not a production
  recommendation. If a client explicitly wants to know "what would COA/COD take?"
  — this epic is the answer, with the costs attached.

## 9. Implementation risks to capture (for plan/Track A hardening)

- Native code in the MCA/QM address space (crash/hang = availability incident).
- **Encoding/CCSID correctness** reading `MsgType`/`Feedback`/`Report` off the
  wire — the `MQXQH`/MQMD numeric fields carry the sender's encoding; naive
  native-int reads are the classic exit bug.
- 64-bit `.so` matching the queue manager; thread-safety (MCAs can be
  multi-threaded); fixed 48-byte space-padded queue names; buffer bounds.
- Re-test on every MQ upgrade (exits are a supported but version-sensitive
  interface).

## 10. Open questions (deliberately unresolved — resolve at pickup)

1. **Redirect fidelity (§3.2).** Confirm live that rewriting `MQXQH.RemoteQName`
   in the receiving exit redirects the put; if not, fall back to the API-exit /
   `MQOD.ObjectName` approach.
2. **Report/reply channel sharing.** Confirm reports and business replies return
   over the same receiver channel (so one inbound exit sees both streams).
3. **Double-report handling.** The local COA is eliminated by not setting bits in
   the app, but confirm no other same-QM report noise; decide demux by
   generating-QM if any remains.
4. **`gwlab`/Ansible ownership surface.** Exactly which underlying-lab objects
   (channels, exit path, report queue) the bolt-on must own and automate, and
   which (if any) require a generic capability added to the base lab (§7.1).
5. **Message-property / `PROPCTL` interaction** on the instrumented channels
   (cross-reference `mqmd-streaming-fidelity.md` §6).

## 11. Sources

Verified via web search over the IBM pages (which 403 automated fetch — a human
re-reads them before operational reliance):

- Message exit: <https://www.ibm.com/docs/en/ibm-mq/7.5.0?topic=programs-message-exit>
- Channel-exit programs for messaging channels:
  <https://www.ibm.com/docs/en/SSFKSJ_9.4.0/develop/q027990_.html>
- MQXQH — transmission queue header:
  <https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=i-mqxqh-transmission-queue-header>
- Types of message (report messages, COA/COD, Feedback):
  <https://www.ibm.com/docs/en/ibm-mq/9.2.x?topic=messages-types-message>
- Field details for MQMD:
  <https://www.ibm.com/docs/en/ibm-mq/9.4.x?topic=descriptor-field-details-mqmd>
- IBM sample exits (starting point): <https://github.com/ibm-messaging/mq-exits>
- Prior findings in this repo: `docs/coa-cod-streaming-findings.md`,
  `docs/mqmd-streaming-fidelity.md`.
