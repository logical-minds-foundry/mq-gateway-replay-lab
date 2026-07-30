# COA/COD transport-milestone telemetry — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose #65's opaque `instructed->acknowledged` settlement leg into three attributable transport sub-legs — `sent->arrived`, `arrived->delivered` (time on queue), `delivered->acknowledged` — plus two pre-response attention sets, by requesting MQ-native COA/COD reports on the outbound instruction and folding them into #65's collector, at zero queue-manager cost.

**Architecture:** A bolt-on to `gwlab.swift`. The outbound securities put (through the `GW.SEC.INSTRUCT` QALIAS) requests plain COA/COD; SVCQM returns them to a new terminal `GW.SEC.REPORT` ledger we own; the collector browses it as a **third source** with a **distinct MQMD-only handler** and folds each report into its instruction by `CorrelId (= MsgId)`. Transport metrics live on a **parallel path** off `observe.Metrics` (the milestones are not lifecycle `State`s). A **gating spike** (doc research + live experiment) resolves the undocumented COA/COD × streaming-queue interaction before any collector code is written; a well-characterised negative result is an acceptable terminal outcome.

**Tech Stack:** Python 3.12, pytest, the existing `gwlab` `Messaging` protocol (`fake_messaging` ↔ `pymqi_messaging`), the `gwlab.swift` collector/observe/dashboard, MQSC + Ansible (`gw-sec-queues`). Validation is `vrg-container-run -- vrg-validate` **only**.

## Global Constraints

Every task's requirements implicitly include these (verbatim from the spec):

- **Zero queue-manager changes — parasite rule by construction.** We create only `GW.SEC.REPORT` (a queue we own). Nothing on the shared lab QM or on SVCQM is created or mutated. The transparency proof is a before/after MQSC config diff (Task 9).
- **Plain COA/COD only.** `MQRO_COA | MQRO_COD`, *not* `_WITH_DATA`; no exception/expiry report handling. Reports are **body-less** — never routed through the SWIFT tokenizer.
- **Reporting / observability only.** No remediation/replay/resend on a missing report.
- **Milestones are not `State`s.** `SENT`/`ARRIVED`/`DELIVERED` do **not** enter #65's reachability graph or `State` enum; transport metrics are a **parallel path**, never through `observe._latencies`.
- **Endpoint-named, ASCII labels.** Rendered leg labels are lowercase ASCII: `sent->arrived`, `arrived->delivered`, `delivered->acknowledged` (matching `observe.py:94`). "delivery latency" / "time on queue" are glosses, not labels.
- **NTP sync assumed and stated.** Only `sent->arrived` is cross-clock; a negative/backwards value is an **anomaly** on the collector's channel, never a silent metric.
- **No silent failures.** Orphan report / missing report / implausible timing → flagged, logged with the offending id, never swallowed, never a hard crash.
- **Bolt-on; real flow is a prerequisite.** A real end-to-end securities flow (QALIAS put → STREAMQ → REMOTE → SVCQM → real destructive-get responder) is #65's correctness fix and this epic's precondition. If not yet landed when #105 starts, Task 2 establishes the QALIAS put.
- **Repo conventions.** `vrg-git`/`vrg-gh`/`vrg-commit` wrappers; work on the assigned feature-branch worktree; `vrg-validate` is the only validation command.
- **Anonymization.** Generic vocabulary only; invented `GW.SEC.*`/`SVCQM` names; no client-identifiable content anywhere.

---

## File Structure

```
ansible/roles/gw-sec-queues/
  defaults/main.yml   [Task 1]  add q_report GW.SEC.REPORT + horizon CAPEXPRY (terminal, no stream)
  tasks/main.yml      [Task 1]  create the one new QLOCAL (assert/verify pattern)

src/gwlab/
  messaging.py        [Task 2]  Message gains `feedback`; put gains report options (Report/ReplyToQ/ReplyToQMgr)
  fake_messaging.py   [Task 2]  honour report options / carry feedback; enqueue synthetic reports for tests
  pymqi_messaging.py  [Task 2]  set MQMD Report/ReplyTo on put; read Feedback on browse
  swift/
    swiftconfig.py    [Task 2/6] SwiftQueueSet gains `instruct` (QALIAS) + `report_ledger`; `request_reports`; Horizon gains `service_interval`
    send.py           [Task 2]  securities outbound put through the QALIAS, report-options gated by request_reports
    model.py          [Task 4]  SwiftMessage gains `msg_id`
    collect.py        [Task 4/5] thread msg_id; add GW.SEC.REPORT as a third source with an MQMD-only handler; DayReport.transport
    correlate.py      [Task 4]  carry msg_id onto the correlated instruction
    lifecycle.py      [Task 4]  InstructionState carries msg_id
    reports.py        [Task 5]  ReportRecord (feedback, correl_id, put_time) + milestone mapping (ARRIVED/DELIVERED)
    observe.py        [Task 6]  Metrics gains transport legs + two attention sets; orphan-report anomaly kind; negative-leg anomaly
    dashboard.py      [Task 7]  front-of-lifecycle transport decomposition panel
    responder.py /
    simulation.py     [Task 8]  condition injection (delay/withhold pickup+ack; per-instruction commit)

docs/
  coa-cod-streaming-findings.md  [Task 3]  the gating-spike findings note (cited doc research + live experiment)

tests/swift/ , tests/            one test module per source module touched
```

Each modified `src/gwlab/**/<mod>.py` has a sibling `tests/**/test_<mod>.py`. Files that change together live together.

---

## Phase 0 — Enable & prove the mechanism (gating)

### Task 1: Provision the `GW.SEC.REPORT` terminal ledger

**Files:**
- Modify: `ansible/roles/gw-sec-queues/defaults/main.yml`
- Modify: `ansible/roles/gw-sec-queues/tasks/main.yml`
- Test: `tests/test_gw_sec_report_queue.py` (renders/asserts the MQSC the role would apply)

**Interfaces:**
- Produces: a QLOCAL `GW.SEC.REPORT`, `CAPEXPRY({{ gw_sec_capexpry }})`, `MAXDEPTH({{ gw_sec_max_depth }})`, **no `STREAMQ`** (terminal ledger), listed in a new `gw_sec_report_queue` var — the collector's third browse source (Task 5) and the reply target (Task 2).

- [ ] **Step 1: Write the failing test** — assert the role's rendered object set includes a non-streaming `GW.SEC.REPORT` with the SEC-horizon CAPEXPRY and is **absent** from `gw_sec_streams` (it must never stream).

```python
def test_report_queue_is_terminal_capexpry_ledger():
    objs = render_gw_sec_objects(defaults)           # helper over defaults/main.yml
    rep = objs["GW.SEC.REPORT"]
    assert rep["type"] == "QLOCAL"
    assert rep["CAPEXPRY"] == defaults["gw_sec_capexpry"]     # 3-day horizon
    assert "STREAMQ" not in rep                                # terminal: no stream copy
    assert "GW.SEC.REPORT" not in [live for live, _ in defaults["gw_sec_streams"]]
```

- [ ] **Step 2: Run test to verify it fails** — `pytest tests/test_gw_sec_report_queue.py -v` → FAIL (queue absent).
- [ ] **Step 3: Add the queue** — in `defaults/main.yml` add `q_report: "GW.SEC.REPORT"` and include it in the created-QLOCAL list (the same assert/verify path as the audit QLOCALs) but **not** in `gw_sec_streams`; in `tasks/main.yml` add it to the object loop.
- [ ] **Step 4: Run test to verify it passes.**
- [ ] **Step 5: Commit** — `vrg-commit --type feat --scope 105 --message "gw-sec-queues: GW.SEC.REPORT terminal report ledger"`.

### Task 2: Report options on the outbound put + `feedback` on messages

**Files:**
- Modify: `src/gwlab/messaging.py` (`Message`, `Messaging` protocol)
- Modify: `src/gwlab/fake_messaging.py`, `src/gwlab/pymqi_messaging.py`
- Create: `src/gwlab/swift/send.py`
- Modify: `src/gwlab/swift/swiftconfig.py`
- Test: `tests/test_messaging_reports.py`, `tests/swift/test_send.py`

**Interfaces:**
- Produces:
  - `Message(..., feedback: int | None = None)` — `None` for application messages, `MQFB_COA`/`MQFB_COD` for reports.
  - `Messaging.put(queue, message, *, report_opts: ReportOpts | None = None)` where `ReportOpts = namedtuple("ReportOpts", "report reply_to_q reply_to_qmgr")`.
  - `swiftconfig.SwiftQueueSet` gains `instruct: str = "GW.SEC.INSTRUCT"` (QALIAS) and `report_ledger: str = "GW.SEC.REPORT"`; a `request_reports: bool = True` field.
  - `swift.send.send_instruction(messaging, queues, body, *, request_reports) -> bytes` — puts to `queues.instruct` (the QALIAS), returns the QM-minted `MsgId`; when `request_reports`, sets `Report = MQRO_COA | MQRO_COD`, `ReplyToQ = queues.report_ledger`, `ReplyToQMgr = <our qm>`.
- Consumes: Task 1's `GW.SEC.REPORT`.

- [ ] **Step 1: Write the failing test (send sets report options through the QALIAS)**

```python
def test_send_requests_coa_cod_through_qalias():
    m = FakeMessaging()
    q = SwiftQueueSet()                       # instruct="GW.SEC.INSTRUCT", report_ledger="GW.SEC.REPORT"
    msg_id = send_instruction(m, q, INSTRUCTION_BODY, request_reports=True)
    put = m.puts[-1]
    assert put.queue == "GW.SEC.INSTRUCT"                 # QALIAS, not the audit queue
    assert put.report_opts.report == MQRO_COA | MQRO_COD  # plain, no _WITH_DATA
    assert put.report_opts.reply_to_q == "GW.SEC.REPORT"
    assert msg_id == put.message.msg_id                   # QM-minted id returned

def test_send_without_reports_sets_no_options():
    m = FakeMessaging(); q = SwiftQueueSet()
    send_instruction(m, q, INSTRUCTION_BODY, request_reports=False)
    assert m.puts[-1].report_opts is None
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/swift/test_send.py -v` → FAIL (`send_instruction` undefined).
- [ ] **Step 3: Implement** — add `feedback` to `Message`; extend `Messaging.put` + both impls to accept `report_opts` (fake records it; pymqi maps it to MQMD `Report`/`ReplyToQ`/`ReplyToQMgr`); add the config fields; write `send.send_instruction`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Add the `feedback`-on-browse test** — `FakeMessaging.enqueue_report(queue, correl_id, feedback, put_time)` yields a body-less `Message` with `feedback` set; a browse returns it.
- [ ] **Step 6: Commit** — `--type feat --scope 105 --message "swift.send: request plain COA/COD through the GW.SEC.INSTRUCT QALIAS; feedback on Message"`.

### Task 3: Gating spike — COA/COD × streaming-queue (doc research + live experiment)

**Files:**
- Create: `docs/coa-cod-streaming-findings.md`
- Test: `tests/test_coa_cod_findings.py` (self-consistency: every asserted milestone-mapping row cites a source or an experiment run id)

**Interfaces:**
- Produces: the **milestone-mapping decision** consumed by Task 5 — which observed report (by generating QM / put-time) maps to `ARRIVED` vs `DELIVERED`, and how a local COA (if any) is discriminated. Encoded as a small table the findings doc cites and Task 5 imports as data.
- Consumes: Tasks 1–2 (a report-requesting put through the real flow + a queue to observe).

> **Precondition:** the real end-to-end flow (§Global Constraints). Verify it first: an instruction put through `GW.SEC.INSTRUCT` actually round-trips to SVCQM and is destructively consumed. If #65 has not landed this, establish the QALIAS put (Task 2 already does the app side) and confirm the round-trip; a missing round-trip is a loud precondition failure, not a workaround.

- [ ] **Step 1: Doc-research track** — from the cited IBM sources (spec §14), record what COA/COD generation is defined to do when the destination queue carries `STREAMQ`/MUSTDUP: local-queue COA on arrival? transmission-queue exclusion on the remote leg? descriptor-field propagation through the duplicate? Cite each; mark explicitly where the docs are silent.
- [ ] **Step 2: Live-experiment track** — enable `request_reports` for **one** instruction against the live lab; capture exactly what lands on `GW.SEC.REPORT` (count, `Feedback`, generating QM, put-times) via `runmqsc`/`amqsbcg` browse. Record against the opening hypothesis (a COA per arrival — local audit queue + SVCQM landing queue — plus one COD).
- [ ] **Step 3: Decide the milestone mapping** — write the mapping table (observed report → `ARRIVED`/`DELIVERED`, local-COA discrimination field) into the findings doc.
- [ ] **Step 4: Self-consistency test** — `pytest tests/test_coa_cod_findings.py -v`: every mapping row has a citation id or an experiment-run id; no row is asserted from memory.
- [ ] **Step 5: Go / no-go** — if the mechanism composes, proceed to Phase 1. **If the result is adverse** (no usable SVCQM COA/COD), record the well-characterised negative finding; downstream tasks are cut and the epic concludes at the retrospective (spec §4).
- [ ] **Step 6: Commit** — `--type docs --scope 105 --message "gating-spike findings: COA/COD x streaming-queue interaction"`.

---

## Phase 1 — Correlation plumbing (gated by Task 3 go)

### Task 4: Thread `msg_id` through model → correlate → lifecycle

**Files:**
- Modify: `src/gwlab/swift/model.py`, `collect.py`, `correlate.py`, `lifecycle.py`
- Test: `tests/swift/test_model.py`, `test_correlate.py`, `test_lifecycle.py`

**Interfaces:**
- Produces: `SwiftMessage.msg_id: bytes | None`; `InstructionState.msg_id: bytes | None` (the instruction's MsgId, the transport join key for Task 5).
- Consumes: nothing new (raw `Message.msg_id` already exists; today it is read only for anomaly labelling at `collect.py:92` and discarded).

- [ ] **Step 1: Write the failing test** — a folded instruction exposes the originating MsgId.

```python
def test_instruction_state_carries_msg_id():
    msg = SwiftMessage(mt="541", seme="ABC", rela=None, statuses=(), put_time=T0,
                       is_instruction=True, is_confirmation=False, msg_id=b"MID-1")
    state = fold(Correlated(instruction=msg, responses=()))
    assert state.msg_id == b"MID-1"
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`SwiftMessage` has no `msg_id`).
- [ ] **Step 3: Implement** — add `msg_id` to `SwiftMessage` (default `None`); in `collect._decode_and_parse` pass the browsed `message.msg_id` into the parsed instruction; carry it through `correlate` onto the `Correlated`/instruction and onto `InstructionState`.
- [ ] **Step 4: Run to verify it passes** (all three test modules).
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift: carry instruction MsgId through model/correlate/lifecycle"`.

### Task 5: Report browse + MQMD-only handler (third collector source)

**Files:**
- Create: `src/gwlab/swift/reports.py`
- Modify: `src/gwlab/swift/collect.py`
- Test: `tests/swift/test_reports.py`, `test_collect_reports.py`

**Interfaces:**
- Produces:
  - `reports.ReportRecord(correl_id: bytes, feedback: int, put_time: datetime)` and `reports.milestone_of(record) -> Literal["arrived","delivered"] | None` (using Task 3's mapping data).
  - `collect.DayReport` gains `transport: dict[bytes, TransportTimeline]` keyed by instruction MsgId, where `TransportTimeline(sent, arrived, delivered)` (datetimes, `None` until observed).
- Consumes: Task 3's milestone mapping; Task 4's `InstructionState.msg_id`; Task 1's `GW.SEC.REPORT`.

- [ ] **Step 1: Write the failing test** — a browsed COA then COD fold into the instruction's timeline by CorrelId.

```python
def test_reports_fold_into_transport_timeline():
    m = FakeMessaging()
    m.enqueue_instruction("GW.SEC.INSTRUCT.AUDIT", seme="ABC", msg_id=b"MID-1", put_time=T0)
    m.enqueue_report("GW.SEC.REPORT", correl_id=b"MID-1", feedback=MQFB_COA, put_time=T1)
    m.enqueue_report("GW.SEC.REPORT", correl_id=b"MID-1", feedback=MQFB_COD, put_time=T2)
    day = collect(m, SwiftQueueSet(), now=T3, horizon=H)
    tl = day.transport[b"MID-1"]
    assert (tl.sent, tl.arrived, tl.delivered) == (T0, T1, T2)

def test_report_is_not_tokenized():
    m = FakeMessaging()
    m.enqueue_report("GW.SEC.REPORT", correl_id=b"MID-x", feedback=MQFB_COA, put_time=T1)
    day = collect(m, SwiftQueueSet(), now=T3, horizon=H)
    assert not [a for a in day.anomalies if a.kind == "malformed"]   # body-less, never parsed
```

- [ ] **Step 2: Run to verify it fails** — FAIL (`transport` absent; report queue not browsed).
- [ ] **Step 3: Implement** — add a **third entry** to the collector browse loop for `queues.report_ledger` routed to an MQMD-only handler (`reports.ReportRecord`), **bypassing** `_decode_and_parse`; map each record to a milestone and set it on the instruction's `TransportTimeline` (seed `sent` from the instruction's `put_time`); add `transport` to `DayReport`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Orphan-report test** — a report whose `correl_id` matches no instruction yields an `orphan-report` anomaly (added in Task 6), never a crash. (Assert the record is captured for Task 6 to classify.)
- [ ] **Step 6: Commit** — `--type feat --scope 105 --message "swift.collect: browse GW.SEC.REPORT as a third source; fold COA/COD into per-instruction transport timelines"`.

---

## Phase 2 — Metrics, attention sets, dashboard

### Task 6: Transport metrics + attention sets + anomalies

**Files:**
- Modify: `src/gwlab/swift/observe.py`, `src/gwlab/swift/swiftconfig.py` (`Horizon.service_interval`)
- Test: `tests/swift/test_observe_transport.py`

**Interfaces:**
- Produces: `observe.Metrics` gains `transport_legs: dict[str, LatencyStats]` (keys `sent->arrived`, `arrived->delivered`, `delivered->acknowledged`), `awaiting_coa: list`, `awaiting_cod: list` (the two attention sets); `_ANOMALY_KINDS` gains `"orphan-report"` and `"clock-skew"`.
- Consumes: Task 5's `DayReport.transport`; the folded `acknowledged` `Hop.at` for the seam; `Horizon.service_interval` (a `timedelta`).

- [ ] **Step 1: Write the failing tests**

```python
def test_transport_legs_computed_and_ascii_labelled():
    legs = metrics(day, cfg).transport_legs
    assert set(legs) == {"sent->arrived", "arrived->delivered", "delivered->acknowledged"}
    assert legs["arrived->delivered"].mean == T2 - T1        # single-clock, exact

def test_negative_delivery_latency_is_anomaly_not_metric():
    # SENT (our clock) after ARRIVED (SVCQM clock) => skew
    day = day_with(sent=T2, arrived=T1)                       # T1 < T2
    mx = metrics(day, cfg)
    assert any(a.kind == "clock-skew" for a in mx.anomalies)  # flagged
    assert "sent->arrived" not in mx.transport_legs or mx.transport_legs["sent->arrived"].n == 0

def test_awaiting_cod_beyond_service_interval():
    day = day_with(arrived=T1, delivered=None, now=T1 + 2*cfg.horizon.service_interval)
    assert day_instruction in metrics(day, cfg).awaiting_cod   # service-interval-high analog

def test_orphan_report_flagged():
    assert any(a.kind == "orphan-report" for a in metrics(day_with_orphan_report, cfg).anomalies)
```

- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement** — compute the three legs on a **parallel path** (not via `_latencies`): `sent->arrived`, `arrived->delivered` from `DayReport.transport`; `delivered->acknowledged` by joining `delivered` to the folded `acknowledged` timestamp. Route a negative/backwards `sent->arrived` to a `clock-skew` anomaly and exclude it from the leg. Build `awaiting_coa`/`awaiting_cod` from timelines missing the next milestone past the threshold. Add the two anomaly kinds to `_ANOMALY_KINDS`; add `Horizon.service_interval`.
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift.observe: transport legs (parallel path) + awaiting-COA/COD sets + clock-skew/orphan-report anomalies"`.

### Task 7: Dashboard — front-of-lifecycle decomposition

**Files:**
- Modify: `src/gwlab/swift/dashboard.py`
- Test: `tests/swift/test_dashboard_transport.py`

**Interfaces:**
- Consumes: Task 6's `transport_legs`, `awaiting_coa`, `awaiting_cod`.
- Produces: a transport panel rendered at the head of the lifecycle board, three sub-leg rows + the two attention sets, ASCII labels, and the anomaly kinds surfaced in the existing anomaly panel.

- [ ] **Step 1: Write the failing test** — the rendered board contains a transport section decomposing `instructed->acknowledged` into the three sub-legs and shows the two attention-set counts.

```python
def test_board_renders_transport_decomposition():
    html = render_board(metrics(day, cfg))
    assert "sent-&gt;arrived" in html and "arrived-&gt;delivered" in html
    assert "delivered-&gt;acknowledged" in html
    assert "awaiting COD" in html
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add a `_transport_panel` drawn from `transport_legs` + the attention sets, positioned before the existing latency panel; ensure `_anomaly_panel` iterates the extended `_ANOMALY_KINDS`.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift.dashboard: front-of-lifecycle transport decomposition panel"`.

---

## Phase 3 — Condition injection

### Task 8: Condition-injecting service side

**Files:**
- Modify: `src/gwlab/swift/responder.py` and/or `src/gwlab/swift/simulation.py` (placement decided here per spec §8.1)
- Test: `tests/swift/test_condition_injection.py`

**Interfaces:**
- Produces: per-instruction injectable conditions — `pickup_delay`, `ack_delay`, `withhold_pickup` — applied by the responder, which **commits each get per-instruction** (so `arrived->delivered` is exact).
- Consumes: Tasks 2–7 (a full report→metric path to observe the injected conditions through).

- [ ] **Step 1: Write the failing test** — an injected pickup delay produces a matching `arrived->delivered`, and a withheld pickup lands the instruction in `awaiting_cod`.

```python
def test_pickup_delay_shows_as_time_on_queue():
    day = run_sim(conditions={"ABC": Condition(pickup_delay=SECONDS(30))})
    assert day.transport_for("ABC").time_on_queue == SECONDS(30)

def test_withheld_pickup_lands_in_awaiting_cod():
    day = run_sim(conditions={"ABC": Condition(withhold_pickup=True)}, now_after=LONG)
    assert "ABC" in [i.seme for i in metrics(day, cfg).awaiting_cod]
```

- [ ] **Step 2: Run to verify it fails.**
- [ ] **Step 3: Implement** — add the condition hooks to the responder/sim; ensure per-instruction commit; decide and document placement (rework #65 sim vs a #105 load task) in the module docstring.
- [ ] **Step 4: Run to verify it passes.**
- [ ] **Step 5: Commit** — `--type feat --scope 105 --message "swift: condition-injecting service side (pickup/ack delay, withhold; per-instruction commit)"`.

---

## Phase 4 — Live validation (operational, issue #108)

### Task 9 (validation): Live run + transparency proof + time-sync check

**Files:**
- Evidence recorded as a comment on issue #108 (operational task; not PR-workable).

**Acceptance (all required):**
- [ ] **Step 1: Capture baseline** — `runmqsc` dump of our QM and SVCQM object/config state **before** the run.
- [ ] **Step 2: Drive a day** — run the sim with `request_reports=True` and a spread of injected conditions.
- [ ] **Step 3: Confirm reports** — COA + COD present on `GW.SEC.REPORT`, correlate to instructions by `MsgId`; the three sub-legs compute and render on the board.
- [ ] **Step 4: Transparency proof** — `runmqsc` dump **after**; diff vs baseline shows **no change** on either QM. This diff is the headline acceptance.
- [ ] **Step 5: Time-sync sanity check** — record what keeps the QM host clocks in sync (presumed default host-sync) and whether they are in fact synced; a drift finding is recorded loudly, not corrected.
- [ ] **Step 6: Record `Outcome: SUCCESS`** (or FAILURE with evidence) as the issue comment.

---

## Self-Review

**Spec coverage:**
- §3 report request → Task 2. §4 spike (doc + experiment, negative-result-OK) → Task 3. §5 report queue → Task 1; third browse source → Task 5. §6 MsgId join → Task 4. §7 three legs + attention sets → Task 6; §7.1 clock-skew handling → Task 6; per-instruction commit → Task 8. §8/§8.1 net-new items 1–8 → Tasks 1–7; prerequisite item 0 → Task 3 precondition/Task 2. §9 orphan/missing/loud → Tasks 5, 6, 2. §10 fake-harness reports → Task 2. §11 validation + time-sync → Task 9. §12 scope-out (no `_WITH_DATA`, deferred tertiary) → honoured (not built). §13 success criteria → Tasks 1–9.
- Gap check: condition-injection placement is intentionally deferred to Task 8 (spec §8.1 sanctions this).

**Placeholder scan:** no TBD/TODO; each code step carries concrete test/impl sketches at #65's plan altitude.

**Type consistency:** `msg_id: bytes | None` (Tasks 4–5); `feedback: int | None` (Task 2, used Tasks 5–6); `ReportRecord(correl_id, feedback, put_time)` (Task 5) consumed by `observe` (Task 6); `TransportTimeline(sent, arrived, delivered)` (Task 5) consumed by Tasks 6–8; leg keys `sent->arrived`/`arrived->delivered`/`delivered->acknowledged` consistent across Tasks 6–7.
