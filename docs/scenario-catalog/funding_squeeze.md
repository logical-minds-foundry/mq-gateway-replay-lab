# Scenario: funding_squeeze

- **Isolating dimension:** `reason_and_msg_type`
- **Expected reason family:** `cash`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

A cash funding shortfall bites the delivery-versus-payment leg: matched trades pend-fail for lack of cash on the settlement directions (MT541/MT543).

## SWIFT / MQ signature

`IPRC//PACK` then `MTCH//MACH` then `SETT//PENF` carrying the `CMON` (cash) reason on the DvP message types — same pend-fail status as a securities lack, split into the cash family by the `:24B:` reason.

## Where it surfaces on the board

Register B **cash**-family deviation, cross-read with Register A's fails-by-msg_type panel: the concentration on the DvP directions is the second macro dimension (reason and msg_type) that pins the funding cause.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![funding_squeeze dashboard](img/funding_squeeze.png)

## Metric evidence (reproducible source of truth)

Per-reason-family deviation z-scores over the retained window:

| reason family | deviation z |
|---|---|
| cash | 24.4 |
| matching | -2.3 |
| rejection | 1.9 |
| settlement | -2.1 |

Captured macro segments:

| state | reason family | msg_type | count |
|---|---|---|---|
| failed | cash | 541 | 14 |
| failed | cash | 543 | 15 |
| failed | settlement | 541 | 1 |
| failed | settlement | 543 | 1 |
| rejected | rejection | 541 | 3 |
| settled | other | 541 | 112 |
| settled | other | 543 | 114 |
