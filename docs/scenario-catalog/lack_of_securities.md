# Scenario: lack_of_securities

- **Isolating dimension:** `reason_family`
- **Expected reason family:** `settlement`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

Delivering trades cannot settle because the securities are not in the account — a classic lack-of-securities fail chain concentrated on one book.

## SWIFT / MQ signature

`IPRC//PACK` then `MTCH//MACH` then `SETT//PENF` carrying the `LACK` reason code. The reason is the only thing separating this from a cash squeeze — same `SETT//PENF` status, different `:24B:` reason.

## Where it surfaces on the board

Register B — the **settlement** reason-family deviation panel. The `LACK` reason routes the pend-fail into the settlement bucket, so the settlement family's z-score is what crosses the red threshold.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![lack_of_securities dashboard](img/lack_of_securities.png)

## Metric evidence (reproducible source of truth)

Per-reason-family deviation z-scores over the retained window:

| reason family | deviation z |
|---|---|
| cash | 6.8 |
| matching | -2.3 |
| rejection | 1.9 |
| settlement | 5.2 |

Captured macro segments:

| state | reason family | msg_type | count |
|---|---|---|---|
| failed | cash | 541 | 5 |
| failed | cash | 543 | 4 |
| failed | settlement | 541 | 10 |
| failed | settlement | 543 | 12 |
| rejected | rejection | 541 | 3 |
| settled | other | 541 | 112 |
| settled | other | 543 | 114 |
