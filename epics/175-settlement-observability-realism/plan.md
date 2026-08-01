# Realistic settlement traffic & signal-in-noise observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Calibrate the SWIFT collector's traffic to real-world settlement-failure rates and rework its dashboard so a rare, concentrated incident surfaces above diffuse background noise — using only macro, non-sensitive dimensions.

**Architecture:** Extend the existing streaming collector (`SettlementState` incremental fold, `sweep` browse pass) with macro-segment accounting (`state`/`msg_type`/`reason_family`) and a pure anomaly-scoring module (binomial-z + EWMA). The generator gains asset-class books and a realistic/mechanism mode; a scenario harness injects deterministic failure episodes; the board is rebuilt into three registers. Reason-family classification lives in the stream layer, keeping `lifecycle.py` a pure state machine.

**Tech Stack:** Python 3.12 (`gwlab` package), pymqi (MQ boundary), Prometheus text exposition, Grafana JSON model, Ansible (`gw-sec-observability`), pytest (100% branch+line).

## Global Constraints

- **Minimal footprint / macro-only:** the collector exposes only `state`, `msg_type`, `reason_family`, `time`. **Never** segment on counterparty or instrument/ISIN. Asset class is a generation-side input only.
- **Bounded cardinality:** every label domain is a small bounded set; a test asserts no unbounded growth.
- **Transparency & out-of-band:** no app-side change; the collector reads only `GW.SEC.*` audit queues.
- **Determinism:** no wall-clock / `random` in the committed path; generators/scenarios are seeded and byte-reproducible.
- **Anonymization (absolute):** invented data only; generic vocabulary; no client-identifiable content.
- **Coverage:** 100% branch + line via `vrg-container-run -- vrg-validate` — the only validation command.
- **Determinism caveat for tests:** pass explicit `now`/`seed`; never call `datetime.now()`/`random` at module import.

## Phases

- **Phase 1 (Tasks 1–7): Realistic traffic + honest dashboard.** Standalone-valuable: a calibrated board (efficiency headline, reason-family/msg-type breakdown, audit-queue infra health). No anomaly scoring.
- **Phase 2 (Tasks 8–13): Anomaly detection + scenario catalog.** Deviation scoring, the 7 episodes, the snapshot catalog, the primitives note.

---

## PHASE 1 — Realistic traffic + honest dashboard

### Task 1: Calibration reference doc

**Files:**
- Create: `docs/settlement-failure-rate-reference.md`

The sourced research is the calibration source of truth; every rate in Task 2 cites it. No code; this is the data-vs-judgment reference.

- [ ] **Step 1: Write the doc** with the sourced anchors, data-vs-judgment separated, URLs preserved (from spec §12): govvie ~2% (ESMA), equity ~2–3% US CNS / ~5% EU (DTCC/ESMA), ETF ~17–20% (ESMA); matching ~a few % (~27% of fails, ICMA); rejection <<1% (judgment-only, no public figure). Include the honesty caveats (ESMA 2022 methodology change → trends over levels; rejection judgment-only; DTCC = institutional equity). Reason-code families from iotafinance MT548 (`LACK`/`CLAC` settlement, `MONY`/`CMON` cash, `Dxxx` matching, `NARR`/format rejection).
- [ ] **Step 2: Commit**

```bash
vrg-git add docs/settlement-failure-rate-reference.md
vrg-commit --type docs --scope calibration --message "sourced settlement-failure-rate reference (data vs judgment)"
```

---

### Task 2: Reason-family classifier

**Files:**
- Create: `src/gwlab/swift/reasons.py`
- Test: `tests/swift/test_reasons.py`

**Interfaces:**
- Produces: `class ReasonFamily(StrEnum)` with members `SETTLEMENT`, `CASH`, `MATCHING`, `REJECTION`, `OTHER`; `def classify(message: SwiftMessage) -> ReasonFamily | None` — the exception family a *response* signals, or `None` if it carries no exception signature. Consumed by Tasks 5 (segmentation) and 11 (scenarios).

Grounded in `reference.py`: `IPRC//REJT` → REJECTION; `MTCH//NMAT` → MATCHING; `SETT//PENF` with a `:24B:` reason in `{LACK, CLAC}` → SETTLEMENT, in `{MONY, CMON}` → CASH; other `PENF` → SETTLEMENT (default fail family); everything else → `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_reasons.py
from datetime import datetime
from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.reasons import ReasonFamily, classify

def _msg(*statuses: StatusItem) -> SwiftMessage:
    return SwiftMessage(mt="548", seme=None, rela="R1", statuses=statuses,
                        put_time=datetime(2026, 1, 1), is_instruction=False, is_confirmation=False)

def test_rejection_family():
    assert classify(_msg(StatusItem("IPRC", "REJT", "NARR"))) is ReasonFamily.REJECTION

def test_matching_family():
    assert classify(_msg(StatusItem("MTCH", "NMAT", "DSEC"))) is ReasonFamily.MATCHING

def test_settlement_vs_cash_family():
    assert classify(_msg(StatusItem("SETT", "PENF", "LACK"))) is ReasonFamily.SETTLEMENT
    assert classify(_msg(StatusItem("SETT", "PENF", "CMON"))) is ReasonFamily.CASH

def test_benign_status_has_no_family():
    assert classify(_msg(StatusItem("IPRC", "PACK", None))) is None
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/swift/test_reasons.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `reasons.py`**

```python
"""Classify the exception family a settlement response signals (macro, non-sensitive)."""
from __future__ import annotations
from enum import StrEnum
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from gwlab.swift.model import SwiftMessage

class ReasonFamily(StrEnum):
    SETTLEMENT = "settlement"   # lack of securities
    CASH = "cash"               # lack of cash
    MATCHING = "matching"       # unmatched / disagreement
    REJECTION = "rejection"     # validation reject
    OTHER = "other"

_CASH_REASONS = frozenset({"MONY", "CMON"})
_SETTLEMENT_REASONS = frozenset({"LACK", "CLAC"})

def classify(message: SwiftMessage) -> ReasonFamily | None:
    """The exception family this response signals, or None if it is benign."""
    for status in message.statuses:
        if status.qualifier == "IPRC" and status.value == "REJT":
            return ReasonFamily.REJECTION
        if status.qualifier == "MTCH" and status.value == "NMAT":
            return ReasonFamily.MATCHING
        if status.qualifier == "SETT" and status.value == "PENF":
            if status.reason in _CASH_REASONS:
                return ReasonFamily.CASH
            return ReasonFamily.SETTLEMENT
    return None
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/swift/test_reasons.py -v` → PASS.
- [ ] **Step 5: Commit**

```bash
vrg-git add src/gwlab/swift/reasons.py tests/swift/test_reasons.py
vrg-commit --type feat --scope swift --message "reason-family classifier (macro exception buckets)"
```

---

### Task 3: Asset-class books + realistic/mechanism mode

**Files:**
- Modify: `src/gwlab/swift/generator.py`
- Test: `tests/swift/test_generator_books.py`

**Interfaces:**
- Produces: `REALISTIC_BOOKS: dict[str, Mix]` (equity/govvie/etf, calibrated per Task 1); `MECHANISM_MIX: Mix` (= today's `DEFAULT_MIX`); `def generate_realistic(count, *, seed=0, target_size_bytes=1024) -> list[GeneratedInstruction]` blending the books by share. Existing `generate(count, mix=...)` is unchanged (preserves `mechanism` mode).

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_generator_books.py
from collections import Counter
from gwlab.swift.generator import generate_realistic, REALISTIC_BOOKS

def test_books_cover_the_three_asset_classes():
    assert set(REALISTIC_BOOKS) == {"equity", "govvie", "etf"}

def test_realistic_is_deterministic():
    assert generate_realistic(500, seed=7) == generate_realistic(500, seed=7)

def test_realistic_fail_rate_is_calibrated_not_inflated():
    # aggregate clean share should be high (mostly-green), unlike the mechanism mix
    gen = generate_realistic(2000, seed=1)
    clean = sum(1 for g in gen if g.directive.kind == "clean")
    assert clean / len(gen) > 0.85  # realistic aggregate, not the ~90%-fail teaching mix
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`generate_realistic` missing).
- [ ] **Step 3: Implement** — add to `generator.py`:

```python
MECHANISM_MIX = DEFAULT_MIX  # today's inflated teaching mix, preserved

# Calibrated books (Task 1 reference). Weights are clean/reject/fail/slow.
REALISTIC_BOOKS: dict[str, Mix] = {
    "govvie": Mix(clean=0.965, reject=0.003, fail=0.020, slow=0.012, fail_reasons=("LACK", "MONY")),
    "equity": Mix(clean=0.955, reject=0.005, fail=0.028, slow=0.012, fail_reasons=("LACK", "MONY")),
    "etf":    Mix(clean=0.800, reject=0.005, fail=0.180, slow=0.015, fail_reasons=("LACK", "MONY")),
}
# Book share of the aggregate stream (invented, plausible back-office mix).
_BOOK_SHARE: dict[str, float] = {"govvie": 0.45, "equity": 0.45, "etf": 0.10}

def generate_realistic(count: int, *, seed: int = 0, target_size_bytes: int = 1024) -> list[GeneratedInstruction]:
    """Blend the calibrated asset-class books into one realistic aggregate day.

    Deterministic given ``seed``. Asset class shapes the aggregate outcome mix only;
    it is not carried on the generated instruction (macro-only invariant).
    """
    rng = random.Random(seed)  # noqa: S311
    books = list(_BOOK_SHARE)
    weights = [_BOOK_SHARE[b] for b in books]
    out: list[GeneratedInstruction] = []
    for index in range(count):
        book = rng.choices(books, weights=weights, k=1)[0]  # noqa: S311
        mix = REALISTIC_BOOKS[book]
        seme = f"GWSEC{index:010d}"
        mt = _pick(rng, mix.mts)
        category = rng.choices(_CATEGORIES, weights=[mix.clean, mix.reject, mix.fail, mix.slow], k=1)[0]  # noqa: S311
        directive = _draw_directive(rng, category, mix)
        out.append(GeneratedInstruction(seme, mt, directive, _build_text(mt, seme, directive, target_size_bytes)))
    return out
```

- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope swift --message "asset-class books + realistic aggregate generation mode"`

---

### Task 4: Audit-queue depth via MQINQ on the Messaging port

**Files:**
- Modify: `src/gwlab/messaging.py` (Protocol + `fake_messaging.py`), `src/gwlab/pymqi_messaging.py`
- Test: `tests/swift/test_fake_messaging_depth.py`

**Interfaces:**
- Produces: `Messaging.inquire_depth(queue: str) -> tuple[int, int]` returning `(current_depth, max_depth)`. `FakeMessaging` returns configured values; `PyMqiMessaging` opens the queue for inquire and reads `CURDEPTH`/`MAXDEPTH` (box-only, covered by the fake-pymqi surface per the repo's coverage pattern).

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_fake_messaging_depth.py
from gwlab.fake_messaging import FakeMessaging

def test_inquire_depth_returns_current_and_max():
    mq = FakeMessaging()
    mq.set_depth("GW.SEC.INSTRUCT.AUDIT", current=1200, maximum=5000)
    assert mq.inquire_depth("GW.SEC.INSTRUCT.AUDIT") == (1200, 5000)
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`set_depth`/`inquire_depth` missing).
- [ ] **Step 3: Implement** — add `inquire_depth` to the `Messaging` Protocol; in `FakeMessaging` add a `_depths: dict[str, tuple[int,int]]`, `set_depth`, and `inquire_depth` (default `(0, 0)`); in `PyMqiMessaging` open with `MQOO_INQUIRE` and `queue.inquire(pymqi.CMQC.MQIA_CURRENT_Q_DEPTH, pymqi.CMQC.MQIA_MAX_Q_DEPTH)`.
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope messaging --message "inquire_depth on the Messaging port (CURDEPTH/MAXDEPTH)"`

---

### Task 5: Macro-segment accounting in SettlementState

**Files:**
- Modify: `src/gwlab/swift/stream/state.py`
- Test: `tests/swift/test_state_segments.py`

**Interfaces:**
- Produces: `SettlementState.segment_counts() -> dict[tuple[str, str, str], int]` keyed by `(state, reason_family, msg_type)` → count, computed from the maintained entries. `_Entry` gains `reason_family: ReasonFamily = ReasonFamily.OTHER`, updated in `_apply` via `reasons.classify` (keep the last non-`None` family). `msg_type` is `entry.instruction.mt`; `state` is `entry.state.current`.

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_state_segments.py
from datetime import datetime
from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.stream.state import SettlementState

def _instr(seme, mt="541"):
    return SwiftMessage(mt, seme, None, (), datetime(2026,1,1), True, False)
def _resp(rela, *st):
    return SwiftMessage("548", None, rela, st, datetime(2026,1,1,0,1), False, False)

def test_segment_counts_key_on_state_family_msgtype():
    s = SettlementState()
    s.ingest(_instr("A", "541"))
    s.ingest(_resp("A", StatusItem("IPRC", "REJT", "NARR")))
    counts = s.segment_counts()
    assert counts[("rejected", "rejection", "541")] == 1
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`segment_counts` missing).
- [ ] **Step 3: Implement** — import `reasons`; in `_Entry` add `reason_family: ReasonFamily = ReasonFamily.OTHER`; in `_apply`, after folding, `fam = reasons.classify(response)` and if `fam is not None: entry.reason_family = fam`; add:

```python
def segment_counts(self) -> dict[tuple[str, str, str], int]:
    """Macro-segment counts keyed (state, reason_family, msg_type) — the exposition seam."""
    counts: dict[tuple[str, str, str], int] = {}
    for entry in self._by_seme.values():
        key = (str(entry.state.current), str(entry.reason_family), entry.instruction.mt)
        counts[key] = counts.get(key, 0) + 1
    return counts
```

- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope collector --message "macro-segment accounting (state x reason_family x msg_type)"`

---

### Task 6: Segmented + audit-queue metrics exposition

**Files:**
- Modify: `src/gwlab/swift/stream/selfmetrics.py`
- Test: `tests/swift/test_segment_exposition.py`

**Interfaces:**
- Consumes: `SettlementState.segment_counts()` (Task 5), `Messaging.inquire_depth` (Task 4).
- Produces: `render_segment_metrics(counts: dict[tuple[str,str,str], int]) -> list[str]` emitting `swift_settlement_segment_total{state,reason_family,msg_type}`; `render_audit_queue_metrics(depths: dict[str, tuple[int,int]]) -> list[str]` emitting `swift_audit_queue_depth{queue}`, `swift_audit_queue_max_depth{queue}`, `swift_audit_queue_fill_ratio{queue}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_segment_exposition.py
from gwlab.swift.stream.selfmetrics import render_segment_metrics, render_audit_queue_metrics

def test_segment_metric_has_bounded_labels():
    lines = render_segment_metrics({("failed", "settlement", "541"): 3})
    assert 'swift_settlement_segment_total{state="failed",reason_family="settlement",msg_type="541"} 3' in lines

def test_audit_queue_fill_ratio():
    lines = render_audit_queue_metrics({"GW.SEC.INSTRUCT.AUDIT": (1000, 5000)})
    assert 'swift_audit_queue_fill_ratio{queue="GW.SEC.INSTRUCT.AUDIT"} 0.2' in lines
```

- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** both renderers (emit `# HELP`/`# TYPE` once per metric name, then one labelled line per key; fill_ratio = current/max, guarding max==0 → 0).
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope collector --message "segmented + audit-queue-health metrics exposition"`

*(Wiring these into `sweep`/`serve_collector` — calling `inquire_depth` per sweep for each `GW.SEC.*` queue and appending the rendered lines to the published body — is folded into this task's implementation, with a box-only integration exercised via the fake-pymqi surface.)*

---

### Task 7: Board Register A (normal-day) + Register C (infra health), and exporter queue config

**Files:**
- Modify: `src/gwlab/swift/board.py`, `ansible/roles/gw-sec-observability/` (exporter queue-monitor config)
- Test: `tests/swift/test_board_registers.py`

**Interfaces:**
- Consumes: the metrics from Task 6.
- Produces: `build_settlement_board` gains Register A (efficiency-% headline `sum(...settled...)/sum(...all...)`, fails by `reason_family`, fails by `msg_type` — each on its own panel) and Register C (audit-queue depth/fill-ratio/oldest-age + node-exporter disk). Retires the settled-dominated bar chart.

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_board_registers.py
from gwlab.swift.board import build_settlement_board

def test_board_has_efficiency_headline_and_infra_register():
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    titles = [p.get("title", "") for p in board["panels"]]
    assert any("Efficiency" in t for t in titles)
    assert any("Audit queue" in t or "Fill" in t for t in titles)

def test_board_retires_settled_dominated_bar():
    board = build_settlement_board("NHAUAPP", ds_uid="x")
    exprs = [t["expr"] for p in board["panels"] for t in p.get("targets", [])]
    # no single panel plots all states together (the buried-signal antipattern)
    assert "max by (state) (swift_settlement_state)" not in exprs
```

- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** the Register-A/C panels (reuse `_stat`/`_target`/`_grid` helpers) and remove the `_STATE_DIST` bar panel. Add the exporter queue-monitor config so the `gw_sec_exporter` watches `GW.SEC.*` (declare-and-verify; the endorsed boundary crossing).
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope board --message "Register A (efficiency+reason/msg breakdown) + Register C (audit-queue infra health)"`

**Phase 1 deliverable:** a realistic, honest board — deploy + eyeball as the Phase-1 checkpoint before Phase 2.

---

## PHASE 2 — Anomaly detection + scenario catalog

### Task 8: Anomaly-scoring primitives (binomial-z + min-sample + EWMA)

**Files:**
- Create: `src/gwlab/swift/stream/anomaly.py`
- Test: `tests/swift/test_anomaly.py`

**Interfaces:**
- Produces: `def deviation_z(observed_fails: int, observed_n: int, expected_p: float, *, min_sample: int = 30) -> float | None` (binomial proportion z-score, `None` below `min_sample`); `class Ewma` with `__init__(alpha: float)`, `update(x: float) -> float`, `value` property.

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_anomaly.py
import math
from gwlab.swift.stream.anomaly import deviation_z, Ewma

def test_below_min_sample_is_none():
    assert deviation_z(1, 5, 0.02, min_sample=30) is None

def test_zero_deviation_when_observed_matches_expected():
    assert abs(deviation_z(2, 100, 0.02)) < 1e-9

def test_positive_z_when_worse_than_baseline():
    z = deviation_z(8, 100, 0.02)   # 8% observed vs 2% expected
    assert z > 3.0

def test_ewma_tracks_sustained_drift():
    e = Ewma(alpha=0.3)
    for _ in range(20):
        e.update(0.04)
    assert abs(e.value - 0.04) < 0.005
```

- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement**

```python
"""Pure anomaly-scoring primitives: binomial-proportion z for spikes, EWMA for drift."""
from __future__ import annotations
import math

def deviation_z(observed_fails: int, observed_n: int, expected_p: float, *, min_sample: int = 30) -> float | None:
    """Binomial-proportion z-score of the observed fail rate vs the expected baseline.

    Returns None below ``min_sample`` (small-sample noise gate). The binomial
    standard error bakes sample size in, so low n needs a larger deviation to flag.
    """
    if observed_n < min_sample:
        return None
    se = math.sqrt(expected_p * (1.0 - expected_p) / observed_n)
    if se == 0.0:
        return 0.0
    return ((observed_fails / observed_n) - expected_p) / se

class Ewma:
    """Exponentially weighted moving average — an SPC detector for small sustained shifts."""
    def __init__(self, alpha: float) -> None:
        self._alpha = alpha
        self._value: float | None = None
    def update(self, x: float) -> float:
        self._value = x if self._value is None else self._alpha * x + (1 - self._alpha) * self._value
        return self._value
    @property
    def value(self) -> float:
        return 0.0 if self._value is None else self._value
```

- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope collector --message "anomaly-scoring primitives: binomial-z + EWMA"`

---

### Task 9: Wire deviation scoring into the collector + expose metrics

**Files:**
- Modify: `src/gwlab/swift/stream/state.py`, `src/gwlab/swift/stream/selfmetrics.py`
- Test: `tests/swift/test_state_deviation.py`

**Interfaces:**
- Consumes: `deviation_z`, `Ewma` (Task 8), `segment_counts` (Task 5), configured baselines (Task 3 book rates rolled up per reason_family).
- Produces: `SettlementState.deviations(baselines: dict[str, float]) -> dict[str, float]` — per-`reason_family` z-score over the window; a per-family `Ewma` maintained across `report` calls; exposition `render_deviation_metrics` → `swift_segment_fail_deviation{dim="reason_family",id=...}` and `swift_segment_fail_ewma{...}`.

- [ ] **Step 1: Write the failing test** — assert a family whose window fail-share exceeds its baseline yields `z > 0`, and one below `min_sample` is absent.

```python
# tests/swift/test_state_deviation.py (sketch — real assertions on a populated state)
def test_reason_family_deviation_positive_above_baseline():
    s = build_state_with(rejections=8, total=100)   # helper builds ingested traffic
    devs = s.deviations({"rejection": 0.003})
    assert devs["rejection"] > 3.0
```

- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** `deviations()` (count fails per family and total n from `segment_counts`, call `deviation_z`, drop `None`s; feed each family's observed rate into a retained `Ewma`) and the exposition renderer.
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope collector --message "per-reason-family deviation scoring + exposition"`

---

### Task 10: Board Register B (exceptional-event detection)

**Files:**
- Modify: `src/gwlab/swift/board.py`
- Test: `tests/swift/test_board_register_b.py`

**Interfaces:**
- Consumes: Task 9 metrics.
- Produces: Register B — a `swift_segment_fail_deviation` panel per `reason_family` with threshold coloring (z≥3 red), a rare-event amplification panel for `reason_family="rejection"` (deltas/sparkline so a 0→N burst is loud), and an EWMA-trend panel.

- [ ] **Step 1: Write the failing test** — assert Register-B panels reference `swift_segment_fail_deviation` and set a red threshold at z≥3.
- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** the Register-B panels.
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope board --message "Register B: per-reason-family deviation + rare-event amplification"`

---

### Task 11: Scenario injection harness (the 7 episodes)

**Files:**
- Create: `src/gwlab/swift/scenarios.py`
- Test: `tests/swift/test_scenarios.py`

**Interfaces:**
- Consumes: `generate_realistic` (Task 3), `corpus` renderers, `ReasonFamily` (Task 2).
- Produces: `SCENARIOS: dict[str, Scenario]` (7 named episodes: `counterparty_outage`, `csd_incident`, `lack_of_securities`, `stale_ssi`, `rejection_burst`, `funding_squeeze`, `silent_degradation`). `class Scenario` with `name`, `isolating_dimension`, `expected_family`, and `def episode(seed: int) -> list[tuple[str, str]]` returning `(queue_alias, message_text)` pairs — a realistic baseline plus a concentrated perturbation. Deterministic given `seed`.

- [ ] **Step 1: Write the failing test**

```python
# tests/swift/test_scenarios.py
from gwlab.swift.scenarios import SCENARIOS
from gwlab.swift.reasons import ReasonFamily

def test_seven_named_scenarios():
    assert len(SCENARIOS) == 7

def test_rejection_burst_isolates_on_rejection():
    sc = SCENARIOS["rejection_burst"]
    assert sc.expected_family is ReasonFamily.REJECTION

def test_episode_is_deterministic():
    assert SCENARIOS["lack_of_securities"].episode(3) == SCENARIOS["lack_of_securities"].episode(3)
```

- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** each episode: baseline from `generate_realistic`, then inject the concentrated signature (e.g. `rejection_burst`: a cluster of instructions whose status stream carries `IPRC//REJT`; `lack_of_securities`: `SETT//PENF`+`LACK`; `funding_squeeze`: `SETT//PENF`+`CMON` on 541/543; `silent_degradation`: a slow ramp of matching fails over successive slices). Reuse `corpus.render_status`/`render_confirmation`.
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope swift --message "scenario injection harness: 7 deterministic failure episodes"`

---

### Task 12: Scenario catalog runner + snapshot capture

**Files:**
- Create: `src/gwlab/swift/catalog.py`, `docs/scenario-catalog/` (output dir + index)
- Test: `tests/swift/test_catalog.py`

**Interfaces:**
- Consumes: `SCENARIOS` (Task 11).
- Produces: `def metric_dump(scenario_name: str, seed: int) -> dict` — the deterministic driving metric values (the reproducible source of truth), and `def render_catalog_page(scenario, dump) -> str` — the per-scenario markdown (what broke, signature, isolating dimension, which register reveals it). The live PNG is captured during validation (Task V); if the base-lab renderer (`mq-resiliency-lab-for-linux#877`) is deployed, `catalog.py` calls the render API, else the page references a screenshot path.

- [ ] **Step 1: Write the failing test** — assert `metric_dump` is deterministic and `render_catalog_page` names the scenario's isolating dimension.
- [ ] **Step 2: Run to verify it fails** — FAIL.
- [ ] **Step 3: Implement** `metric_dump` (drive the episode through an in-memory `SettlementState`, capture `segment_counts` + `deviations`) and `render_catalog_page`; write the 7 pages + `index.md`.
- [ ] **Step 4: Run to verify it passes** — PASS.
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope swift --message "scenario catalog runner + per-scenario metric dump and pages"`

---

### Task 13: Reusable observability-primitives reference

**Files:**
- Create: `docs/observability-primitives.md`

- [ ] **Step 1: Write the doc** — the generalizable pattern: incidents are concentrations; detect deviation-per-macro-dimension (binomial-z for spikes, EWMA for drift); keep the metric surface macro and non-sensitive; the macro signal points to where the full-detail system drills down. Cross-reference the scenario catalog.
- [ ] **Step 2: Commit** — `vrg-commit --type docs --scope observability --message "reusable observability-primitives reference"`

---

## Operational bookends (seeded as issues at plan time, run after merge)

- **Deployment task** (`--kind deployment`): install the collector, board, and exporter-config changes to the live lab (agent-safe install/sync/restart; no release). Blocked-by the Phase-2 impl tasks.
- **Validation task** (`--kind validation`): run all seven scenarios end-to-end on the live audit queues + collector, capture each snapshot (PNG live or via renderer `#877`), and confirm the right register reveals each signal. Blocked-by the deployment. Its `Outcome: SUCCESS` (the 7-snapshot catalog) is the epic's acceptance.

---

## Self-review

- **Spec coverage:** Unit 1→Task 3; Unit 2→Task 11; Unit 3→Tasks 8–9; Unit 4→Tasks 6,9; Unit 5→Tasks 7,10; Unit 6→Task 12; Unit 7→Task 13; Unit 8→Tasks 4,6,7; calibration doc→Task 1; operational bookends→seeded. All eight units + both phases covered.
- **Cardinality:** every exposed label domain (`state`~7, `reason_family`~5, `msg_type`~9, `queue` few, `dim`/`id` small) is bounded; Task 6/9 tests assert the label shape. No counterparty/instrument anywhere.
- **Type consistency:** `ReasonFamily` (Task 2) used identically in Tasks 5/9/11; `segment_counts()` key `(state, reason_family, msg_type)` consistent across Tasks 5/6/9; `deviation_z`/`Ewma` signatures consistent Tasks 8/9.
- **Determinism:** generators/scenarios seeded; no import-time clock/random.
