# Scenario: counterparty_outage

- **Isolating dimension:** `reason_family`
- **Expected reason family:** `matching`
- **Seed:** `0` (metric dump is byte-reproducible)

## What broke

A counterparty stops responding mid-session. Instructions are accepted at input but never progress to matched, so a cluster of trades hangs unmatched while the rest of the day settles normally.

## SWIFT / MQ signature

`IPRC//PACK` (accepted) followed by `MTCH//NMAT` (never matches) — and then silence. The instruction audit copies are present; their status audit copies stall on the matching hop.

## Where it surfaces on the board

Register B — the per-reason-family deviation panel for the **matching** family lifts above its baseline z-band while efficiency (Register A) barely dips, because the concentration is one family, not the whole book.

## Live snapshot

Captured against the live audit queues by the validation bookend:

![counterparty_outage dashboard](img/counterparty_outage.png)

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
