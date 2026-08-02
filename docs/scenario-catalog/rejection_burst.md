# Scenario: rejection_burst

- **Isolating dimension:** `reason_family`
- **Expected reason family:** `rejection`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

A bad deploy or reference-data glitch makes a burst of instructions fail input validation outright — they never enter the settlement lifecycle at all.

## SWIFT / MQ signature

`IPRC//REJT` at input with a `NARR` reason, and no further status. Rejection is vanishingly rare on a normal day, so a 0→N burst is a loud step change.

## Where it surfaces on the board

Register B's rare-event amplification panel for **rejection**: because the baseline is near zero, the deltas/sparkline make the burst unmissable where a raw count would stay visually flat.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![rejection_burst dashboard](img/rejection_burst.png)

## Metric evidence (reproducible source of truth)

Per-reason-family deviation z-scores over the retained window:

| reason family | deviation z |
|---|---|
| cash | 6.8 |
| matching | -2.3 |
| rejection | 21.6 |
| settlement | -2.1 |

Captured macro segments:

| state | reason family | msg_type | count |
|---|---|---|---|
| failed | cash | 541 | 5 |
| failed | cash | 543 | 4 |
| failed | settlement | 541 | 1 |
| failed | settlement | 543 | 1 |
| rejected | rejection | 541 | 12 |
| rejected | rejection | 543 | 11 |
| settled | other | 541 | 112 |
| settled | other | 543 | 114 |
