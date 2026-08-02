# Epic #105 — Retrospective

**Epic:** logical-minds-foundry/mq-gateway-replay-lab#105
**Span:** 2026-07-30 → 2026-08-02
**Reads with:** `spec.md` (intent) → `plan.md` (approach) → this record (how it went).

---

## §0 At a glance

We set out to **add MQ-native COA/COD transport telemetry** to decompose #65's
opaque `instructed→acknowledged` settlement leg into *network / their-queue /
their-app*. A first-task **gating spike proved the feature infeasible** over the
streaming-audit design — before any collector was built — so the epic **pivoted
into a strategic-risk audit** of that design and **empirically validated, on the
live lab, that the streamed copy the counterparty receives is byte-identical to
the retained original save one favorable field**. The delivered value is a set of
cited, measured risk findings — worth more than the original feature.

**Work delivered (gateway repo, `mq-gateway-replay-lab`):**

| PR | Date | What it did |
|---|---|---|
| #169 | 07-30 | spec + plan for COA/COD telemetry (documentation bookend) |
| #170 | 08-01 | provision `GW.SEC.REPORT` ledger *(later reverted)* |
| #171 | 08-01 | request COA/COD + `feedback` on the put *(later reverted)* |
| #174 | 08-01 | **revert** the abandoned COA/COD implementation (#161/#162) |
| #181 | 08-01 | Deliverable A — COA/COD × streaming gating-spike findings (**no-go**) |
| #182 | 08-01 | Deliverable B — MQMD original-vs-streamed fidelity deep-dive |
| #183 | 08-01 | repurpose spec + plan to the risk-documentation mission |
| #184 | 08-01 | Deliverable C — strategic risk register |
| #185 | 08-01 | fold measured MQMD-fidelity results + docs-review sweep |
| #188 | 08-02 | live-lab experiment results (COA/COD strip, groups, properties) |

**Cross-repo:** lab repo `mq-resiliency-lab-for-linux` #878 — the "operating the
lab from a dev session" access runbook (spun off; the gateway repo is private, the
runbook is lab-generic).

**Counts:** 17 sub-issues (10 closed by PR; 5 closed won't-do — the unbuilt
implementation tasks #164–#168; 1 operational validation #108 closed on a SUCCESS
comment; this retrospective). 10 in-repo PRs + 1 lab-repo PR. **0 releases.**
Repos touched: 2.

---

## §1 How the plan evolved

The plan changed more than any epic to date — and the delta *is* the story.

- **Planned as an implementation epic.** Full brainstorm → pushback → plan →
  alignment produced a COA/COD telemetry collector bolted onto #65. It was then
  **suspended** pending #65's completion (its data model and dashboard were still
  in flight), and on resume **refit** to #65's finished streaming-collector /
  requester-responder / Grafana architecture.
- **The gating spike detonated the premise.** The plan's first task — a spike to
  resolve the undocumented COA/COD × streaming-queue interaction *before* building
  the collector — found **two independent showstoppers**: (a) MQ streaming
  **strips the report options** from the streamed copy, so SVCQM never emits the
  `ARRIVED`/`DELIVERED` reports the feature needs; (b) the single `MQMD.ReplyToQ`
  is already committed to SWIFT response routing, so reports can't be isolated to
  a dedicated queue. The only "fix" (an application dual-put) was rejected because
  it moves audit completeness out of the queue manager's `MUSTDUP` guarantee.
- **The productive pivot.** Rather than abandon the work, the epic was
  **repurposed** into a strategic-risk audit of the streaming-audit design — the
  spike had exposed a whole *class* of "the counterparty receives MQ's streamed
  derivative, not what we put" risks. The implementation was reverted (#174), the
  unbuilt tasks closed won't-do, and three cited deliverables produced.
- **Documentation → measured.** The user pushed for hard evidence over
  documentation. Operating the live lab directly, we ran a MQMD fidelity diff and
  three targeted experiments, promoting the "to-validate" risk rows to **measured**
  facts and confirming the COA/COD strip *live*.

## §2 Lessons learned

- **Gating spikes earn their cost many times over.** One spike, run before a line
  of collector code, killed a feature that would otherwise have been built on a
  false premise and failed at integration. This is the single most valuable
  discipline the epic demonstrated.
- **"Documented" is not "measured."** The cited IBM docs were correct, but the
  live-lab measurement is what turned inference into evidence — and it *refuted* a
  high-severity risk (origin-context re-stamping) that the docs left genuinely
  open. Insist on the experiment when the stakes warrant it.
- **A well-characterised negative result is a first-class deliverable.** "These
  two architectures don't compose, and here is the field-level proof" is worth
  more than a shipped-but-fragile feature.
- **Name the design constraint.** The durable takeaway is *why* COA/COD can't
  work here — **completeness by construction (QM-enforced, not app-enforced)** +
  **non-intrusive observation**. Those two principles now have a written home.
- **Discover the domain tooling before concluding "can't."** A dev session
  wrongly concluded "no lab access" by probing the raw shell instead of finding
  `mqlab` / `qemu:///system`. Fixed for humans (the #878 runbook) and for future
  agents (a repo-memory pointer).

## §3 Compromises & tradeoffs

- **Reverted merged code rather than leaving it.** #161/#162 encoded the
  falsified premise (a dedicated report queue; `ReplyToQ` overridden — which would
  have broken real response routing). We chose a clean leading edge over keeping
  dead code; it is fully revivable from git history.
- **The live experiments used probe messages** (`amqsput` / `pymqi`), not the real
  gwlab requester (reverted). Adequate for MQMD fidelity — the origin-context was
  measured with the probe's identity (`amqsput`/`mqm`), but the conclusion
  (streaming preserves origin context, does not re-stamp) holds regardless of
  which app put the message.
- **Disposable-lab liberties.** Cleared ~527k stale messages on SVCQM's landing
  queue to isolate probes, under an explicit full-access grant (the lab is rebuilt
  nightly).
- **Kept the historical slug** `coa-cod-telemetry` despite the repurpose —
  accepted the misnomer to avoid the churn of renaming already-merged docs; the
  spec's status line carries the history.
- **Residual coverage.** R4 (grouping) was measured single-message-in-group only,
  not multi-segment; R5 (properties) was measured as inline `MQRFH2`, not
  message-handle / `PROPCTL`-separated properties. Both residuals are documented,
  not silently dropped.

## §4 New problems & opportunities

- **The risk register is a living asset for the whole streaming-audit design**
  (not just this epic) — the "counterparty receives the streamed derivative"
  class applies to #65 and any streaming-audit gateway.
- **A concrete config invariant surfaced (R1):** *no `CAPEXPRY` on the stream
  target* (`GW.SEC.INSTRUCT.REMOTE`) — otherwise a finite expiry is silently
  re-imposed on the counterparty's message. Worth guarding in #65's provisioning.
- **Spun off:** the lab-access runbook (lab repo #878) + a gateway repo-memory
  pointer — to stop future access confusion.
- **Logged, not yet acted on:** the residual experiments (multi-segment groups;
  message-handle/`PROPCTL` properties) remain open in Deliverable B / the register.
- **COA/COD remains revivable only as a standalone, non-streaming variant** (e.g.
  the v1 request/reply flow), explicitly outside this design's constraints.

## §5 What's next

No follow-on epic is mandated. The risk register (`docs/gateway-design-risk-register.md`)
feeds #65's ongoing hardening; the two residual experiments are logged there for
whenever they're warranted. This epic closes as a completed risk audit.

---

## Appendix A — Operational notes (the live-lab validation)

The empirical results (#108 fidelity diff; #187/#188 experiments) were produced by
operating the live lab directly — a method now captured in the lab repo's
`docs/development/operating-the-lab-from-a-dev-session.md` runbook:

- Lab VMs are libvirt guests under `qemu:///system`, operated via `mqlab`; nodes
  on `10.50.0.0/24`, vagrant SSH. Gateway QM `NHAUAPP` (Native HA; active node via
  `dspmq`); counterparty `SVCQM` on svc-sim.
- **Fidelity diff:** cleared SVCQM's landing queue, put one probe through the
  `GW.SEC.INSTRUCT` QALIAS, and `amqsbcg`'d the same message (matched by `MsgId`)
  as the retained original on `GW.SEC.INSTRUCT.AUDIT` and the streamed copy on
  SVCQM. **Only `Expiry` differed** (original ~3d from AUDIT's `CAPEXPRY`; streamed
  copy `MQEI_UNLIMITED` — favorable); everything else byte-identical.
- **R6 (COA/COD):** a `pymqi` put with `MQRO_COA|MQRO_COD` + `ReplyToQ` yielded
  **exactly one report — a local COA from `NHAUAPP`** (`Feedback 259`); SVCQM
  generated nothing despite receiving the streamed copy. The strip, proven live.
- **R4 (groups):** `GroupId`/`MsgSeqNumber`/`MsgFlags` byte-identical on the
  streamed copy. **R5 (`MQRFH2`):** `Format='MQHRF2'` + the `<usr>` property folder
  survived intact.
- **Net:** streaming preserves the entire MQMD/message **except** `Expiry`
  (→ unlimited, favorable) and report options (stripped) — confirmed across scalar
  fields, grouping, and properties. The gateway's audit/replay integrity holds.
