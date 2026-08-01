# Spec — Realistic SWIFT settlement traffic & signal-in-noise observability

**Epic:** logical-minds-foundry/mq-gateway-replay-lab#175
**Status:** post-pushback draft (for human review)

---

## 1. Summary

Tune the SWIFT settlement collector's **traffic** and **dashboard** from
mechanism-demonstration toward **real-world realism**, so the dashboard delivers a
**high signal-to-noise ratio** for detecting genuine settlement failures **entirely
out-of-band of the trading application**, while reading only the **bare minimum**
non-sensitive matching metadata.

Two coupled problems are solved:

1. **Realistic traffic** — replace the current inflated synthetic failure mix with a
   calibrated baseline (from sourced industry data), plus a library of reproducible
   **failure episodes** modelling real-world incidents.
2. **Signal-in-noise detection** — rework the dashboard so a rare, concentrated real
   incident (a fraction of a percent) surfaces *above* the diffuse background noise
   rather than being masked by it — using only **macro, non-sensitive** dimensions.

Each of seven failure scenarios ships as a **reproducible episode** with a
**documented snapshot** — a demo catalog, not just a live trick.

Absorbs the dashboard/traffic-realism work once imagined for the abandoned COA/COD
epic (#105), whose code was reverted in #174 (COA/COD needs app-side
`MQRO_COA`/`MQRO_COD` report options that violate the transparency invariant, and is
incompatible with the streaming-queue mechanism). That finding belongs to #105's
wind-down, not here.

## 2. Motivation — the science problem, and the minimal-footprint thesis

The current dashboard proves the *mechanism* by flooding the display with synthetic
failures. Operational reality is the inverse: a well-run flow is **mostly green**, and
the challenge is spotting a fraction-of-a-percent signal buried in background noise. A
bar chart that plots `settled` alongside the exception states makes the exceptions
un-renderable — the signal drowns in noise.

Two principles drive the design:

- **Real incidents are *concentrations*; routine noise is *diffuse*.** Background
  noise (routine settlement fails) is smeared evenly across time and message types. A
  real incident **lights up one macro dimension** — one reason-code family, one
  message type, one settlement window. So the dashboard's job is **not** "count
  failures" (the settlement-fail baseline wins that and masks everything) — it is
  **"detect deviation-from-baseline per macro dimension."**

- **Minimal footprint is the selling point, not a limitation.** This layer reads only
  the correlation/matching fields it needs to fold the lifecycle. It deliberately does
  **not** profile *who* you trade with (counterparty) or drill into *which* security
  (instrument) — that is the application's responsibility and its sensitive detail.
  The macro view detects the *class* of problem and **points to where to look**; the
  full-detail system that already holds the sensitive data does the drill-down. A
  plumbing tool that quietly built counterparty-level intelligence would be a
  different, more invasive product.

## 3. Goals / non-goals

**Goals**
- A calibrated, realistic steady-state traffic baseline, sourced from published
  industry failure rates.
- Seven named, deterministic, reproducible failure episodes.
- Collector-side anomaly scoring (per-macro-dimension deviation) with trivially
  bounded metric cardinality.
- A three-register dashboard: normal-day, exceptional-event detection, audit-queue
  infrastructure health.
- A documented snapshot catalog — one captured, explained snapshot per scenario.
- A reusable observability-primitives reference.
- A committed calibration reference doc (sourced research, data-vs-judgment separated).

**Non-goals**
- **No per-counterparty and no per-instrument (ISIN) segmentation or metrics** — the
  minimal-footprint invariant (§4). Correlation references (`:20C::SEME`/`RELA`) are
  used only as the join key, never as an analytic dimension.
- No change to the collector's core streaming architecture (cold-load + incremental
  fold, persistent cursor, atomic snapshot) — this epic *adds* macro segmentation and
  scoring, it does not re-plumb the collector.
- No app-side changes of any kind (transparency invariant).
- No learned/ML baselines — baselines are *configured* from the research, for
  reproducibility (learned baselines are a noted future opportunity).
- No new vendor products; AMS remains out.

## 4. Invariants

- **Transparency.** The trading app is untouched: same queue names, same put/get, no
  report options, no awareness of the audit/observability layer.
- **Out-of-band.** The collector reads only the `GW.SEC.*` audit copies, never the
  app's live queues.
- **Minimal footprint / macro-only.** The collector exposes only structural,
  non-sensitive dimensions: `state`, `msg_type`, `reason_family`, and `time`. It never
  segments on counterparty or instrument. Asset class is a **generation-side realism
  input only** — the generator builds asset-class books so the aggregate stream is
  realistic, but the collector reports aggregate macro health and does not segment by
  it.
- **Bounded cardinality.** Every label domain is a small bounded set by construction
  (see §8); no unbounded dimension exists anywhere in the metric surface.
- **Parasite boundary — with an endorsed exception.** Configure only what we own; the
  QM and lab remain declare-and-verify. **Exception (endorsed):** where faithfully
  simulating a real-world use case requires a feature the core lab would already have
  — specifically MQ Prometheus observability covering *all* business queues — we may
  add that exporter config for the `GW.SEC.*` queues. We never mutate the QM's
  messaging config or restart it.

## 5. Phase structure

One epic, plan sequenced in **two phases**, so Phase 1 lands as a standalone-valuable
deliverable before Phase 2 begins:

- **Phase 1 — Realistic traffic + honest dashboard.** Units 1, 8, the Register-A/C
  dashboard shell, and the calibration reference doc. *Delivers alone:* a realistic,
  calibrated demo board — mostly-green efficiency headline, per-reason-family and
  per-message-type fails on their own scale, plus the audit-queue infra-health
  register. No anomaly scoring yet.
- **Phase 2 — Anomaly detection + scenario catalog.** Units 2, 3, 4, Register-B, 6, 7.
  *Builds on Phase 1:* the deviation scoring, the 7 injectable scenarios, the snapshot
  catalog, and the reusable-primitives note.

## 6. Design — eight units

### Unit 1 — Calibrated traffic generator
*Extends `src/gwlab/swift/generator.py`, `directive.py`, `corpus.py`.*

Introduce **asset-class books** — `equity`, `govvie`, `etf` — each emitting at its own
sourced steady-state outcome mix (§8), blended into one realistic aggregate stream. A
`mode` selector: `mechanism` (today's inflated teaching mix, preserved as the current
default) vs `realistic` (the calibrated baseline). Deterministic (seeded RNG; no
wall-clock/`random` in the committed path). Asset class is a generation-side concept
only — it shapes the aggregate realism; it is not carried as a collector dimension.

### Unit 2 — Scenario injection harness
*New: `src/gwlab/swift/scenarios.py`.*

A library of the seven named episodes (§7). Each is a pure, parameterized function
that, given the baseline stream and a seed, **layers a concentrated perturbation**
biasing a chosen macro dimension (a reason family, a message type, a time window)
toward a failure signature while leaving the rest of the flow at baseline. Episodes are
deterministic and composable. Each declares its **isolating macro dimension** and
**expected signature**, which the catalog and tests assert against.

### Unit 3 — Collector anomaly scoring
*New: `src/gwlab/swift/stream/anomaly.py`; extends `stream/state.py`.*

`SettlementState` already folds each instruction incrementally. Add **macro-segment
accounting**: as each instruction reaches a terminal/aging state, increment per-segment
counters keyed by the bounded macro dimensions (`reason_family`, `msg_type`, `state`)
within the rolling window. The scorer computes, per macro segment:

- **observed rate** over the window;
- **deviation** from the segment's **configured expected baseline**, as a **binomial
  proportion z-score**: `z = (p_obs − p_exp) / sqrt(p_exp·(1−p_exp)/n)`. This bakes
  sample size in — low `n` → large denominator → needs a bigger deviation to flag —
  which kills small-sample false positives for free;
- a **minimum-sample gate** (`n ≥ n_min`) below which a segment is not scored;
- an **EWMA of the observed rate** (`S_t = α·x_t + (1−α)·S_{t−1}`) so a slow,
  sustained drift (scenario #7) accumulates into a persistent positive residual even
  when no single window spikes — the standard EWMA-control-chart approach to detecting
  small sustained shifts.

Binomial-z catches the spikes (scenarios 1–6); the EWMA residual catches the slow creep
(#7). All O(Δ) on the existing incremental fold; state is trivially bounded (no per-id
dimension).

### Unit 4 — Segmented metrics exposition
*Extends `src/gwlab/swift/exposition.py` / `observe.py` / `stream/selfmetrics.py`.*

Expose:
- **Segmented state counters** with bounded macro labels:
  `swift_settlement_state_total{state,reason_family,msg_type}`.
- **Deviation/anomaly metrics** per macro segment:
  `swift_segment_fail_rate{dim,id}`, `swift_segment_fail_deviation{dim,id}` (z-score),
  `swift_segment_fail_ewma{dim,id}` — where `dim ∈ {reason_family, msg_type}` and `id`
  ranges over that dimension's small bounded value set.
- **Audit-queue infra metrics** (Unit 8).

### Unit 5 — Dashboard rework (three registers)
*Rewrites `src/gwlab/swift/board.py`; deploys via `gw-sec-observability` role.*

Retire the settled-dominated bar chart. Three registers on one board:

- **Register A — Normal trading day (routine).** A large **settlement-efficiency %**
  headline (the mostly-green reality), then aggregate fails broken out **by
  reason-family and by message-type**, each on its *own* scale, never plotted against
  `settled` volume. PromQL-native over the bounded labels.
- **Register B — Exceptional-event detection (anomaly).** Per-macro-dimension
  **deviation** panels (reason-family, message-type), and **rare-event amplification**
  — a 0→5 `REJT` burst must *shout* (threshold coloring, z-scored, deltas/sparklines),
  not vanish. Powered by the Unit-3 metrics.
- **Register C — Audit-queue infrastructure health.** The data-store view under all
  the metadata (Unit 8).

### Unit 6 — Scenario catalog + snapshot capture
*New: a scenario runner script + `docs/` catalog output.*

A runner that, per scenario: seeds the realistic baseline → injects the episode
deterministically → drives it through the live audit queues + collector → captures the
**driving metric values as a committed JSON dump** (the reproducible source of truth)
and a **PNG snapshot** → writes a per-scenario markdown page (what broke, the
signature, which register/panel reveals it, the isolating macro dimension). The
deterministic scenario + metric dump make the result byte-reproducible; the PNG is a
**live capture during validation** (manual screenshot by default). If the base-lab
Grafana image renderer (`mq-resiliency-lab-for-linux#877`) is deployed by capture time,
the runner automates the PNG via the render API; otherwise it is screenshotted. Output:
a committed **7-snapshot catalog** + index — the demo artifact and the epic's live
acceptance evidence.

### Unit 7 — Reusable observability-primitives note
*New: a short `docs/` reference.*

Extract the generalizable pattern: *incidents are concentrations; detect
deviation-per-macro-dimension (binomial-z for spikes, EWMA for slow drift), not
volume; keep the metric surface macro and non-sensitive; the macro signal points to
where the full-detail system does the drill-down.* Written for reuse across the family.

### Unit 8 — Audit-queue data-store health
*Extends `stream/sweep.py` (MQINQ read) + `stream/selfmetrics.py`; `gw-sec-observability`
role for exporter config.*

- **Collector-direct via `MQINQ`** (the low-latency source of truth at browse time):
  each sweep the collector inquires `CURDEPTH`, `MAXDEPTH` → **fill ratio / headroom**,
  and derives **oldest-message age** from the window. Exposed as
  `swift_audit_queue_depth{queue}`, `swift_audit_queue_fill_ratio{queue}`,
  `swift_audit_oldest_age_seconds{queue}`. More accurate and synchronous than the
  exporter's out-of-band path — the actual state at the instant we read the queue.
- **MQ Prometheus exporter** (standard infra we do *not* recreate): disk / log /
  filesystem consumption. Ensure the existing `gw_sec_exporter` config **watches the
  `GW.SEC.*` queues** — gap-filling that config is the endorsed boundary crossing (§4).
- **Critical-state signals:** fill-ratio headroom (the top one — a full audit queue
  breaks the whole out-of-band mechanism), disk-footprint trend, and **expiry-window
  stability** (steady depth = healthy rolling window aging out at `CAPEXPRY`; rising
  depth = backup/leak upstream).

## 7. The seven scenarios (macro reframe)

Every scenario isolates on a **macro** dimension. The tool detects the *class* of
problem; the drill-down to *who*/*which* lives in the full-detail application system.

| # | Real-world event | Macro signal | Isolating dimension | Register/panel |
|---|---|---|---|---|
| 1 | **Counterparty outage** — a counterparty's ops/connectivity goes dark | **unmatched (`MTCH//NMAT`) rate spikes** → "go look at counterparties in the app" | reason_family (matching) | B: reason-family deviation |
| 2 | **CSD / T2S settlement-run incident** — the platform stalls a cycle | broad `SETT//PEND→PENF` wave in a time window | time × state | A+B: efficiency dip + time-correlated spike |
| 3 | **Lack-of-securities fail chain** — a security can't be delivered | **`LACK` reason spikes** → "drill to which ISIN in the app" | reason_family (settlement) | B: reason-family deviation |
| 4 | **Stale SSI / static-data drift** — settlement details mismatch | matching-disagreement (`Dxxx`) reasons rise | reason_family (matching) | B: reason-family deviation |
| 5 | **Upstream bad deploy / ref-data glitch** — malformed / ineligible | **`IPRC//REJT` + `NARR` burst, jumps from ~0** | reason_family (rejection) | B: rare-event amplification |
| 6 | **Funding / liquidity squeeze** — lack of cash on DvP legs | `SETT//PENF` + `CMON` on DvP message types (541/543) | reason × msg_type | B: reason × msg-type panel |
| 7 | **Silent degradation** — a slow, sustained drift buried under the settlement-fail baseline | small persistent rise in a reason-rate, caught by EWMA | reason_family trend | B: EWMA residual (the hard case) |

The signal-in-noise challenge is intact and cleaner: the noise (routine *settlement*
fails) and the signals (*matching* / `REJT` / `CMON` drifts) separate naturally by
reason family; #7 needs the EWMA to surface above the baseline.

## 8. Calibration & cardinality

**Calibration (generation-side, sourced — §11).** Approximate anchors by book:

| Book | Settle-fail baseline | Notes |
|---|---|---|
| Govvie | ~2% | ESMA: sovereign ~2% value / ~2.5% volume |
| Equity | ~2–3% (US CNS) to ~5% (EU) | DTCC CNS 2.12% / non-CNS 3.31% (Jul 2024) |
| ETF | ~17–20% | ESMA: ETFs ~17–20% — the fat tail in the aggregate |

Plus matching (~a few %, transient; ~27% of *fails* are matching-rooted per ICMA) and
rejection (<<1%, the rare one). Books blend into one realistic aggregate; per-book
outcome distributions are a config table, tunable without code change.

**Cardinality (trivial by construction).** Bounded macro label domains only:
`state` (~7), `msg_type` (540–548, ~9), `reason_family` (~5:
`settlement`/`cash`/`matching`/`rejection`/`other`). Largest metric is
`state × reason_family × msg_type ≈ 7 × 5 × 9 ≈ 315` series — bounded and legible.
Deviation metrics are 1-D over a small domain. A cardinality unit test asserts no
label domain grows unbounded under a full scenario run.

## 9. Testing & validation

- **Unit tests (100% branch+line, repo norm):** binomial-z and EWMA math; min-sample
  gating; cardinality bounds (assert bounded label growth under a full run); scenario
  determinism (same seed → identical stream); calibration (aggregate generated rates
  within tolerance of the configured books); board generation (three registers,
  expected panels).
- **Live validation (operational tasks — §10):** deploy the changes, then run each of
  the seven scenarios end-to-end on the live audit queues + collector, capture its
  snapshot, and confirm the expected register reveals it. The seven captured snapshots
  are the acceptance artifact.

## 10. Operational bookends

Seeded at plan time — `impl → deploy → validate`:

- **Deployment task** — install the collector, board, and exporter-config changes to
  the live lab (agent-safe install/sync/restart; no release).
- **Validation task** — run all seven scenarios, capture each snapshot, and confirm the
  right register reveals each signal. Its `Outcome: SUCCESS` (the 7-snapshot catalog)
  is the epic's live acceptance.

## 11. Deliverables

1. Calibrated realistic traffic + 7 deterministic injectable scenarios.
2. Collector anomaly scoring (binomial-z + min-sample + EWMA) over macro dimensions,
   trivially bounded cardinality.
3. Three-register dashboard.
4. Audit-queue data-store health section (MQINQ-direct + exporter infra).
5. A documented **snapshot catalog** (one per scenario + index).
6. A **reusable observability-primitives** reference.
7. A **calibration reference doc** (sourced research, data-vs-judgment, URLs).

## 12. Calibration source (sourced research)

Data separated from judgment; URLs preserved for verification. Primary sources:

- **ESMA Report on shortening the settlement cycle (T+1), Nov 2024** — per-asset-class
  fail rates (7.14% EEA all-asset; sovereign ~2%; ETF 17–20%):
  https://www.esma.europa.eu/sites/default/files/2024-11/ESMA74-2119945925-1969_Report_on_shortening_settlement_cycle.pdf
- **SIFMA/ICI/DTCC T+1 After-Action Report** — US equity CNS fail 2.12% / non-CNS
  3.31%; affirmation 73%→~95%:
  https://www.sifma.org/news/press-releases/sifma-ici-and-dtcc-release-t1-after-action-report-industry-coordination-led-to-successful-transition-reducing-risk-and-costs-in-the-system
- **ICMA ERCC "Optimising settlement efficiency"** — 27% of fails matching-rooted:
  https://www.icmagroup.org/assets/Uploads/ERCC-discussion-paper-on-settlement-efficiency.pdf
- **iotafinance MT548** — reason-code catalog (`LACK`/`CLAC`/`CMON`; `Dxxx`; `NARR`):
  https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT548.html

**Honesty caveats:** ESMA changed measurement methodology in 2022 → cite *trends* over
*absolute levels*; the **rejection rate is judgment-only** (no public figure exists);
US DTCC figures are institutional-equity (closest MT540–548 analog).

## 13. Resolved design decisions (from pushback)

- One epic, plan sequenced in two phases (§5).
- Deviation scoring pinned: binomial proportion z-score + min-sample gate + EWMA trend
  (§6 Unit 3).
- Snapshots: deterministic scenario + committed metric-dump reproducible; PNG
  live-captured, optionally automated by base-lab renderer `#877`.
- Fully-macro dimensions only (`state`/`msg_type`/`reason_family`/`time`); no
  counterparty, no instrument; asset class generation-side only — dissolving the
  cardinality risk (§4, §8).
- Operational deploy + validation bookends seeded at plan time (§10).
