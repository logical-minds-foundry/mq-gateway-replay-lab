# COA/COD transport-milestone telemetry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose #65's opaque `instructed->acknowledged` settlement leg into three attributable transport sub-legs — `sent->arrived` (delivery latency; the headline network-elimination signal), `arrived->delivered` (time on queue), `delivered->acknowledged` — plus two pre-response attention sets, by requesting MQ-native COA/COD reports on the existing outbound instruction and folding them into #65's **streaming collector**, at zero queue-manager cost.

**Architecture:** A bolt-on to the now-complete `gwlab.swift`. `gateway_run.put_instructions` (the live requester) requests plain COA/COD through the `GW.SEC.INSTRUCT` QALIAS; SVCQM returns them to a new terminal `GW.SEC.REPORT` ledger; the streaming collector browses it as a **third source** (a slot #65 already reserved in `sweep._QUEUE_ORDER`) and folds each report into its instruction by **self-join `CorrelId → MsgId → SEME`** in `SettlementState.ingest`. Transport metrics live on a **parallel path** and render as new `swift_settlement_*` Prometheus series + Grafana panels. A **gating spike** resolves the COA/COD × streaming-queue interaction first; a negative result is an acceptable terminal outcome.

**Tech Stack:** Python 3.12, pytest, the `gwlab` `Messaging` port (`browse_new` persistent cursor; `fake_messaging` ↔ `pymqi_messaging`), the streaming `gwlab.swift.stream` collector, Prometheus exposition + code-generated Grafana board (`board.py`), MQSC + Ansible (`gw-sec-queues`). Validation is `vrg-container-run -- vrg-validate` **only**.

## Global Constraints

Every task implicitly includes these (verbatim from the spec):

- **Zero queue-manager changes — parasite rule by construction.** Create only `GW.SEC.REPORT`. Nothing on the shared QM or SVCQM is created/mutated. Proof = before/after MQSC diff (Task 9).
- **Plain COA/COD only** (`MQRO_COA | MQRO_COD`, not `_WITH_DATA`); reports are **body-less** — never run through the SWIFT tokenizer.
- **Milestones are not `State`s.** Transport metrics are a **parallel path**, never through `observe._latencies`.
- **COA/COD feed source #1 (the browse collector) only** — self-join `CorrelId→MsgId→SEME` in `SettlementState`; not the app-side tracker, not #65's cross-validation.
- **Endpoint-named, ASCII labels** (`sent->arrived`, …); `sent->arrived` (network) is the foregrounded headline.
- **NTP assumed and stated;** negative `sent->arrived` → `clock-skew` anomaly, never a silent metric.
- **No silent failures.** Orphan/missing report, clock-skew → flagged, logged, never swallowed or crashing.
- **Repo conventions:** `vrg-git`/`vrg-gh`/`vrg-commit`; assigned feature-branch worktree; `vrg-validate` only.
- **Anonymization:** generic vocabulary; invented `GW.SEC.*`/`SVCQM`; no client-identifiable content.

---

## File Structure

```
ansible/roles/gw-sec-queues/
  defaults/main.yml   [Task 1]  add q_report GW.SEC.REPORT + horizon CAPEXPRY (terminal, no stream)
  tasks/main.yml      [Task 1]  create the one new QLOCAL

src/gwlab/
  messaging.py        [Task 2]  Message gains feedback; put gains report options; browse_new/browse/get read feedback
  fake_messaging.py   [Task 2]  honour report options; enqueue body-less reports over browse_new
  pymqi_messaging.py  [Task 2]  set MQMD Report/ReplyTo on put; read Feedback into Message
  swift/
    gateway_run.py    [Task 2/9] request COA/COD on put_instructions (gated); SENT-accuracy (per-instruction commit)
    swiftconfig.py    [Task 2/6] SwiftQueueSet gains instruct (QALIAS) + report_ledger; request_reports; Horizon.service_interval
    model.py          [Task 4]  SwiftMessage gains msg_id
    collect.py        [Task 4/5] batch parity: thread msg_id; report handling in the batch loop; DayReport.transport
    correlate.py /
    lifecycle.py      [Task 4]  carry msg_id onto the correlated instruction / InstructionState
    stream/
      sweep.py        [Task 5]  add report_audit to _QUEUE_ORDER (the reserved slot)
      state.py        [Task 4/5] entries carry msg_id; SettlementState.ingest report branch (self-join CorrelId->MsgId->SEME)
      snapshot.py /
      selfmetrics.py  [Task 6]  expose transport legs + attention sets + new anomaly kinds as swift_settlement_* series
    reports.py        [Task 5]  ReportRecord(feedback, correl_id, put_time) + milestone_of() (uses Task 3 mapping)
    observe.py        [Task 6]  Metrics gains transport legs + attention sets; _ANOMALY_KINDS += orphan-report, clock-skew
    board.py          [Task 7]  Grafana panels: transport decomposition + attention sets (swift_settlement_*)
    dashboard.py      [Task 7]  parallel HTML board update
    directive.py      [Task 8]  Directive gains pickup_delay / withhold_pickup
    serve_responder.py[Task 8]  per-instruction get+commit UOW (enables dwell + exact time-on-queue)

docs/
  coa-cod-streaming-findings.md  [Task 3]  gating-spike findings (cited doc research + live experiment)

tests/swift/ , tests/            one test module per source module touched
```

---

## Phase 0 — Enable & prove the mechanism (gating)

### Task 1: Provision the `GW.SEC.REPORT` terminal ledger

**Files:** Modify `ansible/roles/gw-sec-queues/defaults/main.yml`, `tasks/main.yml`; Test `tests/test_gw_sec_report_queue.py`.

**Interfaces:** Produces a QLOCAL `GW.SEC.REPORT`, `CAPEXPRY({{ gw_sec_capexpry }})`, `MAXDEPTH({{ gw_sec_max_depth }})`, **no `STREAMQ`**, named by a new `q_report` var (matching `q_instruct`/`q_reply`) — the collector's third source (Task 5) and reply target (Task 2).

- [ ] **Step 1: Write the failing test**

```python
def test_report_queue_is_terminal_capexpry_ledger():
    objs = render_gw_sec_objects(defaults)
    rep = objs["GW.SEC.REPORT"]
    assert rep["type"] == "QLOCAL" and rep["CAPEXPRY"] == defaults["gw_sec_capexpry"]
    assert "STREAMQ" not in rep
    assert "GW.SEC.REPORT" not in [live for live, _ in defaults["gw_sec_streams"]]
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_gw_sec_report_queue.py -v`.
- [ ] **Step 3: Add** `q_report: "GW.SEC.REPORT"` to `defaults/main.yml`, include it in the created-QLOCAL list (not in `gw_sec_streams`); add to the `tasks/main.yml` object loop.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope 105 --message "gw-sec-queues: GW.SEC.REPORT terminal report ledger"`.

### Task 2: Report options on the existing put + `feedback` on messages

**Files:** Modify `src/gwlab/messaging.py`, `fake_messaging.py`, `pymqi_messaging.py`, `src/gwlab/swift/gateway_run.py`, `src/gwlab/swift/swiftconfig.py`; Test `tests/test_messaging_reports.py`, `tests/swift/test_gateway_run_reports.py`.

**Interfaces:**
- `Message(..., feedback: int | None = None)`; read in the `Message(...)` sites for `browse_new`/`browse`/`get`.
- `Messaging.put(queue, message, *, report_opts: ReportOpts | None = None)`, `ReportOpts = namedtuple("ReportOpts","report reply_to_q reply_to_qmgr")`.
- `SwiftQueueSet` gains `instruct: str = "GW.SEC.INSTRUCT"`, `report_ledger: str = "GW.SEC.REPORT"`, `request_reports: bool = True`.
- `gateway_run.put_instructions` sets `report_opts` when `request_reports`.

- [ ] **Step 1: Write the failing test**

```python
def test_put_instructions_requests_coa_cod_when_enabled():
    m = FakeMessaging(); q = SwiftQueueSet(request_reports=True)
    put_instructions(m, q, [make_instruction(seme="ABC")])
    put = m.puts[-1]
    assert put.queue == "GW.SEC.INSTRUCT"                    # the QALIAS
    assert put.report_opts.report == MQRO_COA | MQRO_COD     # plain
    assert put.report_opts.reply_to_q == "GW.SEC.REPORT"

def test_put_instructions_no_reports_when_disabled():
    m = FakeMessaging(); q = SwiftQueueSet(request_reports=False)
    put_instructions(m, q, [make_instruction(seme="ABC")])
    assert m.puts[-1].report_opts is None
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add `feedback` to `Message`; extend `Messaging.put` + both impls (`fake` records `report_opts`; `pymqi` maps to MQMD `Report`/`ReplyToQ`/`ReplyToQMgr`); read `Feedback` into `Message` on `browse_new`/`browse`/`get`; add the config fields; wire `gateway_run.put_instructions` to pass `report_opts` when `request_reports`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: SENT-accuracy** — change `put_instructions` to **commit per-instruction** (or stamp `put_time` at commit); test that each instruction's SENT is its own commit time.
- [ ] **Step 6: `browse_new` report fixture** — `FakeMessaging.enqueue_report(queue, correl_id, feedback, put_time)` yields a body-less `Message` with `feedback`; served via `browse_new`.
- [ ] **Step 7: Commit** — `--type feat --scope 105 --message "swift: request plain COA/COD on the gateway put; feedback on Message; per-instruction SENT"`.

### Task 3: Gating spike — COA/COD × streaming-queue (doc + live experiment)

**Files:** Create `docs/coa-cod-streaming-findings.md`; Test `tests/test_coa_cod_findings.py`.

**Interfaces:** Produces the **milestone-mapping decision** consumed by Task 5 (which observed report → `ARRIVED`/`DELIVERED`; local-COA discrimination field), as a small cited table.

> **Precondition (now satisfied by #65):** the real flow exists (`gateway_run` → QALIAS → SVCQM → responder). Verify the round-trip first; a missing round-trip is a loud precondition failure.

- [ ] **Step 1: Doc-research track** — from the cited IBM sources (spec §14), record COA/COD behaviour under `STREAMQ`/MUSTDUP: local-queue COA? transmission-queue exclusion on the remote leg? descriptor propagation through the duplicate? Cite each; mark silences.
- [ ] **Step 2: Live-experiment track** — enable `request_reports` for **one** instruction via `gateway_run` against the live lab; capture what lands on `GW.SEC.REPORT` (count, `Feedback`, generating QM, put-times) via `runmqsc`/`amqsbcg`. Record vs the opening hypothesis (a COA per arrival + one COD).
- [ ] **Step 3: Decide the milestone mapping** — write the mapping table (observed report → `ARRIVED`/`DELIVERED`; local-COA discriminator) into the findings doc.
- [ ] **Step 4: Self-consistency test** — every mapping row has a citation id or an experiment-run id.
- [ ] **Step 5: Go / no-go** — proceed if it composes; else record the well-characterised negative finding, cut downstream tasks, conclude at the retrospective (spec §4).
- [ ] **Step 6: Commit** — `--type docs --scope 105 --message "gating-spike findings: COA/COD x streaming-queue interaction"`.

---

## Phase 1 — Correlation plumbing (gated by Task 3 go)

### Task 4: Thread `msg_id` through the streaming path (+ batch parity)

**Files:** Modify `src/gwlab/swift/model.py`, `stream/state.py`, `stream/sweep.py`, `collect.py`, `correlate.py`, `lifecycle.py`; Test `tests/swift/test_model.py`, `tests/swift/stream/test_state_msgid.py`.

**Interfaces:** `SwiftMessage.msg_id: bytes | None`; the streaming instruction entry (`stream/state.py`) carries `msg_id`; `sweep` passes `Message.msg_id` (from `browse_new`) into the fold. Batch `InstructionState.msg_id` for parity.

- [ ] **Step 1: Write the failing test** — an ingested instruction exposes its MsgId in the streaming state.

```python
def test_settlement_state_entry_carries_msg_id():
    st = SettlementState(horizon=H)
    st.ingest(instruction_message(seme="ABC", msg_id=b"MID-1", put_time=T0))
    assert st.entry_for("ABC").msg_id == b"MID-1"
```

- [ ] **Step 2: Run to verify it fails** (no `msg_id` on the entry).
- [ ] **Step 3: Implement** — add `msg_id` to `SwiftMessage`; in `sweep`/`SettlementState.ingest` capture `Message.msg_id` onto the instruction entry and build a `msg_id → seme` index; thread `msg_id` through the batch `model`/`collect`/`correlate`/`lifecycle` for parity.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift: carry instruction MsgId through the streaming state (+ batch parity)"`.

### Task 5: Report ingest — third source + self-join in `SettlementState`

**Files:** Create `src/gwlab/swift/reports.py`; Modify `stream/sweep.py`, `stream/state.py`, `collect.py`; Test `tests/swift/test_reports.py`, `tests/swift/stream/test_report_ingest.py`.

**Interfaces:**
- `reports.ReportRecord(correl_id, feedback, put_time)`; `reports.milestone_of(record) -> "arrived"|"delivered"|None` (Task 3 mapping).
- `SettlementState` entry gains `TransportTimeline(sent, arrived, delivered)`; `ingest` routes a body-less report by `correl_id → (msg_id index) → seme` and sets the milestone. `DayReport.transport: dict[str, TransportTimeline]` keyed by SEME (batch parity).

- [ ] **Step 1: Write the failing test**

```python
def test_report_folds_into_transport_timeline_via_self_join():
    st = SettlementState(horizon=H)
    st.ingest(instruction_message(seme="ABC", msg_id=b"MID-1", put_time=T0))
    st.ingest(report_message(correl_id=b"MID-1", feedback=MQFB_COA, put_time=T1))
    st.ingest(report_message(correl_id=b"MID-1", feedback=MQFB_COD, put_time=T2))
    tl = st.entry_for("ABC").transport
    assert (tl.sent, tl.arrived, tl.delivered) == (T0, T1, T2)

def test_report_is_not_tokenized():
    st = SettlementState(horizon=H)
    st.ingest(report_message(correl_id=b"MID-x", feedback=MQFB_COA, put_time=T1))
    assert not [a for a in st.report().anomalies if a.kind == "malformed"]
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add `report_audit` to `sweep._QUEUE_ORDER` (the reserved slot); in `SettlementState.ingest`, detect a body-less report (`feedback` set), map to a milestone, self-join via the `msg_id → seme` index (holding it in orphan-gating if the instruction is not yet seen, then re-joining), and set `TransportTimeline`. Mirror in the batch `collect.collect` loop + `DayReport.transport`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Orphan-report test** — a report whose `correl_id` maps to no SEME (after the gating window) becomes an `orphan-report` anomaly (Task 6), never a crash.
- [ ] **Step 6: Commit** — `--type feat --scope 105 --message "swift.stream: fold COA/COD into per-instruction transport timelines (third source, CorrelId->MsgId->SEME self-join)"`.

---

## Phase 2 — Metrics, exposition, board

### Task 6: Transport metrics + attention sets + anomalies (container + exposition)

**Files:** Modify `src/gwlab/swift/observe.py`, `swiftconfig.py` (`Horizon.service_interval`), `stream/snapshot.py`, `stream/selfmetrics.py`; Test `tests/swift/test_observe_transport.py`, `tests/swift/stream/test_transport_exposition.py`.

**Interfaces:** `observe.Metrics` gains `transport_legs: dict[str, LatencyStats]` (keys `sent->arrived`, `arrived->delivered`, `delivered->acknowledged`), `awaiting_coa`, `awaiting_cod`; `_ANOMALY_KINDS += ("orphan-report","clock-skew")`; the streaming exposition emits `swift_settlement_transport_leg_seconds{leg=...}` and `swift_settlement_awaiting{stage=...}` series and the new anomaly label values. Consumes `Horizon.service_interval: timedelta`.

- [ ] **Step 1: Write the failing tests**

```python
def test_transport_legs_ascii_and_time_on_queue_exact():
    legs = metrics(day, cfg).transport_legs
    assert set(legs) == {"sent->arrived","arrived->delivered","delivered->acknowledged"}
    assert legs["arrived->delivered"].mean == T2 - T1        # single-clock, exact

def test_negative_delivery_latency_is_clock_skew_anomaly():
    mx = metrics(day_with(sent=T2, arrived=T1), cfg)          # T1 < T2
    assert any(a.kind == "clock-skew" for a in mx.anomalies)
    assert mx.transport_legs.get("sent->arrived", empty).n == 0

def test_awaiting_cod_beyond_service_interval():
    day = day_with(arrived=T1, delivered=None, now=T1 + 2*cfg.horizon.service_interval)
    assert seme in [i.seme for i in metrics(day, cfg).awaiting_cod]

def test_transport_series_exposed():
    text = render_exposition(snapshot_with_transport)
    assert "swift_settlement_transport_leg_seconds" in text
    assert 'leg="sent->arrived"' in text
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** — compute the three legs on a **parallel path** (not `_latencies`): `sent->arrived`, `arrived->delivered` from the transport timelines; `delivered->acknowledged` by joining `delivered` to the folded `acknowledged` timestamp; route negative `sent->arrived` to a `clock-skew` anomaly and exclude it. Build `awaiting_coa`/`awaiting_cod` from timelines missing the next milestone past the threshold. Add the two anomaly kinds; add `Horizon.service_interval`. Emit the new series + anomaly labels from the exposition.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift: transport legs (parallel path) + awaiting-COA/COD + clock-skew/orphan-report anomalies; Prometheus exposition"`.

### Task 7: Board — front-of-lifecycle decomposition (Grafana + HTML)

**Files:** Modify `src/gwlab/swift/board.py`, `dashboard.py`; Test `tests/swift/test_board_transport.py`, `tests/swift/test_dashboard_transport.py`.

**Interfaces:** Grafana panels querying `swift_settlement_transport_leg_seconds` / `swift_settlement_awaiting`; `sent->arrived` foregrounded; HTML `dashboard.py` parallel rows.

- [ ] **Step 1: Write the failing test** — the generated board JSON contains a transport panel foregrounding `sent->arrived` and shows the two attention sets.

```python
def test_grafana_board_has_transport_decomposition():
    board = build_settlement_board(cfg)
    titles = [p["title"] for p in board["panels"]]
    assert any("delivery latency" in t.lower() or "sent->arrived" in t.lower() for t in titles)
    exprs = panel_exprs(board)
    assert any("swift_settlement_transport_leg_seconds" in e for e in exprs)
    assert any("swift_settlement_awaiting" in e for e in exprs)
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add a transport-decomposition panel group to `board.py` (delivery-latency panel foregrounded, then time-on-queue, then delivered->acknowledged, plus awaiting-COA/COD stat panels); update HTML `dashboard._latency_panel`/`_anomaly_panel` in parallel.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift.board: front-of-lifecycle transport decomposition (Grafana + HTML)"`.

---

## Phase 3 — Condition injection

### Task 8: Pickup-delay / withhold + per-instruction responder commit

**Files:** Modify `src/gwlab/swift/directive.py`, `serve_responder.py` (and `responder_app.py`/`scheduler.py` as needed); Test `tests/swift/test_condition_injection.py`.

**Interfaces:** `Directive` gains `pickup_delay: timedelta = 0`, `withhold_pickup: bool = False` (alongside the existing `delay_vector` ack timing). `serve_responder._drain_instructions` restructured to **per-instruction get+commit**, honouring `pickup_delay`/`withhold_pickup`.

- [ ] **Step 1: Write the failing test**

```python
def test_pickup_delay_shows_as_time_on_queue():
    day = run_live_sim(conditions={"ABC": Directive(pickup_delay=SECONDS(30))})
    assert day.transport_for("ABC").time_on_queue == approx(SECONDS(30), abs=TOL)

def test_withheld_pickup_lands_in_awaiting_cod():
    day = run_live_sim(conditions={"ABC": Directive(withhold_pickup=True)}, now_after=LONG)
    assert "ABC" in [i.seme for i in metrics(day, cfg).awaiting_cod]
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add the two fields to `Directive`; restructure `_drain_instructions` to get+commit per instruction, applying `pickup_delay` (schedule the get) and `withhold_pickup` (skip the get) via the existing min-heap scheduler; ack-delay stays as-is (`delay_vector`).
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift: pickup-delay/withhold injection + per-instruction responder commit"`.

---

## Phase 4 — Live validation (operational, issue #108)

### Task 9 (validation): Live run + transparency proof + time-sync check

**Files:** Evidence recorded as a comment on issue #108 (operational; not PR-workable).

**Acceptance (all required):**
- [ ] **Step 1: Baseline** — `runmqsc` dump of our QM and SVCQM **before**.
- [ ] **Step 2: Drive a day** — `gateway_run` with `request_reports=True` and a spread of injected conditions (pickup-delay, withhold, ack-delay).
- [ ] **Step 3: Confirm reports** — COA + COD on `GW.SEC.REPORT`, correlate by `MsgId → SEME`; the three sub-legs compute and render on the Grafana board with `sent->arrived` foregrounded.
- [ ] **Step 4: Transparency proof** — `runmqsc` dump **after**; diff vs baseline shows **no change** on either QM (headline acceptance).
- [ ] **Step 5: Time-sync sanity check** — record what keeps the QM host clocks synced and whether they are; a drift finding is recorded loudly.
- [ ] **Step 6: Record `Outcome: SUCCESS`** (or FAILURE with evidence) as the issue comment.

---

## Self-Review

**Spec coverage:** §2.1 (feed source #1) → Tasks 4/5. §3 report request → Task 2. §4 spike → Task 3. §5 report queue → Task 1. §6 msg_id join → Tasks 4/5. §7 three legs + attention sets → Task 6; §7.1 clock-skew → Task 6, SENT-accuracy → Task 2, per-instruction commit → Task 8, compression → noted (board labels real-time, Task 7). §8/§8.1 net-new 1–9 → Tasks 1–8. §9 orphan/missing/loud/skew → Tasks 5/6/2. §10 fake reports over browse_new → Task 2. §11 validation + time-sync → Task 9. §12 scope-out honoured. §13 success criteria → Tasks 1–9.

**Placeholder scan:** no TBD/TODO; each code step has concrete test/impl sketches at #65's plan altitude.

**Type consistency:** `msg_id: bytes | None` (Tasks 4–5); `feedback: int | None` (Task 2, used 5–6); `ReportRecord(correl_id, feedback, put_time)` (Task 5) consumed by Task 6; `TransportTimeline(sent, arrived, delivered)` (Task 5) consumed by Tasks 6–8; leg keys consistent Tasks 6–7; `Directive.pickup_delay/withhold_pickup` (Task 8) consumed by the responder.
```
