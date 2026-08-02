# Scenario: csd_incident

- **Isolating dimension:** `time_and_state`
- **Expected reason family:** `settlement`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

A central securities depository settlement cycle stalls. A block of matched trades all pend-fail on settlement together, concentrated in one time window rather than spread across the day.

## SWIFT / MQ signature

`IPRC//PACK` then `MTCH//MACH` then a broad `SETT//PENF` with no distinguishing `:24B:` reason — a settlement pend-fail that arrives as a burst, not a trickle.

## Where it surfaces on the board

Register B settlement-family deviation, read together with Register C's expiry-window-stability timeseries: the oldest-age and audit-queue-depth curves rise as the stalled block ages in the window — the time-and-state tell.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![csd_incident dashboard](img/csd_incident.png)

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
