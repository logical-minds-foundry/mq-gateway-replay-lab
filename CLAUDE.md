# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

**Project name**: mq-gateway-replay-lab

## Validation

```bash
vrg-container-run -- vrg-validate
```

## Project purpose

This repo is an **independent lab experiment** demonstrating resilient,
replayable MQ message-gateway patterns — an MQ-native audit copy (a
two-put-under-syncpoint outbound copy + a mirrored inbound copy) with
browse-based reconciliation and observability that proves "replay the
unconfirmed" against a live HA/DR queue manager.

It bolts onto the **running queue-manager infrastructure of
`mq-resiliency-lab-for-linux`** — it owns no VM (see `[vm] shared_from` in
`vergil.toml`); its sessions run inside that lab's box. The authoritative
design lives in `logical-minds-foundry/.github` at
`epics/49-resilient-mq-gateway/{spec.md,plan.md}` (epic #49).

## Hard rules

- **Anonymization (absolute).** No employer/client-identifiable content in any
  file, commit, diagram, or dummy data — this is a *generic trading back-office
  gateway* problem. Generic vocabulary only: "the application team", "the
  app-team lead", "the alternate log hosts", "Counterparty A/B/…", invented
  queue/host names. This repo is **private and client-adjacent** — deliberately
  **not** part of the public `mq-resiliency-*` OSS family.
- **Self-owning bolt-on rule** (formerly "parasite / boundary rule"). This repo
  is a **self-owning bolt-on** on the shared lab. It **may change the underlying
  lab whatever it needs to** — the rule is *not* "don't touch it." What it
  **must** do: **automate 100%** of (a) its own setup and (b) **every change it
  makes to the underlying lab**, and **own** those changes so they **replay
  cleanly on a from-scratch rebuild** (reproducibility is the goal). Changes that
  must live in the **underlying/generic lab's** code stay **generic and
  general-purpose** — the base lab never carries bolt-on-specific knowledge, only
  generic capabilities the bolt-on builds on. Automated **teardown** is
  good-citizen nice-to-have, lower priority than setup + reproducibility. This is
  a **scale-of-one** environment: the rule enforces disciplined, reproducible,
  fully-automated config ownership — not protection of other tenants. The
  bolt-on's own Ansible + `gwlab` CLI *are* that ownership mechanism. Adding, say,
  an `MSGEXIT` to an existing lab channel is fine when it is automated and owned
  (it may invalidate an underlying test — acceptable). Still: **declare-and-verify**
  what the repo doesn't own before relying on it, and a failed precondition is a
  loud signal, not a silent fix.
- **Reuse none of the lab's tooling.** `mqlab` and the HA/DR `validate`
  framework stay the lab's. This repo ships its **own** small CLI (`gwlab`) and
  its own responder app.
- **MQ-native & minimalist.** The queue-manager-native audit copy is the
  mechanism: a streaming queue (`STREAMQ … STRMQOS(MUSTDUP)`) writes the copy
  atomically, same MsgId, zero application logic; an explicit two-put-in-one-UOW
  is the documented fallback where streaming is unavailable. AMS is out. No new
  vendor products.
- **The one-UOW rule.** Outbound: the audit PUT shares the transmit PUT's unit
  of work, with the **same MsgId** on both. Never two separate commits — a crash
  between them is silent loss.

<!-- vergil:template:claude-md:begin -->
## Memory management

Memory is allowed with human approval. The authoritative policy is in
the user's global `~/.claude/CLAUDE.md` — agents must propose memory
writes and suggest a destination (repo memory, global CLAUDE.md, or
plugin/skill issue) before writing. See that file for the full
workflow.

Available skills:
- `/vergil:memory-init` — set up or update the policy header
  in a project's `MEMORY.md`.
- `/vergil:memory-audit` — structured collaborative review
  of memory files.

## Parallel AI agent development

This repository supports running multiple Claude Code agents in parallel via
git worktrees. The convention keeps parallel agents' working trees isolated
while preserving shared project memory (which Claude Code derives from the
session's starting CWD).

**Canonical spec:**
[`vergil-tooling/docs/specs/worktree-convention.md`](https://github.com/vergil-project/vergil-tooling/blob/develop/docs/specs/worktree-convention.md)
— full rationale, trust model, failure modes, and memory-path implications.
The canonical text lives in `vergil-tooling`; this section is the local
on-ramp.

### Structure

```text
<project-root>/                              ← sessions ALWAYS start here
  .git/
  CLAUDE.md, …                               ← main worktree (usually `develop`)
  .worktrees/                                ← container for parallel worktrees
    issue-<N>-<short-slug>/                  ← worktree on feature/<N>-<short-slug>
    …
```

### Rules

1. **Sessions always start at the project root.**
   Never start Claude from inside `.worktrees/<name>/`. This keeps the
   memory-path slug stable and shared.
2. **Each parallel agent is assigned exactly one worktree.** The session
   prompt names the worktree (see Agent prompt contract below).
   - For Read / Edit / Write tools: use the worktree's absolute path.
   - For Bash commands that touch files: `cd` into the worktree first,
     or use absolute paths.
3. **The main worktree is read-only.** All edits flow through a worktree
   on a feature branch — the logical endpoint of the standing
   "no direct commits to develop" policy.
4. **One worktree per issue.** Don't stack in-flight issues. When a
   branch lands, remove the worktree before starting the next.
5. **Naming: `issue-<N>-<short-slug>`.** `<N>` is the GitHub issue
   number; `<short-slug>` is 2–4 kebab-case tokens.

### Agent prompt contract

When launching a parallel-agent session, use this template (fill in the
placeholders):

```text
You are working on issue #<N>: <issue title>.

Your worktree is: <project-root>/.worktrees/issue-<N>-<slug>/
Your branch is:   feature/<N>-<slug>

Rules for this session:
- Do all git operations from inside your worktree:
    cd <absolute-worktree-path> && vrg-git <command>
- For Read / Edit / Write tools, use the absolute worktree path.
- For Bash commands that touch files, cd into the worktree first
  or use absolute paths.
- Do not edit files at the project root. The main worktree is
  read-only — all changes flow through your worktree on your
  feature branch.
- When you need to run validation, run it from inside your worktree
  (vrg-container-run mounts the current directory).
```

All fields are required.

## Shell command policy

Use `vrg-git` instead of `git` for all git operations. Use `vrg-gh`
instead of `gh` for all GitHub CLI operations. These wrappers enforce
subcommand allowlists, flag deny lists, and credential selection.

Raw `git` and `gh` are denied by the permission model. If a command
is not available through the wrappers, explain the situation to the
human who can run it directly via `! <command>` in the prompt.

## Validation

```bash
vrg-container-run -- vrg-validate
```

This is the **only** validation command. Do not run individual linters,
formatters, or other tools outside of `vrg-validate`. If a tool is not
invoked by `vrg-validate`, it is not part of the validation pipeline.
<!-- vergil:template:claude-md:end -->
