# Scenario: silent_degradation

- **Isolating dimension:** `reason_family_trend`
- **Expected reason family:** `matching`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

A slow, sustained rise in matching fails — no single spike, just a creeping deterioration over successive slices of the day. The hard case: any one window looks unremarkable.

## SWIFT / MQ signature

Successive `IPRC//PACK` then `MTCH//NMAT` slices, each a little larger than the last. No burst clears the z-band on its own; the signal is the trend, not any one window.

## Where it surfaces on the board

Register B's EWMA-trend panel for the **matching** family: the exponentially weighted residual accumulates the sustained drift across sweeps, surfacing what the per-window z-score alone washes out.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![silent_degradation dashboard](img/silent_degradation.png)

## Metric evidence (reproducible source of truth)

Per-reason-family deviation z-scores over the retained window:

| reason family | deviation z |
|---|---|
| cash | 6.8 |
| matching | 7.0 |
| rejection | 1.9 |
| settlement | -2.1 |

Captured macro segments:

| state | reason family | msg_type | count |
|---|---|---|---|
| acknowledged | matching | 541 | 10 |
| acknowledged | matching | 543 | 11 |
| failed | cash | 541 | 5 |
| failed | cash | 543 | 4 |
| failed | settlement | 541 | 1 |
| failed | settlement | 543 | 1 |
| rejected | rejection | 541 | 3 |
| settled | other | 541 | 112 |
| settled | other | 543 | 114 |
