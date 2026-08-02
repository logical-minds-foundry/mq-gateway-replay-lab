# Scenario: stale_ssi

- **Isolating dimension:** `reason_family`
- **Expected reason family:** `matching`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

Stale standing settlement instructions drive a run of matching disagreements: the counterparty rejects the security detail, so trades match-fail rather than settle.

## SWIFT / MQ signature

`IPRC//PACK` then `MTCH//NMAT` carrying a `DSEC` (security-detail) disagreement reason — accepted, then rejected at matching on a detail mismatch.

## Where it surfaces on the board

Register B — the **matching** reason-family deviation panel. Shares the family with the counterparty outage, but the disagreement reason and the immediate (non-stalling) match-fail distinguish the cause on drill-down.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![stale_ssi dashboard](img/stale_ssi.png)

## Metric evidence (reproducible source of truth)

Per-reason-family deviation z-scores over the retained window:

| reason family | deviation z |
|---|---|
| cash | 6.8 |
| matching | 6.6 |
| rejection | 1.9 |
| settlement | -2.1 |

Captured macro segments:

| state | reason family | msg_type | count |
|---|---|---|---|
| acknowledged | matching | 541 | 9 |
| acknowledged | matching | 543 | 11 |
| failed | cash | 541 | 5 |
| failed | cash | 543 | 4 |
| failed | settlement | 541 | 1 |
| failed | settlement | 543 | 1 |
| rejected | rejection | 541 | 3 |
| settled | other | 541 | 112 |
| settled | other | 543 | 114 |
