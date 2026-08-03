# MQ message-exit engineering capability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish and prove, in the lab, the org's capability to build/deploy/support IBM MQ message exits — with a symmetric pair of exits that revives COA/COD over the streaming-audit design (zero application changes) as the first worked example.

**Architecture:** A sender-channel message exit re-asserts `MQRO_COA | MQRO_COD` on the streamed copy's embedded MQMD (post-strip, pre-wire); a receiver-channel message exit redirects returning COA/COD reports (by `MsgType`/`Feedback`) to a dedicated report queue, leaving business replies untouched. Both exits, the report queue, and the channel wiring are owned and automated by this bolt-on (its Ansible + `gwlab`), instrumenting the real SVCQM path and replaying on a cold rebuild.

**Tech Stack:** IBM MQ 9.4 channel/message exits (C++ primary, C as a special case; `MQ_CHANNEL_EXIT` entry point, `MQCXP`/`MQCD`, `MQXQH`+embedded MQMD); a Vergil C/C++ build capability (Track 0 dependency, `vergil-project`, cross-org); Ansible + `gwlab` for owned/automated lab wiring; `amqsbcg`/`runmqsc` for browse-based verification; Grafana for the milestone panels.

## Global Constraints

- **Parked / long-horizon.** Not active work; captured for pickup (≈ autumn 2026). Tasks are track-level by intent — see spec §1 status. Do not begin implementation before Track 0 lands.
- **Anonymization (absolute).** No employer/client/counterparty identity in any artifact. Generic vocabulary only. (spec §anonymization notice)
- **Self-owning bolt-on rule (corrected).** Automate 100% of the bolt-on's own setup AND every change it makes to the underlying lab; own them so they replay on a from-scratch rebuild; keep any underlying-lab code changes generic. (spec §7.1)
- **Reproducibility gate.** Every lab change must re-apply cleanly onto a freshly rebuilt lab.
- **Cited & honest.** `[DATA]` vs `[JUDGMENT]` separated; a human re-reads the primary IBM pages (they 403 automated fetch) before operational reliance. (spec §11)
- **Exit-safety non-negotiables.** Native code runs in the MCA/QM address space — memory safety is availability; encoding/CCSID-correct reads of `MsgType`/`Feedback`/`Report`; 64-bit `.so` matching the QM; thread-safe; retest per MQ upgrade. (spec §9)
- **Vergil workflow.** `vrg-git`/`vrg-gh`/`vrg-commit`; feature-branch worktrees; no direct commits to `develop`/`main`; validation is `vrg-container-run -- vrg-validate` only.

---

## Track 0 — Dependency: Vergil C/C++ build support (GATE, cross-org)

Blocks every build task below. Exits are compiled native code and the Vergil suite cannot yet build C/C++.

### Task 0.1: Create & land the Vergil C/C++ build-support epic

- [ ] Run `epic-create` (or `vrg-epic-create`) in `vergil-project` for a **C/C++ build+compile capability** in the Vergil tool suite — C++ primary, pure C as a special case. Cross-org: **reference** this epic (#228), do **not** link across orgs. (spec §6)
- [ ] Scope that epic to: a container/toolchain that compiles a 64-bit shared object matching the target MQ, a `vrg-validate` hook for C/C++ (build + unit test), and a place for exit sources in a Vergil-managed repo.
- [ ] Record its ref here and mark this track's gate satisfied once it lands.

## Track A — Exit engineering scaffolding (blocked-by Track 0)

Stand up the reusable exit build/deploy capability in the lab before writing behaviour.

### Task A.1: Exit source skeleton + build

- [ ] Create a minimal message-exit source (`MQ_CHANNEL_EXIT` entry point handling `MQXR_INIT`/`MQXR_MSG`/`MQXR_TERM`, `MQXCC_OK` passthrough) that compiles to a 64-bit `.so` via the Track 0 toolchain.
- [ ] Add a `vrg-validate`-invoked build + no-op unit test.

### Task A.2: Owned deploy/wire path via `gwlab` + Ansible

- [ ] Extend the bolt-on's Ansible/`gwlab` to place the `.so` in the exit path and set `MSGEXIT` on the target channel(s) — **owned and automated**, replayable on a cold rebuild. (spec §7.1–§7.2)
- [ ] Add a declare-and-verify check that any underlying-lab capability the bolt-on relies on (exit path exists / exits enabled) is present, and that any base-lab change made to enable it is **generic**.

### Task A.3: Encoding-correctness test harness

- [ ] Build a harness proving the exit reads `MsgType`/`Feedback`/`Report` correctly regardless of wire encoding (the classic exit bug). (spec §9)

## Track B — Outbound exit: re-assert COA/COD (blocked-by Track A)

### Task B.1: Set report bits on the streamed copy

- [ ] Sender-channel message exit that, on `MQXR_MSG`, sets `embedded MQMD.Report |= MQRO_COA | MQRO_COD` and leaves `Expiry` at `MQEI_UNLIMITED`. (spec §3.1)
- [ ] Prove (browse the copy at SVCQM's landing queue) the arriving message carries the report bits and the app's audit-queue put generated **no** local COA (app sets nothing).

## Track C — Inbound exit: separate the responses (blocked-by Track A)

### Task C.1: Redirect COA/COD reports by MsgType/Feedback

- [ ] Receiver-channel message exit that, when `MsgType == MQMT_REPORT` and `Feedback ∈ {MQFB_COA, MQFB_COD}`, rewrites `MQXQH.RemoteQName` to the owned report queue; else passthrough. (spec §3.2)
- [ ] **Resolve open question §10.1 live:** confirm the `RemoteQName` rewrite redirects the put; if not, implement the API-exit / `MQOD.ObjectName` fallback.
- [ ] Prove business replies still land on the business reply queue untouched.

## Track D — Real-path demonstrator (blocked-by Tracks B, C)

### Task D.1: End-to-end milestone capture on the SVCQM path

- [ ] Wire both exits + the report queue onto the real streaming path via the owned automation; run one instruction end-to-end.
- [ ] Measure `ARRIVED` (COA) and `DELIVERED` (COD) landing in the report queue; confirm **zero application changes** and no report noise on the business reply queue.
- [ ] Add Grafana panels exposing the two milestones on the existing board.
- [ ] **Operational validation:** re-apply onto a freshly rebuilt lab and confirm the exits + wiring replay cleanly (reproducibility gate). Seed as an `issue-validate` task at pickup.

## Track E — Capability writeup (blocked-by Track D)

### Task E.1: "When do we embrace exits?" decision framework

- [ ] Author the decision framework: platform lock-in, MQ-Appliance non-support, in-process risk, engineering cost, and the explicit **recommend-against-for-the-current-client** stance. (spec §8)
- [ ] Fold the corrected self-owning-bolt-on methodology (spec §7.1) into the repo/methodology docs.
- [ ] Cross-reference `docs/coa-cod-streaming-findings.md` and `docs/mqmd-streaming-fidelity.md` (this epic is the exit-based sequel to their NO-GO).

---

## Bookend tasks (seeded at creation)

- **#229 (documentation)** — this spec + plan; closed by this PR.
- **#230 (documentation-review)** — sweep human-facing docs when implemented; runs before the retrospective.
- **#231 (retrospective, terminal)** — authored via `epic-retrospective`; its merge closes the epic.

## Open questions

Carried in spec §10 (redirect fidelity, channel sharing, double-report handling, ownership surface, `PROPCTL`). Resolve at pickup, not now.
