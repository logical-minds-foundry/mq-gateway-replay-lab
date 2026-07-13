# mq-gateway-replay-lab

Lab demo of resilient, replayable MQ message-gateway patterns: an MQ-native audit copy
(a two-put-under-syncpoint outbound copy + a mirrored inbound copy) with browse-based
reconciliation and observability, proving "replay the unconfirmed" against a live
HA/DR queue manager.

## Table of Contents

- [Status](#status)
- [Overview](#overview)
- [Getting Started](#getting-started)
- [License](#license)

## Status

Early development

## Overview

An independent lab experiment that bolts onto the running queue-manager
infrastructure of `mq-resiliency-lab-for-linux` (it owns no VM — see
`[vm] shared_from` in `vergil.toml`) and demonstrates an MQ-native audit copy with
browse-based reconciliation and observability. The authoritative design lives in
`logical-minds-foundry/.github` at `epics/49-resilient-mq-gateway/`.

## Getting Started

Sessions run inside the shared lab VM. Validate with `vrg-container-run -- vrg-validate`.

## License

MIT — see [LICENSE](LICENSE).
