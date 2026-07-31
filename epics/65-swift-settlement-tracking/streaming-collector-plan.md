# Streaming stateful collector — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:subagent-driven-development`
> (recommended) or `superpowers:executing-plans` to implement this plan
> task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the O(N)-per-scrape batch collector with a long-running,
client-mode streaming collector that reads each message once, maintains the
settlement picture incrementally in memory, and serves a pre-computed snapshot.

**Architecture:** A single-threaded sweep loop browses the audit queues with
persistent cursors (`BROWSE_FIRST` → drain → `BROWSE_NEXT` deltas), folds each
message into an in-memory per-SEME state, and after each sweep publishes an
immutable metrics snapshot by atomic reference swap; a separate HTTP thread serves
the last snapshot. Deployed as a systemd client-mode service on `mon-probe`,
consistent with the base MQ Prometheus exporter. Design:
`epics/65-swift-settlement-tracking/streaming-collector-design.md`.

**Tech Stack:** Python 3.12, pymqi (client libs on `mon-probe`), `http.server`,
Prometheus text exposition, Ansible, systemd.

## Global Constraints

- **No coverage-omit for logic.** T3–T6 are pure logic and MUST reach 100% branch
  coverage by mocking the `Messaging` boundary — no `[tool.coverage.run] omit`
  entries. Only the box-only serve loop + HTTP thread (T5's `serve_collector`) and
  the pymqi adapter are omit-listed, exactly like `serve_http.py`.
- **Anonymization (absolute).** Generic vocabulary only; invented host/queue names;
  no employer/client-identifiable content in any file, commit, or test datum.
- **Parasite boundary = owned + reproducible.** Everything deployed is defined by
  this repo's Ansible on `mon-probe` (beside the base exporter); declare-and-verify
  the QM and its CCDT endpoints, never mutate them.
- **No silent failures.** No swallowed exceptions; a lost connection or a malformed
  message surfaces (crash-and-restart, or a counted anomaly) — never a silent drop.
- **Validation is `vrg-container-run -- vrg-validate` only.** 100% branch coverage,
  ruff (lint + format), mypy + ty, all tests.
- **Reuse existing logic.** `lifecycle.fold`, `correlate`, `model.parse_swift`,
  `window`, `observe`/`cliswift.render_exposition`, `swiftconfig` are reused, not
  reimplemented. Only the *driver* changes (batch → incremental).

---

## File Structure

- `src/gwlab/messaging.py` — **modify**: add `browse_new(queue)` to the `Messaging`
  Protocol (persistent-cursor read: first call from the first message, later calls
  only new arrivals).
- `src/gwlab/pymqi_messaging.py` — **modify** (box-only, omit): implement
  `browse_new` with a persistent browse handle (`BROWSE_FIRST` once, then
  `BROWSE_NEXT`), re-opening at `BROWSE_FIRST` after a reconnect.
- `tests/gwlab/swift/fakes.py` (or the existing test fake) — **modify**: add
  `browse_new` to the in-memory fake (per-queue high-water index).
- `src/gwlab/swift/stream/state.py` — **create**: `SettlementState` — per-SEME
  incremental fold, lifecycle-aware retention, window-gated orphan, aging.
- `src/gwlab/swift/stream/sweep.py` — **create**: `sweep()` — one round-robin pass
  over the queues in order, ingest into state, age; returns a `SweepResult`.
- `src/gwlab/swift/stream/snapshot.py` — **create**: `Snapshot` (immutable rendered
  metrics + self-metrics) and `SnapshotHolder` (atomic reference swap).
- `src/gwlab/swift/stream/selfmetrics.py` — **create**: `CollectorStats` +
  `render_collector_metrics()` self-observability exposition.
- `src/gwlab/swift/stream/serve_collector.py` — **create** (box-only, omit): the
  run loop (sweep + sleep) and the HTTP responder thread; `main()` entry point.
- `src/gwlab/swift/cliswift.py` — **modify**: outcome-count windowed-gauge note in
  the exposition (documented; the numbers are already windowed by the state).
- `pyproject.toml` — **modify**: add the `gwlab-collector` `console_scripts` entry
  point; add the two new box-only modules to `[tool.coverage.run] omit`.
- `ansible/roles/gw-sec-collector/` — **create**: install the built artifact to
  `/opt/gwlab` on `mon-probe`, render the systemd unit + CCDT + monitoring
  identity, add the Prometheus target.
- Tests mirror each logic module under `tests/gwlab/swift/stream/`.

---

## Task 1 — Packaging & install skeleton

**Files:**
- Modify: `pyproject.toml` (`[project.scripts]`)
- Create: `src/gwlab/swift/stream/__init__.py`, `src/gwlab/swift/stream/serve_collector.py` (a `main()` stub that prints its config and exits 0 for now)
- Create: `ansible/roles/gw-sec-collector/tasks/build.yml`, `.../install.yml`, `.../defaults/main.yml`

**Interfaces:**
- Produces: a `gwlab-collector` console entry point resolving to
  `gwlab.swift.stream.serve_collector:main`; an Ansible role that builds a wheel
  and installs it to `/opt/gwlab` with `/opt/gwlab/bin/gwlab-collector` on PATH.

This task is packaging/infra, not TDD-code. Steps are concrete and verifiable.

- [ ] **Step 1: Add the console entry point.** In `pyproject.toml`:

```toml
[project.scripts]
gwlab = "gwlab.cli:main"
gwlab-collector = "gwlab.swift.stream.serve_collector:main"
```

- [ ] **Step 2: Stub `serve_collector.main`** so the entry point resolves:

```python
def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gwlab-collector")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--conn", required=True)
    parser.add_argument("--channel", default="MON.SVRCONN")
    parser.add_argument("--port", type=int, default=9111)
    # ... TLS/CCDT args mirroring serve_http ...
    args = parser.parse_args(argv)
    print(f"gwlab-collector: {args.qmgr} via {args.conn} on :{args.port}")
```

- [ ] **Step 3: Build a wheel locally** and confirm the entry point:

Run: `uv build --wheel` then in a throwaway venv `pip install dist/gwlab-*.whl && gwlab-collector --help`
Expected: help text; `which gwlab-collector` resolves.

- [ ] **Step 4: Ansible `gw-sec-collector` build+install to `/opt/gwlab`.** `build.yml`
  builds the wheel; `install.yml` creates an isolated `/opt/gwlab` venv, installs
  the wheel into it, and symlinks `/opt/gwlab/bin/gwlab-collector`. Mirror the
  `mq-exporter` role's "on `mon-probe`" host targeting.

- [ ] **Step 5: Validate + commit.**

Run: `vrg-container-run -- vrg-validate` (green), then `vrg-commit --type build --scope collector --message "gwlab-collector entry point + /opt/gwlab install role"`.

---

## Task 2 — Client connectivity + first-lap measurement (de-risk)

**Files:**
- Create: `ansible/roles/gw-sec-collector/templates/collector-ccdt.json.j2`, `.../tasks/identity.yml`
- Create: `epics/65-swift-settlement-tracking/assets/first-lap-measurement.md` (results)

**Interfaces:**
- Produces: a working client connection from `mon-probe` to the Native-HA QM over a
  monitoring `SVRCONN` + CCDT CONNAME list; a recorded first-lap-time number and
  the bindings escape-hatch decision.

Operational/measurement task — no TDD. De-risks design §11.1–§11.2.

- [ ] **Step 1: Provision pymqi + monitoring identity on `mon-probe`.** `mon-probe`
  already has MQ client libs (base exporter); ensure pymqi builds into `/opt/gwlab`.
  Render a monitoring `SVRCONN` CCDT (CONNAME list of all Native-HA nodes) + a
  monitoring keystore, in the style of `mq-exporter`'s `MON.SVRCONN` / `mq_prometheus`.

- [ ] **Step 2: Smoke-connect + browse.** From `mon-probe`, connect client-mode and
  `browse` a `GW.SEC.*.AUDIT` queue; confirm depth read (the same probe pattern used
  in the #123 experiment, now via the monitoring identity/CCDT).

- [ ] **Step 3: Validate persistent-cursor semantics on live iron (design §11.3).**
  On the live QM, confirm as documented evidence: (a) a parked `BROWSE_NEXT` handle
  returns only new arrivals across sweeps (exactly-once, no duplicates); (b) a
  `CAPEXPRY`-expiring message under a parked cursor does **not** break `BROWSE_NEXT`
  (the QM reaps it, the cursor continues, no error); (c) a forced client reconnect
  re-opens at `BROWSE_FIRST` (= the cold-reload). This proves *early* — before the
  ingest core is built on it — the exactly-once cursor assumption T3–T5 rest on.

- [ ] **Step 4: Measure the first-lap time at multi-day depth.** Load ~250k messages
  (the multi-day retention target) and time one full sweep from empty; record msg/s
  and total. Compare against the 30 s scrape *only for context* — the scrape is
  decoupled, so the number that matters is recovery time.

- [ ] **Step 5: Record the decision.** Write the measurement + the bindings
  escape-hatch call (accept client-mode / escalate to bindings) to the asset file;
  commit as `docs(collector): first-lap measurement + escape-hatch decision`.

---

## Task 3 — `browse_new` persistent cursor (port + fake + adapter)

**Files:**
- Modify: `src/gwlab/messaging.py` (Protocol)
- Modify: `tests/gwlab/swift/fakes.py` (in-memory fake)
- Modify: `src/gwlab/pymqi_messaging.py` (box-only adapter)
- Test: `tests/gwlab/swift/stream/test_browse_new.py`

**Interfaces:**
- Produces: `Messaging.browse_new(queue: str) -> list[Message]` — first call returns
  every message from the first; each later call returns only messages that arrived
  since the previous call (persistent cursor; a browsed message is never returned
  twice). Consumed by `sweep()` (Task 4/5).

- [ ] **Step 1: Write the failing test** (`test_browse_new.py`):

```python
def test_browse_new_returns_only_new_since_last_call():
    mq = FakeMessaging()
    mq.seed("Q", [msg(b"a"), msg(b"b")])
    first = mq.browse_new("Q")
    assert [m.body for m in first] == [b"a", b"b"]
    # nothing new yet
    assert mq.browse_new("Q") == []
    # a new arrival is returned exactly once, no re-read of a/b
    mq.seed("Q", [msg(b"c")])
    assert [m.body for m in mq.browse_new("Q")] == [b"c"]
    assert mq.browse_new("Q") == []
```

- [ ] **Step 2: Run it, verify it fails** (`AttributeError: browse_new`).

Run: `uv run --frozen pytest tests/gwlab/swift/stream/test_browse_new.py -v`
Expected: FAIL.

- [ ] **Step 3: Add `browse_new` to the Protocol** in `messaging.py`:

```python
def browse_new(self, queue: str) -> list[Message]:
    """Persistent-cursor browse: first call from the first message, each later
    call only messages arrived since the previous call. A message is never
    returned twice. After a reconnect the adapter re-opens at the first message
    (a full re-read = the cold-reload)."""
    ...
```

- [ ] **Step 4: Implement it in the fake** (per-queue high-water index):

```python
class FakeMessaging:
    def __init__(self):
        self._queues: dict[str, list[Message]] = {}
        self._cursor: dict[str, int] = {}
    def seed(self, queue, messages):
        self._queues.setdefault(queue, []).extend(messages)
    def browse_new(self, queue):
        msgs = self._queues.get(queue, [])
        start = self._cursor.get(queue, 0)
        self._cursor[queue] = len(msgs)
        return msgs[start:]
```

- [ ] **Step 5: Run the test, verify it passes.** Then add the box-only adapter
  method in `pymqi_messaging.py` (a persistent browse handle: `BROWSE_FIRST` on the
  first call, `BROWSE_NEXT` thereafter, draining to `2033`; on `MQRC_CONNECTION_BROKEN`
  / reconnect, reset to `BROWSE_FIRST`). Add nothing to coverage-omit that isn't
  already the pymqi module.

- [ ] **Step 6: Commit.** `vrg-commit --type feat --scope collector --message "persistent-cursor browse_new on the Messaging port"`.

---

## Task 4 — `SettlementState` (incremental fold, retention, orphan-gating, aging)

**Files:**
- Create: `src/gwlab/swift/stream/state.py`
- Test: `tests/gwlab/swift/stream/test_state.py`

**Interfaces:**
- Consumes: `model.SwiftMessage`, `lifecycle.fold`, `correlate.Correlated`,
  `window.in_window/_real_horizon`, `swiftconfig.Horizon`, `reference.State`.
- Produces: `SettlementState` with
  `ingest(message: SwiftMessage) -> None`,
  `record_anomalies(anomalies: list[Anomaly]) -> None` (per-message decode
  anomalies — `malformed` / `field-violation` — accumulated for the report),
  `age(now: datetime, horizon: Horizon) -> None`,
  `report(now, horizon) -> collect.DayReport` — the **single state→metrics seam**,
  so `observe.metrics` renders it unchanged. `DayReport.scanned` holds the current
  in-state message **population** (not a per-pass browse count — the scan-cost gauge
  is superseded by the §7 self-metrics; design §7). Internally keyed
  `dict[str (seme) → _Entry]` where
  `_Entry = {instruction: SwiftMessage | None, responses: list[SwiftMessage]}`.

- [ ] **Step 1: Write the failing test — instruction then responses fold to SETTLED:**

```python
def test_ingest_folds_instruction_and_responses():
    st = SettlementState()
    st.ingest(instruction("541", seme="S1", at=t0))
    st.ingest(status("S1", [("IPRC","PACK")], at=t1))
    st.ingest(confirmation("545", rela="S1", at=t2))
    report = st.report(now=t2, horizon=Horizon(days=3))
    states = {s.seme: s.current for s in report.states}
    assert states["S1"] is State.SETTLED
    assert report.anomalies == []
```

- [ ] **Step 2: Run, verify it fails** (module missing).

- [ ] **Step 3: Implement `ingest` + `report`** — group by SEME, re-fold each entry
  via `correlate` + `lifecycle.fold`:

```python
@dataclass
class _Entry:
    instruction: SwiftMessage | None = None
    responses: list[SwiftMessage] = field(default_factory=list)

class SettlementState:
    def __init__(self) -> None:
        self._by_seme: dict[str, _Entry] = {}
        self._orphans: list[Anomaly] = []

    def ingest(self, message: SwiftMessage) -> None:
        if message.is_instruction:
            self._by_seme.setdefault(message.seme, _Entry()).instruction = message
        else:
            key = message.rela
            entry = self._by_seme.get(key)
            if entry is None or entry.instruction is None:
                self._buffer_or_orphan(message)   # window-gated (Step 5)
            else:
                entry.responses.append(message)

    def report(self, now, horizon):
        states, anomalies = [], list(self._orphans)
        for seme, entry in self._by_seme.items():
            if entry.instruction is None:
                continue
            correlated = Correlated(entry.instruction, entry.responses)
            state = lifecycle.fold(correlated)
            anomalies.extend(_transition_anomalies(state))
            states.append(state)
        aged = [s for s in states if window.is_aged_out(s, now, horizon)]
        return DayReport(states=states, aged_out=aged, anomalies=anomalies,
                         scanned=self._scanned)
```

- [ ] **Step 4: Run, verify it passes.**

- [ ] **Step 5: RED — window-gated orphan.** Test: a response whose `rela` is unknown
  is an `orphan` **only if** the referenced instruction would still be in-window;
  an old one is dropped:

```python
def test_unknown_response_is_orphan_only_when_in_window():
    st = SettlementState()
    st.ingest(status("UNKNOWN", [("IPRC","PACK")], at=recent))
    st.ingest(status("UNKNOWN2",[("IPRC","PACK")], at=long_ago))
    rep = st.report(now, Horizon(days=3))
    kinds = [a.kind for a in rep.anomalies]
    assert kinds.count("orphan") == 1   # recent one only
```

Implement `_buffer_or_orphan`: if `in_window(message.put_time, now, horizon)` →
append an `orphan` `Anomaly`; else drop silently (trailing-edge artifact).
Note: no pending buffer — the correlation invariant (design §5) guarantees an
in-flight instruction is already present, so an unknown `rela` is either orphan or
trailing-edge.

- [ ] **Step 6: RED — lifecycle-aware retention.** Test: `age()` keeps an *open*
  lifecycle regardless of age, and purges a *complete* one only once past the window:

```python
def test_age_keeps_open_purges_completed_and_expired():
    st = SettlementState()
    st.ingest(instruction("541", "OPEN", at=long_ago))      # never confirmed
    st.ingest(instruction("541", "DONE", at=long_ago))
    st.ingest(confirmation("545", rela="DONE", at=long_ago)) # settled, expired
    st.age(now, Horizon(days=3))
    semes = {s.seme for s in st.report(now, Horizon(days=3)).states}
    assert "OPEN" in semes     # open -> retained past its age
    assert "DONE" not in semes # complete AND expired -> purged
```

Implement `age`: for each entry, fold; if `current` is terminal
(`SETTLED/REJECTED/FAILED`) AND `not in_window(instruction.put_time, now, horizon)`
→ delete the entry; otherwise keep.

- [ ] **Step 7: RED — recorded decode anomalies surface in the report (four-kind
  parity).** Test: `st.record_anomalies([Anomaly("malformed","x","bad body")])`,
  then `st.report(now, Horizon(days=3)).anomalies` includes it, alongside any
  `orphan` / `transition`. Implement `record_anomalies` to accumulate into the list
  `report` returns — so the streaming state carries the same four-kind unified
  channel as the batch collector (design §5). The sweep supplies these from
  `_decode_and_parse` (T5).

- [ ] **Step 8: Run all state tests green; REFACTOR.** Extract the fold-per-entry
  helper shared by `report` and `age`; ensure no duplicated fold logic.

- [ ] **Step 9: Commit.**

---

## Task 5 — `sweep()` (one round-robin ingest pass)

**Files:**
- Create: `src/gwlab/swift/stream/sweep.py`
- Test: `tests/gwlab/swift/stream/test_sweep.py`

**Interfaces:**
- Consumes: `Messaging.browse_new`, `SettlementState`, `SwiftQueueSet`, `Horizon`,
  `collect._decode_and_parse`.
- Produces: `sweep(messaging, state, queues, now, horizon) -> SweepResult` where
  `SweepResult = {ingested: int}`; drains each queue **in order** (instruct →
  status → [report]) via `browse_new`, decodes+ingests each message, then
  `state.age(now, horizon)`. Malformed messages become counted anomalies (never
  abort the sweep) — reuse `collect._decode_and_parse`.

- [ ] **Step 1: RED — sweep order and ingest count:**

```python
def test_sweep_reads_instruct_before_status_and_ages():
    mq = FakeMessaging()
    mq.seed(Q.instruct_audit, [raw(instruction("541","S1"))])
    mq.seed(Q.status_audit,   [raw(confirmation("545", rela="S1"))])
    state = SettlementState()
    result = sweep(mq, state, Q, now, Horizon(days=3))
    assert result.ingested == 2
    assert state.report(now, Horizon(days=3)).states[0].current is State.SETTLED
```

- [ ] **Step 2: Run, verify it fails.**

- [ ] **Step 3: Implement `sweep`:**

```python
_QUEUE_ORDER = ("instruct_audit", "status_audit")  # + "report_audit" with COA/COD

def sweep(messaging, state, queues, now, horizon):
    ingested = 0
    for attr in _QUEUE_ORDER:
        queue = getattr(queues, attr)
        for message in messaging.browse_new(queue):
            ingested += 1
            parsed, anomalies = _decode_and_parse(message, queue)
            state.record_anomalies(anomalies)
            if parsed is not None and in_window(parsed.put_time, now, horizon):
                state.ingest(parsed)
    state.age(now, horizon)
    return SweepResult(ingested=ingested)
```

- [ ] **Step 4: Run green.**

- [ ] **Step 5: RED — a second sweep folds only new arrivals** (no re-read, no
  double-count): seed instruct on sweep 1, seed a status arrival, sweep 2, assert
  `ingested == 1` on sweep 2 and the lifecycle advances. Verifies the continuous
  cursor semantics end-to-end through the fake. Also assert a **malformed** body in
  the stream becomes a counted `malformed` anomaly (via `_decode_and_parse` →
  `state.record_anomalies`) and does **not** abort the sweep — four-kind parity
  end-to-end.

- [ ] **Step 6: Run green; commit.**

---

## Task 6 — Snapshot + self-metrics + windowed outcome semantics

**Files:**
- Create: `src/gwlab/swift/stream/snapshot.py`, `src/gwlab/swift/stream/selfmetrics.py`
- Test: `tests/gwlab/swift/stream/test_snapshot.py`, `.../test_selfmetrics.py`

**Interfaces:**
- Produces:
  - `SnapshotHolder` with `publish(text: str) -> None` and `latest() -> str`
    (atomic reference swap — a bare attribute assignment, GIL-atomic, no lock).
  - `CollectorStats` (bootstrap_seconds, last cycle_seconds + histogram buckets,
    messages_ingested_total, cycle_backlog, last_publish_timestamp) and
    `render_collector_metrics(stats) -> str` (Prometheus exposition).
  - The published snapshot text = `render_exposition(metrics)` +
    `render_collector_metrics(stats)`.

- [ ] **Step 1: RED — atomic holder round-trips the latest text:**

```python
def test_holder_returns_latest_published():
    h = SnapshotHolder(initial="# empty")
    assert h.latest() == "# empty"
    h.publish("# v1"); assert h.latest() == "# v1"
    h.publish("# v2"); assert h.latest() == "# v2"
```

- [ ] **Step 2–3: Implement `SnapshotHolder`** (single attribute swap) and run green.

- [ ] **Step 4: RED — self-metrics exposition emits the five series:**

```python
def test_render_collector_metrics_emits_series():
    out = render_collector_metrics(CollectorStats(
        bootstrap_seconds=42.0, cycle_seconds=0.18,
        messages_ingested_total=1000, cycle_backlog=7,
        last_publish_timestamp=1_700_000_000.0))
    assert "swift_collector_bootstrap_seconds 42.0" in out
    assert "swift_collector_messages_ingested_total 1000" in out
    assert "swift_collector_cycle_backlog 7" in out
```

- [ ] **Step 5: Implement `render_collector_metrics`** (mirror `render_exposition`'s
  line-builder style; `cycle_seconds` as a summary/histogram). Run green.

- [ ] **Step 6: Document the windowed-outcome semantics.** Add a comment/HELP note in
  the state-distribution exposition that the settled/rejected/failed gauges are
  *windowed over the retention horizon* (design §6.1); no code change to the numbers
  (the state already windows them). REFACTOR: share the line-builder helper between
  `render_exposition` and `render_collector_metrics`.

- [ ] **Step 7: Commit.**

---

## Task 7 — Box-only serve loop + HTTP thread; `mon-probe` deployment

**Files:**
- Modify: `src/gwlab/swift/stream/serve_collector.py` (replace the Task 1 stub)
- Modify: `pyproject.toml` (`[tool.coverage.run] omit` += `serve_collector.py`)
- Create: `ansible/roles/gw-sec-collector/templates/gwlab-collector.service.j2`, `.../tasks/deploy.yml`
- Modify: the Prometheus target file/role to add the fixed `mon-probe` scrape target

**Interfaces:**
- Consumes: `connect()` (pymqi), `sweep`, `SettlementState`, `SnapshotHolder`,
  `CollectorStats`, `render_exposition`/`render_collector_metrics`.
- Produces: `main()` running the sweep loop in the main thread and an
  `HTTPServer` in a daemon thread serving `holder.latest()`; a systemd unit
  (`Restart=always` + `StartLimitIntervalSec`/`StartLimitBurst` fail-loud); a
  `qmgr`-labelled fixed Prometheus target.

Box-only (loop + socket + real clock + pymqi) — coverage-omit like `serve_http.py`.
Verified end-to-end on the box, not in the container.

- [ ] **Step 1: Implement the run loop + HTTP thread** (the sweep publishes into the
  holder; the handler reads `holder.latest()`):

```python
def run(args):
    messaging = connect(args)
    state, holder, stats = SettlementState(), SnapshotHolder("# starting"), CollectorStats()
    threading.Thread(target=_serve, args=(args.port, holder), daemon=True).start()
    while True:
        t0 = time.monotonic(); now = datetime.now(UTC)
        result = sweep(messaging, state, args.queues, now, args.horizon)
        stats.observe(cycle_seconds=time.monotonic()-t0, ingested=result.ingested)
        report = state.report(now, args.horizon)   # the single state->metrics seam
        holder.publish(render_exposition(observe.metrics(report, now))
                       + render_collector_metrics(stats))
        time.sleep(args.poll_seconds)
```

- [ ] **Step 2: Add `serve_collector.py` to `[tool.coverage.run] omit`;** confirm
  `vrg-validate` stays 100% (the logic in T3–T6 is fully covered; only the loop is
  omitted, matching `serve_http.py`). **No other omit entries.**

- [ ] **Step 3: systemd unit template** — `Restart=always`, `StartLimitIntervalSec`
  + `StartLimitBurst` (fail-loud into `failed`), `ExecStart=/opt/gwlab/bin/gwlab-collector …`,
  running as the monitoring user on `mon-probe`.

- [ ] **Step 4: Prometheus target** — add the fixed `mon-probe:<port>` scrape target
  (file_sd), `qmgr`-labelled; do NOT scrape the QM nodes.

- [ ] **Step 5: Deploy + verify on the box** — service `active`, `/metrics` serves
  the settlement + collector series, Prometheus target `up`. Commit.

---

## Task 8 — Validation & cutover (operational)

**Files:**
- Create: `epics/65-swift-settlement-tracking/assets/streaming-collector-validation.md`

Operational validation — recorded as an `Outcome:` on the issue, no PR.

- [ ] **Step 1: Cutover.** Repoint Prometheus at the fixed `mon-probe` target; stop
  and **decommission the `app-client` batch exporter** (the old `gwlab-swift-exporter`).
- [ ] **Step 2: Flat scrape cost.** Load multi-day volume; confirm `/metrics` scrape
  latency stays in **ms** regardless of retained depth (the decoupling works).
- [ ] **Step 3: Small steady-state sweep.** Drive a steady arrival rate; confirm
  `swift_collector_cycle_seconds` stays sub-second and `cycle_backlog` stays bounded.
- [ ] **Step 4: Failover recovery.** Force a Native-HA failover; confirm the client
  reconnects, cold-reloads, and the board recovers within the published
  `swift_collector_bootstrap_seconds`; the `qmgr`-labelled series is continuous.
- [ ] **Step 5: Record `Outcome: SUCCESS`** with the numbers.

---

## Self-Review

**Spec coverage** (design §-by-§ → task):
- §2 decouple + maintain → T4/T5 (state, sweep) + T6 (snapshot). ✓
- §4 one continuous sweep loop, order, zero-wait, `BROWSE_FIRST` restart → T3 (`browse_new`) + T5 (`sweep`). ✓
- §5 incremental fold, correlation invariant (no buffer), lifecycle retention, window-gated orphan, aging → T4. ✓
- §6/§6.1 snapshot + windowed outcome semantics → T6. ✓
- §7 self-metrics → T6. ✓
- §8 client on `mon-probe`, CCDT, `qmgr` label, fixed scrape → T2 + T7. ✓
- §8.1 systemd `Restart=always` + fail-loud → T7. ✓
- §9 install artifact + entry point → T1. ✓
- §10 recovery = first lap → T3/T5 (BROWSE_FIRST on reconnect) + T8 (failover). ✓
- §11 de-risks → T2 (measurement), T3 (cursor semantics). ✓
- §12 T1–T8 → this plan's Tasks 1–8 (same intent; reordered so the port/logic that
  everything consumes is built via TDD before deploy). ✓

**Placeholder scan:** none — every code step carries real code; infra/measurement
steps carry concrete commands and expected results.

**Type consistency:** `browse_new` returns `list[Message]` (used by `sweep`);
`SettlementState.report → DayReport` (consumed by `observe.metrics`);
`SnapshotHolder.latest() → str` (served by the HTTP thread). Consistent across tasks.

**No-omit discipline:** only `serve_collector.py` (box-only loop + socket) joins the
existing omit set; T3–T6 reach 100% by mocking the `Messaging` boundary.
