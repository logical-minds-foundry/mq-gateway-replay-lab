# Strategic-risk audit of the streaming-audit gateway design — Plan

**Goal:** Produce three cited documentation deliverables mapping the "the counterparty receives MQ's streamed derivative, not what we put" risk class of the streaming-audit gateway, and confirm the field-level divergences empirically with one lab experiment.

**Nature:** This is a **documentation / risk-analysis** epic, not a code-implementation epic. Tasks produce cited markdown and (one) live lab measurement; there is no TDD build. The original COA/COD implementation was reverted (#173).

## Global constraints

- **Cited and honest:** [DATA] (docs state) vs [JUDGMENT] (our read) separated; [DOCS SILENT] where the docs don't address it; `docs.ibm.com` 403s automated fetch, so primary pages are search-sourced and a human must re-read them before operational reliance.
- **No live mutation from tooling sessions;** the one empirical run happens in the lab. Parasite rule intact: QM/SVCQM never mutated.
- **Repo conventions:** `vrg-git`/`vrg-gh`/`vrg-commit`; feature-branch worktrees; `vrg-validate` only.

## Deliverable tasks

### Task A — COA/COD ⊥ streaming (issue #163) — DONE

`docs/coa-cod-streaming-findings.md`. The gating-spike write-up: the two showstoppers (streaming strips the report options; the single `ReplyToQ` is shared with SWIFT response routing) and the definitive no-go. Cited. This is the epic's origin finding.

### Task B — MQMD original-vs-streamed fidelity deep-dive (issue #172) — DONE (empirical rows pending)

`docs/mqmd-streaming-fidelity.md`. Every MQMD field + message properties, each with copied/altered/reset/uncertain status, cited evidence, and a counterparty-risk rating. Flagship deliverable.

### Task C — Strategic risk register (issue #180) — DONE

`docs/gateway-design-risk-register.md`. Living register synthesized from A and B: per-risk description / what-to-test / why-it-matters / severity / status. Seeded with R1 (CAPEXPRY expiry), R2 (origin-context re-stamping), R3 (UserIdentifier), R4 (groups), R5 (MQRFH2 properties), R6 (COA/COD stripping), R7 (shared ReplyToQ).

### Task D — Spec/plan repurpose (issue #179) — this task

Rewrite `spec.md`/`plan.md` from the COA/COD implementation to this risk-audit mission, preserving the COA/COD origin as history.

## The one empirical validation (issue #108, repurposed)

Repurpose the live-run validation from "COA/COD reports arrive" to **"streamed-copy fidelity diff."** In the lab: put one instruction; `amqsbcg GW.SEC.INSTRUCT.AUDIT` (the retained original); capture the streamed copy on the **SVCQM landing queue** (`GW.SEC.INSTRUCT.REMOTE` is a QREMOTE — not directly browsable); **diff the two MQMDs field by field.** Record which of the "to-validate" rows (origin-context, `UserIdentifier`, groups, properties) are copied vs altered, and confirm no `CAPEXPRY` re-imposes an expiry on the stream-target path. This promotes B and C from "documented + inferred" to "measured on our topology." Then A/B/C are report-ready-final.

## Closing bookends

- **Documentation-review (#107):** sweep human-facing docs (README / docs/) so the risk findings are discoverable, not buried in the epic folder.
- **Retrospective (#109):** record the whole arc — the COA/COD idea, the two showstoppers, the design constraints (completeness-by-construction, non-intrusive observation), the repurpose, and the risk register as the durable output.

## Reverted / closed

- Implementation tasks #164–#168 closed won't-do; #161/#162 reverted (#173). No dead code on the leading edge; revivable from git history.
