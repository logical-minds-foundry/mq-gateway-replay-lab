# mq-gateway-replay-lab

Lab demo of resilient, replayable MQ message-gateway patterns: an MQ-native audit copy
(a two-put-under-syncpoint outbound copy + a mirrored inbound copy) with browse-based
reconciliation and observability, proving "replay the unconfirmed" against a live
HA/DR queue manager.

## Table of Contents

- [Status](#status)
- [Overview](#overview)
- [Documentation](#documentation)
- [Getting Started](#getting-started)
- [License](#license)

## Status

Early development

## Overview

An independent lab experiment that bolts onto the running queue-manager
infrastructure of `mq-resiliency-lab-for-linux` (it owns no VM — see
`[vm] shared_from` in `vergil.toml`) and demonstrates an MQ-native audit copy with
browse-based reconciliation and observability.

It carries **two parallel, independent designs** over the same MQ-native
streaming-audit mechanism:

- **Request/reply confirmation tracking** — correlate replies to requests through the
  MQ message descriptor (request id → reply correlation id).
- **SWIFT MT540–548 settlement tracking** — parse the message **body** to correlate
  and fold a multi-day securities-settlement lifecycle, served by a **streaming,
  stateful collector** (read once, maintain incrementally, snapshot decoupled from the
  scrape) that has been scale-hardened to hundreds of thousands of instructions and
  characterised client-vs-bindings.

## Documentation

- [`docs/mq-native-settlement-tracking.md`](docs/mq-native-settlement-tracking.md) —
  the SWIFT settlement-tracking recommendation (design, algorithms, live evidence,
  scale/performance, and observability).
- [`docs/mq-native-confirmation-tracking.md`](docs/mq-native-confirmation-tracking.md) —
  the request/reply confirmation-tracking recommendation.
- [`docs/swift-settlement-reference.md`](docs/swift-settlement-reference.md) — the
  cited public ISO 15022 / ISO 20022 field and status reference.
- [`docs/coverage-exceptions-audit.md`](docs/coverage-exceptions-audit.md) — the
  coverage-exception audit (the pipeline runs at 100% branch + line, zero omits).
- Authoritative designs: request/reply in `logical-minds-foundry/.github`
  (`epics/49-resilient-mq-gateway/`); SWIFT settlement in
  `epics/65-swift-settlement-tracking/` in this repo.

### Streaming-audit gateway — strategic-risk findings (epic #105)

Three deliverables assess the streaming-audit design — where *the counterparty
does not receive what the application put; it receives the queue manager's
streamed derivative*. Headline outcomes:

- **MQ-native COA/COD are incompatible with a streaming-audit gateway.** Two
  independent showstoppers: streaming **strips the report options** from the
  streamed copy (no `ARRIVED`/`DELIVERED` report is ever generated at the
  counterparty), and the single MQMD `ReplyToQ` is **already committed to SWIFT
  business responses** (MT548), so reports cannot be isolated to a queue of our
  own. Verdict: definitive NO-GO.
- **The streamed copy is byte-/value-identical to the retained original in every
  MQMD field except `Expiry`**, empirically confirmed on the live lab (one probe,
  original vs. copy matched by `MsgId`). `Expiry` becomes `MQEI_UNLIMITED` on the
  copy — the desired direction (the counterparty receives no expiry). `MsgId`,
  origin/identity context, persistence, priority, CCSID (`1208`), and `ReplyToQMgr`
  all measured identical.
- **Design invariant to preserve:** **no `CAPEXPRY` on the stream target**
  (`GW.SEC.INSTRUCT.REMOTE`) — that is what keeps the copy's expiry unlimited.

Deliverables:

- [`docs/coa-cod-streaming-findings.md`](docs/coa-cod-streaming-findings.md) —
  the COA/COD × streaming gating spike (definitive NO-GO, two showstoppers).
- [`docs/mqmd-streaming-fidelity.md`](docs/mqmd-streaming-fidelity.md) — the
  field-by-field MQMD fidelity account, with the live-lab measured diff.
- [`docs/gateway-design-risk-register.md`](docs/gateway-design-risk-register.md) —
  the living strategic-risk register, updated with the measured evidence.

## Getting Started

Sessions run inside the shared lab VM. Validate with `vrg-container-run -- vrg-validate`.

## License

MIT — see [LICENSE](LICENSE).
