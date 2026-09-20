# Generic MQ request-reply reconciliation tool — retrospective

- **Epic:** `logical-minds-foundry/mq-gateway-replay-lab#59`
- **Retrospective task:** `logical-minds-foundry/mq-gateway-replay-lab#308`
- **Retired under:** MQ portfolio wind-down — `logical-minds-foundry/.github#245`
  (task #302)
- **Outcome:** DROPPED — planned, never built; design preserved.
- **Date:** 2026-09-20

## §0 At a glance

We set out to build a **generic, standalone MQ request-reply reconciliation
tool** — a reusable tool that correlates replies to requests through the message
descriptor, extracted from the replay-lab's own confirmation-tracking work. What
shipped was the **plan, not the tool**: a complete, reviewed spec and TDD plan,
captured deliberately as an *aspirational parked epic* and never entered into
implementation. Under the portfolio wind-down it is now formally dropped, with
the design left intact in-repo for any future revival.

**Work delivered (planning only):**

| PR / ref | What it did |
|---|---|
| #63 (task #60) | Published `spec.md` + `plan.md` (brainstorm → pushback → writing-plans → alignment; 11 TDD tasks, T0 bootstrap → T10 order-fulfillment example) |
| task #64 | Corrected the spec to record public visibility (missed in #63) |
| #61 (closed won't-do) | Follow-on brainstorm bookend — not pursued |
| #62 (closed won't-do) | Docs-review bookend — not pursued |

- **Repos touched:** `mq-gateway-replay-lab` (planning docs only — no code).
- **Implementation tasks filed:** 0 (parked before any were created).
- **Releases:** none.
- **Span:** opened 2026-07-13, parked the same day, retired 2026-09-20.

## §1 How the plan evolved

There was no execution phase, so there is no plan-vs-actual delta of the usual
kind — the delta *is* the whole story: a fully-planned epic that never moved from
plan to code. Two decision points shaped it. First, at creation the design was
captured explicitly as **parked** ("aspirational epic captured to preserve the
design, not to be built now") — a conscious choice to bank the thinking rather
than build immediately. Second, the strategic refocus behind wind-down #245
converted "not now" into "not at all for the foreseeable future," so the epic is
dropped rather than left dangling.

## §2 Lessons learned

- **Parking a design as a first-class epic worked.** The spec and plan survive as
  a coherent, reviewed artifact anyone can pick up — far better than the idea
  living only in someone's head or a scratch note.
- **Running the full pipeline on something never built is a double-edged
  investment.** The brainstorm → pushback → plan → alignment discipline produced
  a genuinely reusable design, but for a *speculative* tool that cost real effort
  up front. Worth it here because the artifact is durable; worth weighing next
  time before fully planning work that may not be scheduled.

## §3 Compromises & tradeoffs

None incurred in code — nothing was built, so there is no technical debt to carry.
The only "compromise" is the decision to drop a fully-planned direction rather
than finish it; that is a deliberate strategic call recorded here, not an
accident.

## §4 New problems & opportunities

- The concrete request-reply correlation idea did not go to waste: a working
  **request/reply confirmation-tracking** example ships in this same repo (the
  SWIFT example lab), over the shared MQ-native streaming-audit mechanism. The
  *generic standalone tool* is what was dropped, not the pattern.
- The reviewed design remains available as a starting point should a genuine need
  for a standalone recon tool ever arise.

## §5 What's next

- Dropped under wind-down #245; **no follow-on is scheduled.**
- The design is preserved in `epics/59-generic-rr-recon/{spec.md,plan.md}` in this
  repo, which is itself being published as the public SWIFT example lab
  (`mq-swift-settlement-lab`). Re-entry, if ever, starts at the plan's Task 0
  (stand up a public `mq-request-reply-recon` repo) and would likely revisit the
  design wholesale first.
