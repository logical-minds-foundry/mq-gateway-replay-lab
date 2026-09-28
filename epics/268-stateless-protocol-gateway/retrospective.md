# Stateless MQ protocol gateway — retrospective

- **Epic:** `logical-minds-foundry/mq-gateway-replay-lab#268`
- **Retrospective task:** `logical-minds-foundry/mq-gateway-replay-lab#271`
- **Retired under:** MQ portfolio wind-down — `logical-minds-foundry/.github#245`
  (task #304)
- **Outcome:** DROPPED at an early stage — bootstrap + planning done, the gateway
  itself never built; repo `mq-protocol-gateway` archived.
- **Date:** 2026-09-28

## §0 At a glance

We set out to build a **stateless MQ protocol gateway** — an outbound protocol
translator plus an inbound, ack-gated consumer with exclusive-open standby
takeover — as a worked demonstration of correct MQI usage and the correct
placement of message state, in a new C++ repo (`mq-protocol-gateway`). This was
our **first real C++ effort**. It reached repo bootstrap, a full spec + plan, and
partial empirical `imqi.hpp` MQI C++ API-surface findings — and then stopped. The
gateway proper (wire codec, inbound/outbound gateways, the stateful counter-example)
was never built. Under the strategic refocus it is dropped, and the C++ repo is
archived (not deleted) as the historical trace of that start.

**Delivered (merged):**

| Ref | What it did |
|---|---|
| #269 (task, replay-lab) | Spec + plan for the stateless protocol gateway |
| `mq-protocol-gateway#1` | Repo bootstrap — Vergil C++ pipeline green; began the `imqi.hpp` API-surface record |
| `mq-protocol-gateway#6` | Repo bound to the shared lab VM + CLAUDE.md |

**Dropped (closed won't-do) — never built:**

- Gateway build (`mq-protocol-gateway`): wire codec + conformance corpus (#2),
  inbound ack-gated gateway (#3), outbound open-once translator (#4), stateful
  counter-example (#5), complete the `imqi.hpp` API surface + spec §7 Q1 (#11)
- Design/reference docs & research (replay-lab): MQGET failure-mode analysis
  (#273), SWIFT ordering research (#274), ordering-semantics fix (#275),
  duplicate-handling reference (#279), reference sending app L1 (#280), gateway
  observability L14 (#281), MQI C++ API findings writeup L3 (#285), risk model L11
  (#286), argument document L4 (#287)
- Operational (replay-lab): deploy gateway binaries (#282), failure-envelope
  validation (#283), HA/DR drill validation (#284)
- Docs-review bookend (#270), and the **competing repo-rename task (#272)** — moot:
  `mq-protocol-gateway` is archived, and the rename that stands is
  `mq-gateway-replay-lab → mq-swift-settlement-lab`

- **Repos touched:** `mq-protocol-gateway` (bootstrap + partial findings),
  `mq-gateway-replay-lab` (epic docs + planned task graph).
- **Releases:** none.
- **Span:** opened 2026-08-19, retired 2026-09-28.

## §1 How the plan evolved

This is the earliest-stage of the dropped epics. The plan was complete and the C++
repo stood up with a green Vergil pipeline, but implementation barely began — the
one piece of real product knowledge produced was the empirical start on the
`imqi.hpp` MQI C++ API surface (what the C++ MQI actually exposes). The gateway's
own logic — codec, ack-gated inbound with exclusive-open standby, open-once
outbound, and the deliberately-broken counter-example that motivates the design —
never got written. The refocus behind wind-down #245 ended it before it became a
sunk cost.

## §2 Lessons learned

- **The `imqi.hpp` API-surface probe was the valuable early output.** Answering
  "what does the C++ MQI actually give you" empirically, before building on it, is
  the right first move for any future C++ MQI work — and that finding is preserved.
- **Standing up a whole new repo + C++ pipeline ahead of proving the core is a
  heavy on-ramp.** For an experimental demonstrator, more of the design could have
  been proven in the existing replay-lab before minting a separate C++ repo.

## §3 Compromises & tradeoffs

Nothing shipped, so there is no runtime debt. The correct-MQI-usage / message-state
demonstration remains **unproven in code** — an accepted gap, not hidden debt. The
one-way cost is simply the bootstrap effort on a repo that will now sit archived.

## §4 New problems & opportunities

- The partial `imqi.hpp` API-surface findings and the C++ pipeline scaffold survive
  in the archived `mq-protocol-gateway` repo — a genuine head start for any future
  C++ MQI work.
- The design itself (spec + plan) records a coherent argument for correct message-
  state placement in a gateway; it remains available if the idea is ever revived.

## §5 What's next

- Dropped under wind-down #245; **no follow-on is scheduled.**
- `mq-protocol-gateway` is **archived** read-only under wind-down task #261 (the
  first C++ repo, kept as a historical/archaeological trace). Its design and
  findings are preserved there and referenced from this retrospective; the
  replay-lab spec/plan remain in `epics/268-stateless-protocol-gateway/`.
