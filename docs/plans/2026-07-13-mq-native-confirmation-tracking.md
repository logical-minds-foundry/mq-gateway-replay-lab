# MQ-native confirmation-tracking recommendation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a client-facing recommendation document (no code — prose, algorithms, MQSC, one dashboard screenshot) that shows how to track MQ message confirmations and enable replay via MQ-native **audit** queues, backed by a fresh last-hour live dashboard and a proven third algorithm (unexpected-confirmation detection).

**Architecture:** Three sequential GitHub issues under epic `logical-minds-foundry/.github#49`: **#52** renames `archive`→`AUDIT` across code/board/docs and removes a stale `CLAUDE.md` rule; **#49** adds Algorithm 3 (anomaly detection) + the last-hour simulation, runs it live, and captures the board screenshot; **#53** writes the document. Dependency order: **#52 → #49 → #53**.

**Tech Stack:** Python 3.12+, `pytest`, in-memory `FakeMessaging` for offline TDD, `pymqi` live adapter, Prometheus text exposition + code-templated Grafana board, Ansible (`gw-queues`, `gw-observability`), MQSC (`STREAMQ`/`STRMQOS(MUSTDUP)`/`CAPEXPRY`) on Native HA queue manager `NHAUAPP` over mutual TLS 1.3.

## Global Constraints

- **Validation is one command:** `vrg-container-run -- vrg-validate`. Never run individual linters/formatters. A task is "green" only when `vrg-validate` passes.
- **Git:** work only in the worktree `.worktrees/issue-49-confirmation-recommendation/` on branch `feature/49-confirmation-recommendation`. Use `vrg-git` for git and `vrg-commit --type <t> --scope <s> --message <m> [--body <b>]` for commits (raw `git commit`/`reset` are denied). End commit bodies with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Anonymization (absolute):** generic vocabulary only — "the application team", Counterparty A/B, invented queue/host names (`GW.CPB.SEND.AUDIT`, `NHAUAPP`). No client-identifiable content in any file or commit.
- **The document contains NO code.** Only prose, described algorithms (numbered steps referencing header fields), MQSC config snippets, and one embedded dashboard screenshot.
- **Naming:** the archive copies are **audit queues**. Python fields `send_audit`/`reply_audit`; MQ names `GW.<CP>.SEND.AUDIT` / `GW.<CP>.REPLY.AUDIT`.
- **The one-UOW invariant is load-bearing:** outbound message and its audit copy share one unit of work with the same MsgId (streaming `MUSTDUP` primary; two-put fallback). The reconciliation key is MsgId↔CorrelId (the counterparty echoes the request MsgId as the reply CorrelId).

---

## Task 1 (issue #52): Rename `archive` → `AUDIT` + remove the stale CLAUDE.md rule

**Why first:** the live board screenshot in #49 must show `AUDIT` naming, and the document must match the code.

**Files (anchor + driven):**
- Modify (anchor): `src/gwlab/config.py:25-39` — the `QueueSet` fields and name derivation.
- Modify (driven by mypy/tests): every consumer of `send_archive`/`reply_archive` — `src/gwlab/{reconcile,outbound,inbound,exporter,recovery,replay,cli,deploy,deploy_http,dayboundary,pymqi_messaging}.py`, `demo/{live_demo,scale_bench,full_day_sim}.py`, and their tests under `tests/`.
- Modify: `ansible/roles/gw-queues/defaults/main.yml`, `ansible/roles/gw-queues/tasks/main.yml` — MQSC object names and `STREAMQ(...)` targets.
- Modify: `src/gwlab/dashboard.py` — any queue label shown on the board (the `ibmmq_queue_*` target's `queue=` for the audit queue, if present).
- Modify: `CLAUDE.md` — delete the stale hard-rule bullet.
- Modify: prose/docstrings mentioning "archive" in the files above (keep the word only where it means a generic verb, not the queue).

**Interfaces:**
- Produces: `QueueSet(send, send_audit, reply, reply_audit)` with `for_counterparty("CPB") → GW.CPB.SEND / GW.CPB.SEND.AUDIT / GW.CPB.REPLY / GW.CPB.REPLY.AUDIT`.
- Consumed by: all of Task 2 and the document (Task 3).

- [ ] **Step 1: Update the `config.py` test first (TDD anchor)**

In `tests/test_config.py`, change the expected names and field references from `*_archive`/`.ARCHIVE` to `*_audit`/`.AUDIT`. Example expected block:

```python
def test_for_counterparty_derives_audit_names() -> None:
    qs = QueueSet.for_counterparty("CPB")
    assert qs.send == "GW.CPB.SEND"
    assert qs.send_audit == "GW.CPB.SEND.AUDIT"
    assert qs.reply == "GW.CPB.REPLY"
    assert qs.reply_audit == "GW.CPB.REPLY.AUDIT"
```

- [ ] **Step 2: Run the config test to verify it fails**

Run: `vrg-container-run -- vrg-validate` (or, for a fast local loop, `pytest tests/test_config.py -q`).
Expected: FAIL — `QueueSet` has no attribute `send_audit`.

- [ ] **Step 3: Rename the fields and derivation in `config.py`**

```python
    send: str
    send_audit: str
    reply: str
    reply_audit: str

    @classmethod
    def for_counterparty(cls, counterparty: str) -> QueueSet:
        """Derive the queue set for a counterparty tag (e.g. ``"CPB"``)."""
        base = f"GW.{counterparty}"
        return cls(
            send=f"{base}.SEND",
            send_audit=f"{base}.SEND.AUDIT",
            reply=f"{base}.REPLY",
            reply_audit=f"{base}.REPLY.AUDIT",
        )
```

Also update the `QueueSet` docstring: `send_audit` = the outbound audit (replay copy); `reply_audit` = the inbound audit (authoritative "processed" ledger).

- [ ] **Step 4: Drive the rename through every consumer**

Find every remaining reference and rename `send_archive`→`send_audit`, `reply_archive`→`reply_audit`, and the string `.ARCHIVE`→`.AUDIT`:

```bash
cd /Users/pmoore/dev/projects/logical-minds-foundry/mq-gateway-replay-lab/.worktrees/issue-49-confirmation-recommendation
grep -rln "send_archive\|reply_archive\|\.ARCHIVE\|_archive\b" src tests demo ansible
```

Update each hit. In `ansible/roles/gw-queues/*`, rename the MQSC object names and the `STREAMQ(<name>)` targets to the `.AUDIT` names. In prose/docstrings, replace "archive queue"→"audit queue" (leave the generic English verb "archive" only where it isn't the queue).

- [ ] **Step 5: Remove the stale `CLAUDE.md` hard rule**

Delete this bullet from `CLAUDE.md` (it contradicts the real streaming-queue design the lab implements and proves):

```
- **MQ-native & minimalist.** The two-put-under-syncpoint is the mechanism;
  streaming queues are deliberately not used; AMS is out. ...
```

Replace it with a corrected bullet:

```
- **MQ-native & minimalist.** The queue-manager-native audit copy is the
  mechanism: a streaming queue (`STREAMQ … STRMQOS(MUSTDUP)`) writes the copy
  atomically, same MsgId, zero application logic; an explicit two-put-in-one-UOW
  is the documented fallback where streaming is unavailable. AMS is out. No new
  vendor products.
```

Also fix the "MQ-native archive" phrasing in the "Project purpose" section → "MQ-native audit copy".

- [ ] **Step 6: Validate**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (all tests green, types clean, lint clean). If any consumer was missed, mypy/tests name the file — fix and re-run.

- [ ] **Step 7: Commit**

```bash
vrg-commit --type refactor --scope naming --message "adopt AUDIT queue naming (IBM convention); drop stale CLAUDE.md rule" --body "Rename archive -> AUDIT across QueueSet, consumers, MQSC, dashboard, and docs so code, board, and the recommendation document are consistent with IBM's streaming-queue vocabulary. Remove the stale hard rule that said streaming queues are not used, which contradicted the real STREAMQ/MUSTDUP design. Closes #52.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2a (issue #49): Algorithm 3 — unexpected-confirmation detection in `reconcile.py`

**Files:**
- Modify: `src/gwlab/reconcile.py` (add dataclass + constants; extend `report`; add `unexpected_detail` view; add a field to `DailyMetrics` and `DailyReport`).
- Test: `tests/test_reconcile.py`.

**Interfaces:**
- Produces: `UnexpectedConfirmation(correl_id: bytes, arrived_at: datetime, reason: str)`; `REASON_ORPHAN = "orphan"`, `REASON_DUPLICATE = "duplicate"`; `DailyReport.unexpected: list[UnexpectedConfirmation]`; `DailyMetrics.unexpected: int`; `unexpected_detail(messaging, queues, now) -> list[UnexpectedConfirmation]`.
- Consumed by: Task 2b (exporter), Task 2c (dashboard), Task 2d (simulation test asserts the count).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_reconcile.py` (reuse the `_send_at` / `_reply_at` helpers and `NOW`):

```python
from gwlab.reconcile import (
    REASON_DUPLICATE,
    REASON_ORPHAN,
    UnexpectedConfirmation,
    report,
    unexpected_detail,
)


def test_orphan_reply_is_flagged_unexpected() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"1", NOW - timedelta(minutes=5))
    _reply_at(fm, b"1", NOW - timedelta(minutes=4))      # legit confirmation
    _reply_at(fm, b"999", NOW - timedelta(minutes=1))    # orphan: no sent MsgId 999
    result = unexpected_detail(fm, QS, NOW)
    assert [(u.correl_id, u.reason) for u in result] == [(b"999", REASON_ORPHAN)]


def test_duplicate_confirmation_is_flagged_unexpected() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"1", NOW - timedelta(minutes=5))
    _reply_at(fm, b"1", NOW - timedelta(minutes=4))
    _reply_at(fm, b"1", NOW - timedelta(minutes=2))      # duplicate confirmation for 1
    result = unexpected_detail(fm, QS, NOW)
    assert [(u.correl_id, u.reason) for u in result] == [(b"1", REASON_DUPLICATE)]


def test_report_counts_unexpected_and_leaves_unconfirmed_intact() -> None:
    fm = FakeMessaging()
    _send_at(fm, b"1", NOW - timedelta(minutes=5))       # confirmed
    _send_at(fm, b"2", NOW - timedelta(minutes=5))       # unconfirmed
    _reply_at(fm, b"1", NOW - timedelta(minutes=4))
    _reply_at(fm, b"777", NOW - timedelta(minutes=1))    # orphan
    r = report(fm, QS, NOW)
    assert r.metrics.unconfirmed == 1
    assert r.metrics.unexpected == 1
    assert [u.msg_id for u in r.unconfirmed] == [b"2"]
    assert [u.reason for u in r.unexpected] == [REASON_ORPHAN]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_reconcile.py -q`
Expected: FAIL — `cannot import name 'UnexpectedConfirmation'`.

- [ ] **Step 3: Add the dataclass, constants, and view to `reconcile.py`**

Add near the other constants/dataclasses:

```python
REASON_ORPHAN = "orphan"
REASON_DUPLICATE = "duplicate"


@dataclass(frozen=True)
class UnexpectedConfirmation:
    """A confirmation that should never occur.

    Unlike a *missing* confirmation — a benign glitch (a message lost in
    transit) — an unexpected confirmation is a genuine correctness fault:
    an ``orphan`` reply whose CorrelId matches no sent MsgId, or a
    ``duplicate`` confirmation for a MsgId already confirmed today.
    """

    correl_id: bytes
    arrived_at: datetime
    reason: str
```

Add `unexpected: int` to `DailyMetrics` (after `scanned_headers`) and `unexpected: list[UnexpectedConfirmation]` to `DailyReport` (after `unconfirmed`).

- [ ] **Step 4: Detect anomalies inside `report`**

In `report`, after `reply_time_by_key` is built, add:

```python
    sent_ids = {message.msg_id for message, _ in sent}
    seen: set[bytes] = set()
    unexpected: list[UnexpectedConfirmation] = []
    for message, put_time in replies:
        correl = message.correl_id
        if correl not in sent_ids:
            unexpected.append(UnexpectedConfirmation(correl, put_time, REASON_ORPHAN))
        elif correl in seen:
            unexpected.append(UnexpectedConfirmation(correl, put_time, REASON_DUPLICATE))
        seen.add(correl)
```

Set `unexpected=len(unexpected)` in the `DailyMetrics(...)` constructor, and `unexpected=unexpected` in the `DailyReport(...)` return. Add the view function:

```python
def unexpected_detail(
    messaging: Messaging, queues: QueueSet, now: datetime
) -> list[UnexpectedConfirmation]:
    """Return today's unexpected confirmations (orphan / duplicate replies)."""
    return report(messaging, queues, now).unexpected
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_reconcile.py -q`
Expected: PASS. If other tests construct `DailyMetrics(...)` positionally, add `unexpected=0` — grep `DailyMetrics(` in `tests/` and fix.

- [ ] **Step 6: Validate + commit**

Run: `vrg-container-run -- vrg-validate` → PASS.

```bash
vrg-commit --type feat --scope reconcile --message "detect unexpected confirmations (orphan / duplicate replies)" --body "Algorithm 3: the mirror of the unconfirmed-set diff. An orphan reply (CorrelId matching no sent MsgId) or a duplicate confirmation is flagged as a should-never-happen exception, distinct from a benign missing confirmation. Part of #49.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2b (issue #49): Expose the anomaly on the metrics feed (`exporter.py`)

**Files:**
- Modify: `src/gwlab/exporter.py`.
- Test: `tests/test_exporter.py`.

**Interfaces:**
- Produces Prometheus series `gwlab_unexpected_confirmations{counterparty}` (gauge = count) and `gwlab_unexpected_confirmation{counterparty,correlid,arrived,reason}` (value = 1 per anomaly).
- Consumed by: Task 2c (the dashboard binds to `gwlab_unexpected_confirmations`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_exporter.py` (mirror the existing render test setup — a `FakeMessaging` with a sent+confirmed pair plus one orphan reply):

```python
def test_render_exposes_unexpected_confirmations() -> None:
    fm = FakeMessaging()
    # one confirmed, one orphan reply (CorrelId 999 was never sent)
    fm.put(QS.send_audit, Message(b"t", b"1", put_time=NOW - timedelta(minutes=5))); fm.commit()
    fm.put(QS.reply_audit, Message(b"r", b"R:1", correl_id=b"1", put_time=NOW - timedelta(minutes=4))); fm.commit()
    fm.put(QS.reply_audit, Message(b"r", b"R:999", correl_id=b"999", put_time=NOW - timedelta(minutes=1))); fm.commit()
    text = render(fm, QS, "CPB", NOW)
    assert 'gwlab_unexpected_confirmations{counterparty="CPB"} 1' in text
    assert 'gwlab_unexpected_confirmation{' in text
    assert 'reason="orphan"' in text
```

(Match the imports/`QS`/`NOW` already used in `tests/test_exporter.py`; add `timedelta` if needed.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_exporter.py -q`
Expected: FAIL — the metric is absent.

- [ ] **Step 3: Render the new series**

In `exporter.py`: add to `_GAUGE_HELP` the entry `"gwlab_unexpected_confirmations": "Unexpected confirmations today: replies matching no sent message, or duplicates."`. Add `("gwlab_unexpected_confirmations", daily.unexpected)` to the `gauges` tuple. Add a per-message metric block after the unconfirmed loop:

```python
    _UNEXPECTED_METRIC = "gwlab_unexpected_confirmation"  # define near _MESSAGE_METRIC
    ...
    lines.append(f"# HELP {_UNEXPECTED_METRIC} An unexpected confirmation; labels identify the reply and why.")
    lines.append(f"# TYPE {_UNEXPECTED_METRIC} gauge")
    for item in daily_report.unexpected:
        arrived = item.arrived_at.strftime("%H:%M:%S")
        correlid = item.correl_id.hex()
        labels = f'counterparty="{counterparty}",correlid="{correlid}",arrived="{arrived}",reason="{item.reason}"'
        lines.append(f"{_UNEXPECTED_METRIC}{{{labels}}} 1")
```

Define `_UNEXPECTED_METRIC` at module scope alongside `_MESSAGE_METRIC`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_exporter.py -q` → PASS.

- [ ] **Step 5: Validate + commit**

Run: `vrg-container-run -- vrg-validate` → PASS.

```bash
vrg-commit --type feat --scope exporter --message "expose unexpected-confirmation metrics" --body "Add gwlab_unexpected_confirmations (count) and per-anomaly gwlab_unexpected_confirmation series so the board can surface orphan/duplicate replies. Part of #49.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2c (issue #49): Surface the anomaly on the Grafana board (`dashboard.py`)

**Files:**
- Modify: `src/gwlab/dashboard.py` (add a KPI stat tile bound to `gwlab_unexpected_confirmations`; bump `_SCHEMA_VERSION`).
- Test: `tests/test_dashboard.py`.

**Interfaces:**
- Consumes: `gwlab_unexpected_confirmations{qmgr,counterparty}` via the existing `_gwlab(metric, qmgr, counterparty)` helper.

- [ ] **Step 1: Read the existing KPI-tile pattern**

Read `src/gwlab/dashboard.py` (the KPI tiles: sent/confirmed/unconfirmed/mean-latency) and the matching assertions in `tests/test_dashboard.py`. Follow that exact pattern — do not invent a new panel style.

- [ ] **Step 2: Write the failing test**

Add a test mirroring the existing KPI-tile assertions, expecting a panel whose target expression is `_gwlab("unexpected_confirmations", qmgr, counterparty)` and whose title is "Unexpected confirmations". Assert the board JSON contains that expr and title. (Copy the shape of the existing "unconfirmed" tile test.)

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/test_dashboard.py -q`
Expected: FAIL — the panel/expr is absent.

- [ ] **Step 4: Add the tile**

Add an "Unexpected confirmations" stat tile next to the existing KPI tiles, bound to `_gwlab("unexpected_confirmations", qmgr, counterparty)`, styled like the `unconfirmed` tile but with a red threshold at `>= 1` (any anomaly is notable). Bump `_SCHEMA_VERSION` by 1.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_dashboard.py -q` → PASS.

- [ ] **Step 6: Validate + commit**

Run: `vrg-container-run -- vrg-validate` → PASS.

```bash
vrg-commit --type feat --scope dashboard --message "add unexpected-confirmations KPI tile" --body "Surface Algorithm 3's should-never-happen exception on the board with a red-thresholded stat tile. Part of #49.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2d (issue #49): The last-hour scenario (`simulation.py`)

**Files:**
- Modify: `src/gwlab/simulation.py` (add `LAST_HOUR` phases and a `run_last_hour` orchestration that leaves a few unconfirmed and injects one anomaly).
- Test: `tests/test_simulation.py`.

**Design decision (explicit, not deferred):** model the last hour with the existing primitives:
1. `steady` (confirm) — healthy flow.
2. `slowdown` (confirm) — same behavior; the live harness spreads sends over wall-clock so confirm latency visibly rises (offline just advances the clock).
3. `block` (no drain at all) — requests are sent and audited but neither consumed nor confirmed, so the unconfirmed backlog climbs.
4. `recovery` (confirm) — drains and confirms the whole backlog, so the board shows the backlog draining.
5. `tail` — send `leave_unconfirmed` (default 3) final trades that are audited but never confirmed: the end-of-day outstanding set.
6. Inject **one** orphan reply (a confirmation whose CorrelId matches no sent MsgId).

**Interfaces:**
- Produces: `LAST_HOUR: tuple[Phase, ...]`; `run_last_hour(messaging, queues, responder, *, after_send=None, after_phase=None, leave_unconfirmed=3, inject_anomaly=True) -> int` (returns trades sent).
- Consumed by: `demo/last_hour_sim.py` (Task 2e).

- [ ] **Step 1: Write the failing offline test**

Add to `tests/test_simulation.py` (follow the existing offline `SimClock`+`FakeMessaging`+`Responder` pattern already used for `TROUBLED_DAY`):

```python
def test_last_hour_leaves_a_few_unconfirmed_and_one_anomaly() -> None:
    clock = SimClock(datetime(2026, 7, 8, 15, 30, 0))  # noqa: DTZ001
    fm = FakeMessaging(clock)
    fm.configure_stream(QS.send, QS.send_audit)
    fm.configure_stream(QS.reply, QS.reply_audit)
    responder = Responder(fm, QS)
    run_last_hour(
        fm, QS, responder,
        after_send=lambda _p: clock.advance(timedelta(seconds=5)),
        after_phase=lambda _p: clock.advance(timedelta(minutes=1)),
        leave_unconfirmed=3,
        inject_anomaly=True,
    )
    r = report(fm, QS, clock())
    assert r.metrics.unconfirmed == 3
    assert r.metrics.unexpected == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_simulation.py -q`
Expected: FAIL — `cannot import name 'run_last_hour'`.

- [ ] **Step 3: Implement `LAST_HOUR` and `run_last_hour`**

```python
LAST_HOUR: tuple[Phase, ...] = (
    Phase("steady", 20, confirm=True),
    Phase("slowdown", 16, confirm=True),
    Phase("block", 10, confirm=False),
    Phase("recovery", 12, confirm=True),
)


def _inject_orphan_reply(messaging: Messaging, queues: QueueSet) -> None:
    """A confirmation whose CorrelId matches no sent MsgId — the should-never
    -happen anomaly. Put on the reply queue; the stream audits it on arrival."""
    orphan = Message(body=b"orphan", msg_id=b"R:orphan", correl_id=b"\xffNOSENT")
    messaging.put(queues.reply, orphan)
    messaging.commit()


def run_last_hour(
    messaging: Messaging,
    queues: QueueSet,
    responder: Responder,
    *,
    after_send: Callable[[Phase], None] | None = None,
    after_phase: Callable[[Phase], None] | None = None,
    leave_unconfirmed: int = 3,
    inject_anomaly: bool = True,
) -> int:
    """The last hour of a trading day: steady, slowdown, a block, recovery, a
    small end-of-day unconfirmed tail, and one injected anomaly. During ``block``
    requests are sent and audited but not consumed, so the backlog climbs; the
    ``recovery`` phase drains and confirms it. The tail sends ``leave_unconfirmed``
    trades that are audited but never confirmed.
    """
    seq = run_day(
        messaging, queues, responder, LAST_HOUR,
        after_send=after_send, after_phase=after_phase,
    )
    for _ in range(leave_unconfirmed):
        outbound.send(messaging, queues, f"trade-{seq:06d}".encode(), _msgid(seq))
        seq += 1
        if after_send is not None:
            after_send(LAST_HOUR[-1])
    if inject_anomaly:
        _inject_orphan_reply(messaging, queues)
    return seq
```

Note: `run_day`'s `block` phase uses `_drain_outage`, which currently *consumes* the send queue without confirming — that permanently strands the backlog. For this scenario the backlog must survive into `recovery` to be confirmed. **Change `run_last_hour` to not use `_drain_outage`:** run the `block` phase with a no-op drain (send only), then let `recovery`'s `_drain_confirmed` process both the block backlog and the recovery trades. Implement this by iterating `LAST_HOUR` locally in `run_last_hour` instead of delegating to `run_day` for the `block` phase — send during `block`, skip its drain; drain+confirm on `recovery`. Keep `run_day`/`TROUBLED_DAY` untouched (the existing test still passes). Import `Message` at module scope for `_inject_orphan_reply`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_simulation.py -q` → PASS (both the existing `TROUBLED_DAY` test and the new one).

- [ ] **Step 5: Validate + commit**

Run: `vrg-container-run -- vrg-validate` → PASS.

```bash
vrg-commit --type feat --scope simulation --message "add last-hour scenario with partial recovery and one anomaly" --body "Model the last hour of a trading day: steady, slowdown, a ~10-minute block whose backlog recovers, a small end-of-day unconfirmed tail, and one injected orphan reply for Algorithm 3. Part of #49.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2e (issue #49): Live run harness + capture the screenshot

**Files:**
- Create: `demo/last_hour_sim.py` (mirror `demo/full_day_sim.py`, calling `run_last_hour`, with wall-clock spreads that make the degradation curve visible).

**This task is a live operation — there is no unit test for a screenshot.** Verification is the captured board.

- [ ] **Step 1: Write `demo/last_hour_sim.py`**

Copy `demo/full_day_sim.py`; replace `TROUBLED_DAY`/`run_day` with `LAST_HOUR`/`run_last_hour`; set `_PHASE_SECONDS = {"steady": 300, "slowdown": 420, "block": 600, "recovery": 360}` (≈28 min wall-clock; the `block` is the 10-minute stoppage). Keep the same CLI args (`--qmgr`, `--conn`, `--channel`, `--counterparty`, TLS flags).

- [ ] **Step 2: Confirm preconditions on the box (declare-and-verify, never mutate the lab)**

Verify (do not create): `NHAUAPP` reachable over the app channel; the gateway's `GW.CPB.*` queues + `AUDIT` streams provisioned by `gw-queues`; the gwlab exporter serving and Prometheus scraping it; the board deployed by `gw-observability`. A failed precondition is a loud stop, not a silent fix.

- [ ] **Step 3: Run it live**

```bash
python demo/last_hour_sim.py --qmgr NHAUAPP --conn '10.50.0.11(1414)' --channel APP.SVRCONN --counterparty CPB --tls-cipher ANY_TLS13_OR_HIGHER --key-repo <stem> --cert-label <label>
```

- [ ] **Step 4: Capture the board screenshot**

At end of run, capture the Grafana board (last-hour window) showing: the confirm-latency degradation, the backlog climb-and-drain across the block, the KPI tiles, the which-trades unconfirmed table (the 2–3 tail), and the red **Unexpected confirmations** tile (=1). Save to `docs/images/last-hour-board.png`.

- [ ] **Step 5: Commit the screenshot**

```bash
vrg-git add docs/images/last-hour-board.png
vrg-commit --type docs --scope demo --message "capture last-hour end-of-day board snapshot" --body "Live run of demo/last_hour_sim.py against NHAUAPP over mutual TLS: the hero figure for the recommendation document. Closes #49.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3 (issue #53): Write the recommendation document

**Files:**
- Create: `docs/mq-native-confirmation-tracking.md`.

**No code.** The "tests" here are the constraint checklist in Step 3. Structure follows the spec (`docs/specs/2026-07-13-mq-native-confirmation-tracking-design.md`).

- [ ] **Step 1: Draft the document, section by section (Approach A)**

Write these sections, in order. Prose + algorithms + MQSC snippets only.

1. **Executive summary** — one paragraph: the ask (track confirmations + enable replay via MQ-native audit queues); close with the provenance claim (built and validated in a lab against real Native HA MQ over mutual TLS).
2. **The problem** — replay requires knowing *which* messages are unconfirmed; a hand-rolled log makes the gateway less reliable than the MQ estate it fronts (host loss human-gated; a replicated log with a failed write is not a usable replay source; no dedup; log-write correctness unspecified).
3. **The core insight** — let the queue manager write an audit copy of every message and every confirmation, one unit of work, same MsgId. Stop hand-rolling a log.
4. **The blueprint (MQSC)** — the only literal syntax allowed. Show the audit-queue objects and streaming config, e.g.:

   ```mqsc
   DEFINE QLOCAL('GW.CPB.SEND.AUDIT')  MAXFSIZE(DEFAULT) CAPEXPRY(24)
   ALTER  QLOCAL('GW.CPB.SEND')        STREAMQ('GW.CPB.SEND.AUDIT')  STRMQOS(MUSTDUP)
   DEFINE QLOCAL('GW.CPB.REPLY.AUDIT') MAXFSIZE(DEFAULT) CAPEXPRY(24)
   ALTER  QLOCAL('GW.CPB.REPLY')       STREAMQ('GW.CPB.REPLY.AUDIT') STRMQOS(MUSTDUP)
   ```

   State the invariants: MsgId preserved into the audit copy; the counterparty echoes the request MsgId as the reply CorrelId (the authoritative match); `MUSTDUP` = both-or-neither (no "sent but unaudited" state); `CAPEXPRY(24h)` makes the audit a self-clearing rolling trading day.
5. **Algorithm 1 — per-request response time** (numbered steps): browse both audit queues; index confirmations by CorrelId with their put-time; for each sent message, latency = confirmation put-time − request put-time; report the mean and distribution. Reference the MQMD put date/time fields explicitly.
6. **Algorithm 2 — reconciliation** (numbered steps): collect sent MsgIds and confirmation CorrelIds; the set difference (sent MsgIds with no matching CorrelId) is the unconfirmed set; report the count *and* the specific list (MsgId, sent-time, age); scope to the current trading day; note the scale refinement (local bindings + incremental high-water-mark).
7. **Algorithm 3 — unexpected confirmations** (numbered steps): the mirror diff — a confirmation CorrelId matching no sent MsgId (orphan), or a duplicate confirmation. Frame it: a *missing* confirmation is a benign glitch (a message lost in transit); an *unexpected* confirmation should never happen and is flagged as an exception.
8. **The collector** — describe conceptually what the client's engineers build (browse the two audit queues, run the three algorithms, expose the results); state plainly it was **prototyped and proven in this lab**. No language, no code.
9. **Evidence** — embed `docs/images/last-hour-board.png`; cite the measured end-of-day numbers (sent/confirmed/unconfirmed, mean confirm latency, the specific unconfirmed set, the anomaly caught).
10. **Scale** — the diff is ~0.5 µs/header (~1.1 s for a 1M-trade day); the client-mode browse round-trip (~640 µs/header, ~21 min for a full 1M-trade scan) is the bottleneck → run on the QM node over local bindings, reconcile incrementally; `CAPEXPRY` bounds the worst case.
11. **Questions for the application team** — forwarding, volumes, topology, recovery objective, data-at-rest, HA appetite.

- [ ] **Step 2: Weave the provenance spine**

Confirm the "built and validated in a lab" claim appears in the summary (claim), the collector section (proven implementation), and the evidence section (the data that earns it).

- [ ] **Step 3: Constraint checklist (the document's "tests")**

Verify every item; fix inline:
- [ ] No code blocks in any language — only prose, numbered algorithms, MQSC, and the screenshot.
- [ ] No client-identifiable content; all names generic (`GW.CPB.*`, `NHAUAPP`, Counterparty A/B).
- [ ] All three algorithms present and described as steps referencing header fields.
- [ ] MQSC snippets use the `AUDIT` names and match the blueprint invariants.
- [ ] The screenshot is embedded and the cited numbers match the run.
- [ ] Reads as one argument: makes the case, then hands over a build-ready blueprint.

- [ ] **Step 4: Commit**

```bash
vrg-git add docs/mq-native-confirmation-tracking.md docs/images/last-hour-board.png
vrg-commit --type docs --scope recommendation --message "MQ-native confirmation-tracking & replay recommendation (client-facing)" --body "The client-facing write-up: problem, the MQ-native audit-queue insight, the MQSC blueprint, the three algorithms (response time, reconciliation, unexpected confirmations), the collector, the last-hour board evidence, scale, and the questions for the application team. No code; prose + algorithms + MQSC + one screenshot. Provenance spine throughout. Closes #53.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review (spec coverage)

- **Spec §"Hard constraints" (no code / anonymization / AUDIT naming):** Task 1 (naming), Task 3 Step 3 (no-code + anonymization checklist). ✓
- **Spec §"Document structure" (11 sections, provenance spine):** Task 3 Steps 1–2. ✓
- **Spec §"Algorithm 1/2":** already implemented in `reconcile.report` (response time via put-times; unconfirmed set); documented in Task 3. ✓
- **Spec §"Algorithm 3":** Tasks 2a–2d (detect), Task 2c (board), Task 3 §7 (document). ✓
- **Spec §"last-hour scenario" (+ one anomaly):** Task 2d (offline), Task 2e (live). ✓
- **Spec §"Evidence" (screenshot + numbers):** Task 2e, Task 3 §9. ✓
- **Spec §"Scale":** documented in Task 3 §10 (measured numbers already exist). ✓
- **Spec §"Epic #49 task breakdown":** Tasks map 1:1 to issues #52/#49/#53. ✓

**Type consistency:** `send_audit`/`reply_audit` (Task 1) used consistently in Tasks 2a/2b/2d tests; `UnexpectedConfirmation(correl_id, arrived_at, reason)` and `DailyMetrics.unexpected`/`DailyReport.unexpected` defined in 2a and consumed unchanged in 2b/2c/2d. ✓
