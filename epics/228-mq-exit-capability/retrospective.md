# MQ message-exit engineering capability — retrospective

- **Epic:** `logical-minds-foundry/mq-gateway-replay-lab#228`
- **Retrospective task:** `logical-minds-foundry/mq-gateway-replay-lab#231`
- **Retired under:** MQ portfolio wind-down — `logical-minds-foundry/.github#245`
  (task #303)
- **Outcome:** DROPPED — planned, never built; design and rationale preserved.
- **Date:** 2026-09-20

## §0 At a glance

We set out to establish an **MQ message-exit engineering capability** — a worked
example proving message exits could carry the COA/COD (confirm-on-arrival /
confirm-on-delivery) telemetry that #105 had ruled out of the streaming-audit
design, and that #49 had deferred ("no magic exits unless proven"). What shipped
was the **plan, not the capability**: a spec and plan establishing the approach,
with no implementation tasks ever filed. Under the portfolio wind-down it is
dropped — message exits are a niche corner case that only made sense as follow-on
to the gateway/replay direction now being retired.

**Work delivered (planning only):**

| Ref | What it did |
|---|---|
| #229 (docs task) | Published `spec.md` + `plan.md` for the exit capability (successor to #105's NO-GO; extends #65's streaming-audit architecture) |
| #230 (closed won't-do) | Docs-review bookend — not pursued |

- **Repos touched:** `mq-gateway-replay-lab` (planning docs only — no code).
- **Implementation tasks filed:** 0.
- **Releases:** none.
- **Span:** opened 2026-08-03, docs merged same day, retired 2026-09-20.

## §1 How the plan evolved

No execution phase — the delta is again total: a planned capability that never
reached code. The epic existed to answer a specific, real question left open by
#105 (streaming strips report options from the streamed copy, so COA/COD cannot
ride the streaming-audit design non-intrusively) — a message exit was the
candidate escape hatch. The strategic refocus behind wind-down #245 removed the
reason to pursue it: without the gateway/replay follow-on, a bespoke exit example
has no home.

## §2 Lessons learned

- **The #105 → #228 chain is worth preserving as reasoning, not code.** It records
  *why* the streaming-audit design cannot carry report-option telemetry and what
  the alternative would have been — useful to a future reader even though neither
  was built.
- **Niche corner cases are the right things to defer.** A message-exit worked
  example is deep, narrow MQ engineering; parking it (rather than building it
  speculatively) was the correct instinct, now made permanent.

## §3 Compromises & tradeoffs

None in code — nothing was built. The COA/COD telemetry question remains formally
unanswered-by-exit; that is an accepted, documented gap, not hidden debt.

## §4 New problems & opportunities

- The durable takeaway is #105's finding itself (streaming strips report options),
  which stands independently of this epic and informs any future audit design.
- The exit approach remains documented in this repo's spec/plan if a genuine need
  for MQ message exits ever arises.

## §5 What's next

- Dropped under wind-down #245; **no follow-on is scheduled.**
- Design and rationale preserved in `epics/228-mq-exit-capability/{spec.md,plan.md}`
  in this repo, which is being published as the public SWIFT example lab.
