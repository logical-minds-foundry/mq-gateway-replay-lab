# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Project overview

`mq-gateway-replay-lab` is an **independent lab experiment** demonstrating
resilient, replayable MQ message-gateway patterns: an MQ-native audit copy
(outbound via a streaming queue, plus a mirrored inbound copy) with
browse-based reconciliation and observability that proves "replay the
unconfirmed" against a live HA/DR queue manager.

It bolts onto the **running queue-manager infrastructure of
`mq-resiliency-lab-for-linux`** and owns no VM (see `[vm] shared_from` in
`vergil.toml`); its sessions run inside that lab's box. It ships its own CLI,
`gwlab` (`src/gwlab/`), plus a settlement collector (`gwlab-collector`),
Ansible for its setup (`ansible/`) and demo drivers (`demo/`).

Authoritative designs:

- request/reply confirmation tracking: `logical-minds-foundry/.github`,
  `epics/49-resilient-mq-gateway/{spec.md,plan.md}`;
- SWIFT settlement tracking: `epics/65-swift-settlement-tracking/` in this
  repo.

The design write-ups and findings live in `docs/`; the README's
[Documentation](README.md#documentation) section indexes them.

## Hard rules

- **Anonymization (absolute).** No employer/client-identifiable content in any
  file, commit, diagram, or dummy data — this is a *generic trading back-office
  gateway* problem. Generic vocabulary only: "the application team", "the
  app-team lead", "the alternate log hosts", "Counterparty A/B/…", invented
  queue/host names. This repo is **private and client-adjacent** — deliberately
  **not** part of the public `mq-resiliency-*` OSS family.
- **Self-owning bolt-on.** This repo may change the underlying lab however it
  needs to, but it must **automate 100%** of (a) its own setup and (b) every
  change it makes to the underlying lab, and **own** those changes so they
  replay cleanly on a from-scratch rebuild. Its own Ansible and `gwlab` are
  that ownership mechanism. Changes that must live in the underlying lab's code
  stay **generic** — the base lab never carries bolt-on-specific knowledge.
  Automated teardown is a lower-priority nice-to-have. **Declare and verify**
  what this repo doesn't own before relying on it; a failed precondition is a
  loud signal, not a silent fix.
- **Reuse none of the lab's tooling.** `mqlab` and the HA/DR `validate`
  framework stay the lab's. This repo ships its **own** small CLI (`gwlab`) and
  its own responder app.
- **MQ-native & minimalist.** The queue-manager-native audit copy is the
  mechanism: a streaming queue (`STREAMQ … STRMQOS(MUSTDUP)`) writes the copy
  atomically, same MsgId, zero application logic; an explicit two-put-in-one-UOW
  (`send_via_two_put`) is the documented fallback where streaming is
  unavailable. AMS is out. No new vendor products.
- **The one-UOW rule.** In the two-put fallback, the audit PUT shares the
  transmit PUT's unit of work, with the **same MsgId** on both. Never two
  separate commits — a crash between them is silent loss.

<!-- vergil:template:claude-md:begin -->
## Memory

Never write, edit or delete memory files without explicit human approval.
Propose the entry and where it belongs first, then wait for the decision.

## Worktrees

- Sessions always start at the project root, never inside `.worktrees/`.
- One worktree per issue, `.worktrees/issue-<N>-<slug>/` on `feature/<N>-<slug>`, or per batch,
  `.worktrees/issue-<N>-batch-<slug>/` on `feature/<N>-batch-<slug>` (`<N>` its first issue). Remove it once its branch lands.
- The main worktree is read-only, bar ignored `build/` scratch; every edit goes through a worktree.
- Read, Edit and Write use the worktree's absolute path. Bash commands `cd`
  into the worktree first or use absolute paths.
- The full convention: <https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md>.
- Brief each parallel sub-agent with its [Agent prompt contract](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md#agent-prompt-contract).

## Git and GitHub

Use `vrg-git` and `vrg-gh` only; the hook guard blocks raw `git` and `gh`. If a
command is not available through the wrappers, explain why and ask the human to
run it with `! <command>` in the prompt.

## Validation

- Every linter, formatter and test runs through `vrg-container-run`, never directly on the host.
- Running one tool ad hoc while working is fine: `vrg-container-run -- markdownlint --fix <file>`, one test file.
- Work is finished only when a full `vrg-container-run -- vrg-validate` passes; it is the gate before handoff.

## PR handoff

- Agents never run `vrg-submit-pr`. Submitting, merging and finalizing a PR are
  human actions.
- Hand off with `vrg-pr-workflow report-ready --issue <N> --title --summary
  --notes`. It pushes the branch itself. Leave `--linkage` at its default (`Ref`).
- A delta touching `.github/workflows/**` makes GitHub refuse the agent push.
  That is expected: report-ready records the state anyway and the human's
  `vrg-submit-pr` pushes the branch.
- After report-ready the branch is frozen: no new commits and no push that
  advances it; more work is a new follow-up issue. Re-running report-ready to
  fix the prose is allowed. Submitting with `vrg-submit-pr` lifts it, as does `vrg-pr-workflow unfreeze`.
- The report-ready title, summary and notes are public (the relay ref is
  world-readable): never put secrets in them.

## Local settings

`.claude/settings.local.json` must stay absent or exactly `{}`; the agent tools
refuse otherwise. See the [ownership model](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/site/docs/guides/claude-config-ownership.md).

## Engineering principles

- Correctness over cost over speed. Never ship known-broken code with a
  follow-up fix; if something is wrong, fix it now.
- No silent failures: no swallowed exceptions, no `except: return ""`, no
  fallbacks that hide errors. A failure hidden in code becomes a hallucination
  at the AI layer.

## paad

paad usage policy and deviations:
<https://github.com/vergil-project/vergil-claude-plugin/blob/develop/docs/paad-policy.md>.
<!-- vergil:template:claude-md:end -->
