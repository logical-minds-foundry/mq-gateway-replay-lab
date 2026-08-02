# Reusable observability primitives

**Epic:** #175 · **Task:** #202

This is a distilled, reusable reference for the observability pattern epic #175
built on top of the MQ-native settlement audit copy. The settlement work is the
worked example; the ideas here are deliberately generic, so any other stream of
events with a rare-but-concentrated failure mode can reuse them. Nothing here is
client- or employer-specific — it is a *generic trading back-office gateway*
problem, and the primitives are ordinary statistical process control.

Read this if you are adding observability to a new stream and want the shape of a
detector surface that stays cheap, non-sensitive, and hard to fool.

## 1. Incidents are concentrations; routine noise is diffuse

The core insight. On any busy day a settlement stream carries a thin, *diffuse*
background of routine failures — a few percent, sprinkled across every
counterparty, instrument, and message type, because that is just what settlement
is. Watching raw failure *volume* drowns in that background: the count is always
non-zero, always jittering, and a real incident rarely moves the total enough to
notice.

An incident looks different in kind, not just degree. It is a *concentration* — a
counterparty outage lands almost entirely in the `matching` family; a CSD
processing halt concentrates in `settlement` within a time slice; a funding
squeeze piles into `cash` on a particular message type. The signal is not "more
failures" but "failures bunched into one macro dimension far past what that
dimension's own baseline predicts."

So the detector measures **deviation per macro dimension**, never raw volume. It
asks, for each bounded dimension, "is *this* slice's failure share abnormal
against *its own* calibrated baseline?" A diffuse rise everywhere barely moves any
one dimension; a concentrated incident lights up exactly the dimension it lands
in. That is what separates a rare, concentrated incident from the diffuse
background — and it is what the seven scenario catalog examples were built to
demonstrate, each isolating on one macro dimension.

## 2. Two complementary detectors

One detector is not enough, because "abnormal" has two failure modes: the sudden
spike and the slow creep. Epic #175 runs both, side by side, over the same macro
dimension. Both primitives are pure functions in
[`src/gwlab/swift/stream/anomaly.py`](../src/gwlab/swift/stream/anomaly.py), and
[`src/gwlab/swift/stream/state.py`](../src/gwlab/swift/stream/state.py) wires them
into the maintained settlement picture.

### 2a. Binomial-proportion z-score — the spike detector

`deviation_z(observed_fails, observed_n, expected_p, *, min_sample=30)` scores an
observed failure *share* against a calibrated baseline share as a
binomial-proportion z-score:

```text
z = (observed_fails / observed_n - expected_p) / sqrt(expected_p * (1 - expected_p) / observed_n)
```

Two properties make it the right spike detector:

- **Sample-size-aware.** The binomial standard error in the denominator bakes `n`
  in: the same observed rate is a *larger* z at high volume than at low volume,
  because a big sample makes a given deviation harder to explain as chance. You get
  volume-adjusted surprise for free, without a separate volume threshold.
- **Small-sample gate.** Below `min_sample` (default 30) the function returns
  `None` rather than a number. Thin evidence never flags. Without this gate, a
  couple of failures out of three messages would post a screaming z-score every
  time the window is quiet — the classic small-sample false positive. Returning
  `None` (not `0.0`, not a swallowed error) makes "not enough evidence" an explicit,
  visible state rather than a fake calm reading.

### 2b. EWMA — the slow-drift detector

`Ewma(alpha)` is an exponentially weighted moving average — a textbook SPC
control-chart residual. Each sweep folds the family's observed rate in:

```text
value <- alpha * x + (1 - alpha) * value
```

A small `alpha` (the collector uses `0.3`) weights history heavily, so a slow,
sustained climb accumulates across sweeps instead of being washed out inside any
single window. This is the detector for **silent degradation**: the case where no
single window ever clears the z-band, but the trend is unmistakably up. The z-score
alone would miss it; the EWMA catches it. See the `silent_degradation` scenario,
whose whole point is that every individual window looks unremarkable.

### 2c. How `state.py` wires them together

`SettlementState.deviations(baselines)` in `state.py` is where the two detectors
meet the data:

- It folds the macro `segment_counts()` picture (keyed
  `(state, reason_family, msg_type)`) into a failure share per `reason_family`:
  numerator = instructions classified into that family, denominator = the total
  retained population.
- For each family with a configured baseline in `REASON_FAMILY_BASELINES`, it
  scores that share with `deviation_z`. Families whose z is `None` (below the
  small-sample gate) are dropped — thin evidence never flags.
- **Independently of the gate**, every family's observed rate is *always* fed into
  its retained per-family `Ewma` (held in `self._ewmas`, exposed via
  `family_ewmas`). Below-sample windows included. That persistence across sweeps is
  exactly what lets the slow-drift case accumulate rather than resetting each pass.

`REASON_FAMILY_BASELINES` is a tunable **config constant**, not sourced data — a
roll-up of the calibration reference (see §4). The
`test_baseline_constant_covers_the_four_exception_families` test pins it to exactly
the four non-benign families (`settlement`, `cash`, `matching`, `rejection`);
`OTHER` is benign and carries no baseline.

The two detectors surface as two metric series (see
[`src/gwlab/swift/stream/selfmetrics.py`](../src/gwlab/swift/stream/selfmetrics.py),
`render_deviation_metrics`):

| series | detector | reads |
|---|---|---|
| `swift_segment_fail_deviation{dim="reason_family",id=...}` | binomial z-score | `SettlementState.deviations()` |
| `swift_segment_fail_ewma{dim="reason_family",id=...}` | EWMA drift | `SettlementState.family_ewmas` |

The Grafana board
([`src/gwlab/swift/board.py`](../src/gwlab/swift/board.py), "Register B") plots the
z-score as a bargauge that colours red at z ≥ 3, so a three-sigma family jump can
never read as a benign green bar, and plots the EWMA as its slow-drift trend line
alongside.

## 3. Keep the metric surface macro and non-sensitive

The detectors above operate only on **bounded macro dimensions**:

- `state` — the settlement lifecycle state (~7 values).
- `msg_type` — the SWIFT message type, MT540–548 (a small fixed set).
- `reason_family` — the exception family: `settlement`, `cash`, `matching`,
  `rejection`, `other` (a five-member `StrEnum`).
- time / window — the retention horizon, not a free-form timestamp label.

Deliberately **not** exposed: per-counterparty, per-instrument, per-SEME, or any
other high-cardinality or business-sensitive identifier. Those live in the
full-detail application settlement system, which is authoritative for the
who/what/how-much. This audit copy's job is narrower and complementary: the macro
signal answers **"is something concentrating, and in which family / type / state?"**
and thereby points at *where* the full-detail system should be drilled into. It is
a pointer, not a ledger.

That minimal footprint is the selling point, not a limitation. A macro,
non-sensitive metric surface is:

- **Safe to expose widely.** No counterparty or instrument identity leaves the
  gateway, so the board can sit on a shared Prometheus/Grafana without a data-
  sensitivity review of every label.
- **Cheap.** Bounded label domains mean bounded time-series count (see §3a), so the
  cost is flat regardless of trade volume.
- **Robust.** The macro dimension is exactly the level at which a real incident
  concentrates, so you lose nothing that matters for detection by refusing the fine
  labels.

### 3a. Bounded cardinality by construction

Cardinality is bounded *by construction*, not by a runtime cap. Every exposed label
draws from a small, closed domain — the `reason_family` `StrEnum` has five members,
`state` about seven, `msg_type` the fixed MT540–548 set, `queue` a handful of named
audit queues. The product of those domains is the ceiling on distinct time series,
and it is a few hundred at most, independent of how many trades flow through.

The design keeps that ceiling honest:

- `render_segment_metrics` in `selfmetrics.py` emits one line per
  `(state, reason_family, msg_type)` key and nothing per message; the deviation
  series are labelled only `dim="reason_family",id=<family>`.
- The deviation and EWMA series carry a `dim="reason_family"` label rather than the
  raw family so the *label schema itself* is fixed and future dimensions slot into
  the same two series names.
- Tests pin the label shape so a high-cardinality label cannot sneak in unnoticed:
  `test_baseline_constant_covers_the_four_exception_families`
  ([`tests/swift/test_state_deviation.py`](../tests/swift/test_state_deviation.py))
  fixes the scored-family set, `test_segment_counts_key_on_state_family_msgtype`
  ([`tests/swift/test_state_segments.py`](../tests/swift/test_state_segments.py))
  fixes the segment key to the three macro labels, and the board tests assert every
  deviation query carries `dim="reason_family"`. Add an unbounded label and one of
  these turns red.

## 4. Calibration and worked examples

The detectors are only as good as their baselines, and the baselines are
**calibrated**, not guessed. Every per-asset-class rate that feeds
`REASON_FAMILY_BASELINES` traces back to a cited anchor in
[`docs/settlement-failure-rate-reference.md`](settlement-failure-rate-reference.md),
which keeps **data** (what a public source actually reports, with the URL) strictly
apart from **judgment** (our interpolation on top of it, including the one family
for which no public figure exists at all). Retune the baselines there, in one
place, as the calibration source moves.

The [scenario catalog](scenario-catalog/index.md) is the demonstration half: seven
deterministic, seed-reproducible failure episodes, each isolating on one macro
dimension so a rare, concentrated incident visibly separates from the diffuse
background. The catalog is the concrete proof that the primitives in this document
do what they claim — including `silent_degradation`, the case built specifically to
show the EWMA drift detector catching what the spike detector cannot.

## 5. Reusing this elsewhere — the checklist

To apply the pattern to a new event stream:

1. **Find the macro dimension(s)** an incident concentrates in — the closed, small,
   non-sensitive labels (family / type / state / window), never the identity of the
   individual entity.
2. **Calibrate a baseline share** per dimension value from a cited source; keep data
   and judgment apart; store the baselines as one tunable config constant.
3. **Score with both detectors** over that dimension: binomial-proportion z-score
   for spikes, with a small-sample gate that returns "no evidence" rather than a
   false alarm; EWMA for slow sustained drift, persisted across sweeps.
4. **Expose only the macro surface** — bounded label domains, one series per
   detector — and let the macro signal point at *where* the full-detail system
   drills down.
5. **Guard the cardinality with a test** that pins the label schema, so a
   high-cardinality label can never slip in.
