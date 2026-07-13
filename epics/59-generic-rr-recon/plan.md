# Generic MQ request-reply reconciliation tool — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `rrecon` — a standalone, MQ-native, read-only tool that browses a request-archive queue and its reply-archive queue, matches request↔reply on a correlation id within a request-cohort window, and emits a small fixed set of Prometheus observability metrics.

**Architecture:** A pure, clock-injected core (`reconcile`) works over an abstract `ArchivedRecord` yielded by a `Browser` port. A `pymqi` adapter is the production `Browser`; an in-memory fake backs tests and the example. An exporter renders the reconciliation as Prometheus text; a cached HTTP server decouples scan cadence from scrape cadence. A CLI (`render`/`serve`/`dashboard`) and an order-fulfillment example complete it.

**Tech Stack:** Python 3.12, `pymqi` (lazy-imported, coverage-excluded), `argparse`, `http.server`, `pytest`, `ruff`, `mypy`, `uv`, vergil tooling.

**Note on repo:** This plan builds a **new repository** `mq-request-reply-recon`. Task 0 bootstraps it. All paths below are relative to that new repo's root. The spec is at `epics/59-generic-rr-recon/spec.md` in `mq-gateway-replay-lab`.

## Global Constraints

- **Python 3.12**; managed with `uv`; single validation entry point `vrg-container-run -- vrg-validate` (ruff + mypy + pytest). Do not run linters individually.
- **MQ-native, not broker-agnostic.** The abstraction stops at the internal `Browser` port; the product is MQ.
- **Read-only collector.** No `put`/`get`/`commit`. The MQ adapter opens queues `MQOO_BROWSE` only.
- **No silent failures.** A record with no `put_time` raises; a missing/unopenable archive queue fails loud; MQ errors propagate with context. No swallowed exceptions.
- **No silent caps.** The per-request series is capped at `--top-n` (default 200) and the omitted count is always emitted as `rr_outstanding_truncated`.
- **Naming:** package/CLI `rrecon`; metric prefix `rr_` (configurable via `--prefix`); per-stream label `stream`.
- **`pymqi` is lazy-imported** inside the adapter and **excluded from coverage** (needs MQ client libs; the `browse`-path is integration-marked).
- **Generic vocabulary only** — no finance terms in the tool or the example (the example domain is order fulfillment).

---

### Task 0: Repo bootstrap

**Files:**
- Create: `pyproject.toml`, `vergil.toml`, `README.md`, `LICENSE`, `.gitignore`, `VERSION`
- Create: `src/rrecon/__init__.py`
- Create: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: an importable `rrecon` package; a green `vrg-validate` baseline.

- [ ] **Step 1: Write the smoke test**

```python
# tests/test_smoke.py
import rrecon

def test_package_imports_and_has_version():
    assert isinstance(rrecon.__version__, str)
    assert rrecon.__version__
```

- [ ] **Step 2: Run it to verify it fails**

Run: `vrg-container-run -- vrg-validate`
Expected: FAIL — `ModuleNotFoundError: No module named 'rrecon'`.

- [ ] **Step 3: Create the package and project files**

```python
# src/rrecon/__init__.py
"""rrecon — MQ request-reply reconciliation observability."""
__version__ = "0.1.0"
```

`pyproject.toml` mirrors `mq-gateway-replay-lab`'s (hatchling/uv, `ruff`, `mypy`, `pytest`, `--cov=rrecon`), package name `rrecon`, `requires-python = ">=3.12"`, `[project.scripts] rrecon = "rrecon.cli:main"`. `vergil.toml`: `repository-type = "tooling"`, `primary-language = "python"`, `[ci] versions = ["3.12"]`, **no `[vm]` section**. Add `pymqi` coverage exclusion in `[tool.coverage.run] omit = ["src/rrecon/mq.py", "src/rrecon/exporter_server.py"]`.

- [ ] **Step 4: Run validation to verify it passes**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(bootstrap): rrecon package skeleton + validating baseline"
```

---

### Task 1: The record and the Browser port

**Files:**
- Create: `src/rrecon/records.py`
- Test: `tests/test_records.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ArchivedRecord(msg_id: bytes, correl_id: bytes, put_time: datetime | None, body: bytes)` — frozen dataclass.
  - `Browser` — `Protocol` with `browse(self, queue: str) -> list[ArchivedRecord]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_records.py
from datetime import UTC, datetime
from rrecon.records import ArchivedRecord

def test_archived_record_fields():
    t = datetime(2026, 1, 1, tzinfo=UTC)
    r = ArchivedRecord(msg_id=b"m1", correl_id=b"c1", put_time=t, body=b"payload")
    assert r.msg_id == b"m1"
    assert r.correl_id == b"c1"
    assert r.put_time == t
    assert r.body == b"payload"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_records.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'rrecon.records'`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/records.py
"""The minimal record the matcher needs, and the read-only Browser port."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class ArchivedRecord:
    """One archived message: identity fields, put-time, and body."""

    msg_id: bytes
    correl_id: bytes
    put_time: datetime | None
    body: bytes


class Browser(Protocol):
    """Non-destructive read of every record on a queue — the only port an adapter satisfies."""

    def browse(self, queue: str) -> list[ArchivedRecord]: ...
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_records.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/records.py tests/test_records.py
git commit -m "feat(records): ArchivedRecord + Browser port"
```

---

### Task 2: In-memory fake Browser

**Files:**
- Create: `src/rrecon/fake.py`
- Test: `tests/test_fake.py`

**Interfaces:**
- Consumes: `ArchivedRecord` (Task 1).
- Produces: `FakeBrowser` with `seed(queue: str, records: list[ArchivedRecord]) -> None` and `browse(queue: str) -> list[ArchivedRecord]` (satisfies `Browser`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_fake.py
from datetime import UTC, datetime
from rrecon.fake import FakeBrowser
from rrecon.records import ArchivedRecord

def _rec(mid):
    return ArchivedRecord(msg_id=mid, correl_id=b"", put_time=datetime(2026, 1, 1, tzinfo=UTC), body=b"")

def test_seed_and_browse_returns_records():
    b = FakeBrowser()
    b.seed("Q1", [_rec(b"a"), _rec(b"b")])
    assert [r.msg_id for r in b.browse("Q1")] == [b"a", b"b"]

def test_browse_unknown_queue_is_empty():
    assert FakeBrowser().browse("NOPE") == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_fake.py -v`
Expected: FAIL — no module `rrecon.fake`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/fake.py
"""In-memory Browser for tests and the example — no queue manager needed."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rrecon.records import ArchivedRecord


class FakeBrowser:
    """A seedable, read-only queue set."""

    def __init__(self) -> None:
        self._queues: dict[str, list[ArchivedRecord]] = {}

    def seed(self, queue: str, records: list[ArchivedRecord]) -> None:
        self._queues.setdefault(queue, []).extend(records)

    def browse(self, queue: str) -> list[ArchivedRecord]:
        return list(self._queues.get(queue, []))
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_fake.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/fake.py tests/test_fake.py
git commit -m "feat(fake): in-memory Browser for tests and examples"
```

---

### Task 3: Config surface

**Files:**
- Create: `src/rrecon/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ArchivePair(request_archive: str, reply_archive: str)`
  - `Correlation(request_field: str = "msg_id", reply_field: str = "correl_id")` with `def request_key(r) -> bytes` and `def reply_key(r) -> bytes` (each `getattr(r, field)`)
  - `Window(start: datetime | None = None, end: datetime | None = None)` with `def contains(self, t: datetime) -> bool` (half-open `[start, end)`; open ends unbounded)
  - `ReconConfig(pair, stream="default", correlation=Correlation(), window=Window(), thresholds: list[tuple[str, int]] | None = None, prefix="rr_")`
  - `TLSConfig(cipher: str, key_repo: str, cert_label: str | None = None)`
  - `MQConnection(qmgr: str, channel: str, host: str, port: int = 1414, tls: TLSConfig | None = None)` with `def conn_name(self) -> str` returning `"{host}({port})"`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from datetime import UTC, datetime
from rrecon.config import ArchivePair, Correlation, MQConnection, ReconConfig, Window
from rrecon.records import ArchivedRecord

def _rec():
    return ArchivedRecord(msg_id=b"m", correl_id=b"c", put_time=None, body=b"")

def test_correlation_default_echo_keys():
    corr = Correlation()
    assert corr.request_key(_rec()) == b"m"
    assert corr.reply_key(_rec()) == b"c"

def test_correlation_reverse_mapping():
    corr = Correlation(request_field="correl_id", reply_field="msg_id")
    assert corr.request_key(_rec()) == b"c"
    assert corr.reply_key(_rec()) == b"m"

def test_window_open_contains_everything():
    assert Window().contains(datetime(2026, 1, 1, tzinfo=UTC))

def test_window_half_open_boundaries():
    w = Window(start=datetime(2026, 1, 1, 10, tzinfo=UTC), end=datetime(2026, 1, 1, 11, tzinfo=UTC))
    assert w.contains(datetime(2026, 1, 1, 10, tzinfo=UTC))       # start inclusive
    assert not w.contains(datetime(2026, 1, 1, 11, tzinfo=UTC))   # end exclusive
    assert not w.contains(datetime(2026, 1, 1, 9, tzinfo=UTC))

def test_reconconfig_defaults():
    cfg = ReconConfig(pair=ArchivePair("REQ", "RPY"))
    assert cfg.stream == "default"
    assert cfg.prefix == "rr_"
    assert cfg.thresholds is None

def test_mqconnection_conn_name():
    assert MQConnection("QM1", "APP.SVRCONN", "mq.example").conn_name() == "mq.example(1414)"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — no module `rrecon.config`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/config.py
"""Queue names and run configuration — the tool's driving surface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from rrecon.records import ArchivedRecord


@dataclass(frozen=True)
class ArchivePair:
    request_archive: str
    reply_archive: str


@dataclass(frozen=True)
class Correlation:
    """How a reply is linked to its request; defaults to the canonical MQ echo."""

    request_field: str = "msg_id"
    reply_field: str = "correl_id"

    def request_key(self, record: ArchivedRecord) -> bytes:
        return getattr(record, self.request_field)

    def reply_key(self, record: ArchivedRecord) -> bytes:
        return getattr(record, self.reply_field)


@dataclass(frozen=True)
class Window:
    """A half-open [start, end) request-cohort selector; open ends are unbounded."""

    start: datetime | None = None
    end: datetime | None = None

    def contains(self, when: datetime) -> bool:
        if self.start is not None and when < self.start:
            return False
        return not (self.end is not None and when >= self.end)


@dataclass(frozen=True)
class ReconConfig:
    pair: ArchivePair
    stream: str = "default"
    correlation: Correlation = field(default_factory=Correlation)
    window: Window = field(default_factory=Window)
    thresholds: list[tuple[str, int]] | None = None
    prefix: str = "rr_"


@dataclass(frozen=True)
class TLSConfig:
    cipher: str
    key_repo: str
    cert_label: str | None = None


@dataclass(frozen=True)
class MQConnection:
    qmgr: str
    channel: str
    host: str
    port: int = 1414
    tls: TLSConfig | None = None

    def conn_name(self) -> str:
        return f"{self.host}({self.port})"
```

- [ ] **Step 4: Run it to verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/config.py tests/test_config.py
git commit -m "feat(config): ArchivePair, Correlation, Window, ReconConfig, MQConnection"
```

---

### Task 4: The reconcile core

**Files:**
- Create: `src/rrecon/core.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `Browser` (Task 1), `ArchivePair`/`Correlation`/`Window` (Task 3).
- Produces:
  - `Outstanding(msg_id: bytes, put_time: datetime, age: timedelta)`
  - `Reconciliation(requests: int, replied: int, outstanding: int, mean_latency_seconds: float, scanned_headers: int, outstanding_detail: list[Outstanding])`
  - `reconcile(browser: Browser, pair: ArchivePair, correlation: Correlation, window: Window, now: datetime) -> Reconciliation`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_core.py
from datetime import UTC, datetime, timedelta
import pytest
from rrecon.config import ArchivePair, Correlation, Window
from rrecon.core import reconcile
from rrecon.fake import FakeBrowser
from rrecon.records import ArchivedRecord

PAIR = ArchivePair("REQ", "RPY")

def _req(mid, minute):
    return ArchivedRecord(msg_id=mid, correl_id=b"", put_time=datetime(2026, 1, 1, 10, minute, tzinfo=UTC), body=b"")

def _rpy(cid, minute):
    return ArchivedRecord(msg_id=b"r" + cid, correl_id=cid, put_time=datetime(2026, 1, 1, 10, minute, tzinfo=UTC), body=b"")

def _browser(reqs, rpys):
    b = FakeBrowser()
    b.seed("REQ", reqs)
    b.seed("RPY", rpys)
    return b

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)

def test_counts_and_scanned_headers():
    b = _browser([_req(b"a", 0), _req(b"b", 1)], [_rpy(b"a", 5)])
    r = reconcile(b, PAIR, Correlation(), Window(), NOW)
    assert (r.requests, r.replied, r.outstanding) == (2, 1, 1)
    assert r.scanned_headers == 3  # 2 requests + 1 reply, pre-filter
    assert [o.msg_id for o in r.outstanding_detail] == [b"b"]

def test_mean_latency_earliest_reply_wins():
    b = _browser([_req(b"a", 0)], [_rpy(b"a", 10), _rpy(b"a", 4)])  # 10 min and 4 min replies
    r = reconcile(b, PAIR, Correlation(), Window(), NOW)
    assert r.mean_latency_seconds == 240.0  # earliest reply: 4 minutes

def test_outstanding_age_from_now():
    b = _browser([_req(b"a", 0)], [])
    r = reconcile(b, PAIR, Correlation(), Window(), NOW)
    assert r.outstanding_detail[0].age == timedelta(hours=2)

def test_window_is_request_cohort_no_straddle_miscount():
    # request 09:59 in-window; reply 10:01 out-of-window-end but still matched.
    req = ArchivedRecord(b"a", b"", datetime(2026, 1, 1, 9, 59, tzinfo=UTC), b"")
    rpy = ArchivedRecord(b"ra", b"a", datetime(2026, 1, 1, 10, 1, tzinfo=UTC), b"")
    b = _browser([req], [rpy])
    w = Window(start=datetime(2026, 1, 1, 9, tzinfo=UTC), end=datetime(2026, 1, 1, 10, tzinfo=UTC))
    r = reconcile(b, PAIR, Correlation(), w, NOW)
    assert (r.requests, r.replied, r.outstanding) == (1, 1, 0)

def test_future_reply_ignored():
    b = _browser([_req(b"a", 0)], [_rpy(b"a", 5)])
    early = datetime(2026, 1, 1, 10, 2, tzinfo=UTC)  # before the 10:05 reply
    r = reconcile(b, PAIR, Correlation(), Window(), early)
    assert (r.replied, r.outstanding) == (0, 1)

def test_missing_put_time_raises():
    bad = ArchivedRecord(b"a", b"", None, b"")
    b = _browser([bad], [])
    with pytest.raises(ValueError, match="put_time"):
        reconcile(b, PAIR, Correlation(), Window(), NOW)

def test_empty_archives():
    r = reconcile(_browser([], []), PAIR, Correlation(), Window(), NOW)
    assert (r.requests, r.replied, r.outstanding, r.mean_latency_seconds) == (0, 0, 0, 0.0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_core.py -v`
Expected: FAIL — no module `rrecon.core`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/core.py
"""The reconcile core: browse both archives, match on correlation, derive metrics.

Pure and clock-injected. The window selects the request cohort; replies are
matched from the full browsed set bounded by ``now`` (no future replies). A
record with no put_time is a data-integrity fault and raises.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from rrecon.config import ArchivePair, Correlation, Window
    from rrecon.records import ArchivedRecord, Browser


@dataclass(frozen=True)
class Outstanding:
    msg_id: bytes
    put_time: datetime
    age: timedelta


@dataclass(frozen=True)
class Reconciliation:
    requests: int
    replied: int
    outstanding: int
    mean_latency_seconds: float
    scanned_headers: int
    outstanding_detail: list[Outstanding]


def _put_time(record: ArchivedRecord) -> datetime:
    if record.put_time is None:
        msg = f"archived record {record.msg_id!r} has no put_time; cannot date-scope it"
        raise ValueError(msg)
    return record.put_time


def reconcile(
    browser: Browser,
    pair: ArchivePair,
    correlation: Correlation,
    window: Window,
    now: datetime,
) -> Reconciliation:
    requests = browser.browse(pair.request_archive)
    replies = browser.browse(pair.reply_archive)
    scanned_headers = len(requests) + len(replies)

    # Earliest reply put_time per key, bounded by now (no future replies).
    earliest_reply: dict[bytes, datetime] = {}
    for reply in replies:
        rtime = _put_time(reply)
        if rtime > now:
            continue
        key = correlation.reply_key(reply)
        if key not in earliest_reply or rtime < earliest_reply[key]:
            earliest_reply[key] = rtime

    cohort = [r for r in requests if window.contains(_put_time(r))]

    detail: list[Outstanding] = []
    latencies: list[float] = []
    for request in cohort:
        rtime = _put_time(request)
        key = correlation.request_key(request)
        reply_time = earliest_reply.get(key)
        if reply_time is None:
            detail.append(Outstanding(request.msg_id, rtime, now - rtime))
        else:
            latencies.append((reply_time - rtime).total_seconds())

    mean_latency = sum(latencies) / len(latencies) if latencies else 0.0
    return Reconciliation(
        requests=len(cohort),
        replied=len(latencies),
        outstanding=len(detail),
        mean_latency_seconds=mean_latency,
        scanned_headers=scanned_headers,
        outstanding_detail=detail,
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_core.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/core.py tests/test_core.py
git commit -m "feat(core): request-cohort reconcile — counts, latency, outstanding detail"
```

---

### Task 5: Prometheus exporter

**Files:**
- Create: `src/rrecon/exporter.py`
- Test: `tests/test_exporter.py`

**Interfaces:**
- Consumes: `reconcile`/`Reconciliation` (Task 4), `ReconConfig` (Task 3), `Browser` (Task 1).
- Produces: `render(browser: Browser, config: ReconConfig, now: datetime, timer: Callable[[], float] = perf_counter, top_n: int = 200) -> str`.

**Behavior:** six gauges (`{prefix}requests|replied|outstanding|latency_seconds|scanned_headers|scan_seconds`) labelled `stream`; per-request `{prefix}outstanding_message{stream,msgid,sent}` = age seconds, capped at `top_n` oldest, with `{prefix}outstanding_truncated{stream}` = omitted count; `sent` label is ISO-8601 (`put_time.isoformat()`); `msgid` is hex. If `config.thresholds` is set: per-request series gains a `state` label (bucketed by descending min-age) and `{prefix}outstanding_aged{stream}` counts the top bucket. `scan_seconds` measured around the `reconcile` call via `timer`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_exporter.py
from datetime import UTC, datetime
from rrecon.config import ArchivePair, ReconConfig
from rrecon.exporter import render
from rrecon.fake import FakeBrowser
from rrecon.records import ArchivedRecord

PAIR = ArchivePair("REQ", "RPY")
NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)

def _req(mid, minute):
    return ArchivedRecord(mid, b"", datetime(2026, 1, 1, 10, minute, tzinfo=UTC), b"")

def _browser(reqs, rpys):
    b = FakeBrowser(); b.seed("REQ", reqs); b.seed("RPY", rpys); return b

def test_gauges_present_with_stream_label():
    b = _browser([_req(b"a", 0)], [])
    out = render(b, ReconConfig(pair=PAIR, stream="orders"), NOW, timer=lambda: 0.0)
    assert 'rr_requests{stream="orders"} 1' in out
    assert 'rr_outstanding{stream="orders"} 1' in out
    assert "rr_scan_seconds" in out

def test_per_request_series_iso_sent_and_hex_msgid():
    b = _browser([_req(b"a", 0)], [])
    out = render(b, ReconConfig(pair=PAIR, stream="orders"), NOW, timer=lambda: 0.0)
    assert 'rr_outstanding_message{stream="orders",msgid="61",sent="2026-01-01T10:00:00+00:00"} 7200' in out

def test_top_n_truncation_counter():
    reqs = [_req(bytes([i]), i) for i in range(5)]
    b = _browser(reqs, [])
    out = render(b, ReconConfig(pair=PAIR, stream="orders"), NOW, timer=lambda: 0.0, top_n=2)
    assert out.count("rr_outstanding_message{") == 2
    assert 'rr_outstanding_truncated{stream="orders"} 3' in out

def test_thresholds_add_state_label_and_aged_gauge():
    b = _browser([_req(b"a", 0)], [])  # age 2h = 7200s
    cfg = ReconConfig(pair=PAIR, stream="orders", thresholds=[("within-sla", 0), ("aged", 1800)])
    out = render(b, cfg, NOW, timer=lambda: 0.0)
    assert 'state="aged"' in out
    assert 'rr_outstanding_aged{stream="orders"} 1' in out

def test_no_thresholds_no_state_or_aged():
    b = _browser([_req(b"a", 0)], [])
    out = render(b, ReconConfig(pair=PAIR, stream="orders"), NOW, timer=lambda: 0.0)
    assert "state=" not in out
    assert "rr_outstanding_aged" not in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_exporter.py -v`
Expected: FAIL — no module `rrecon.exporter`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/exporter.py
"""Prometheus text exposition of the reconciliation metrics."""
from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from rrecon.core import reconcile

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from rrecon.config import ReconConfig
    from rrecon.core import Outstanding
    from rrecon.records import Browser


def _state_for(age_seconds: float, thresholds: list[tuple[str, int]]) -> str:
    for name, floor in sorted(thresholds, key=lambda t: t[1], reverse=True):
        if age_seconds >= floor:
            return name
    return thresholds[0][0]


def render(
    browser: Browser,
    config: ReconConfig,
    now: datetime,
    timer: Callable[[], float] = perf_counter,
    top_n: int = 200,
) -> str:
    p = config.prefix
    s = config.stream
    start = timer()
    r = reconcile(browser, config.pair, config.correlation, config.window, now)
    scan_seconds = timer() - start

    gauges = [
        (f"{p}requests", r.requests),
        (f"{p}replied", r.replied),
        (f"{p}outstanding", r.outstanding),
        (f"{p}latency_seconds", r.mean_latency_seconds),
        (f"{p}scanned_headers", r.scanned_headers),
        (f"{p}scan_seconds", scan_seconds),
    ]
    lines = [f'{name}{{stream="{s}"}} {value}' for name, value in gauges]

    oldest = sorted(r.outstanding_detail, key=lambda o: o.age, reverse=True)
    shown = oldest[:top_n]
    truncated = len(oldest) - len(shown)

    aged = 0
    for item in shown:
        age = int(item.age.total_seconds())
        labels = f'stream="{s}",msgid="{item.msg_id.hex()}",sent="{item.put_time.isoformat()}"'
        if config.thresholds is not None:
            state = _state_for(item.age.total_seconds(), config.thresholds)
            top_bucket = max(config.thresholds, key=lambda t: t[1])[0]
            if state == top_bucket:
                aged += 1
            labels += f',state="{state}"'
        lines.append(f"{p}outstanding_message{{{labels}}} {age}")

    lines.append(f'{p}outstanding_truncated{{stream="{s}"}} {truncated}')
    if config.thresholds is not None:
        lines.append(f'{p}outstanding_aged{{stream="{s}"}} {aged}')

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_exporter.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/exporter.py tests/test_exporter.py
git commit -m "feat(exporter): Prometheus exposition — gauges, top-N detail, optional states"
```

---

### Task 6: pymqi MQ adapter (browse-only)

**Files:**
- Create: `src/rrecon/mq.py`
- Test: `tests/test_mq_mapping.py`

**Interfaces:**
- Consumes: `ArchivedRecord` (Task 1), `MQConnection`/`TLSConfig` (Task 3).
- Produces: `MQBrowser(connection: MQConnection)` satisfying `Browser` (`browse(queue) -> list[ArchivedRecord]`); module-level `put_time_from_mqmd(md) -> datetime | None` (unit-testable without pymqi).

**Note:** `pymqi` is lazy-imported and coverage-excluded. The live `browse` path is integration-marked (`@pytest.mark.integration`) and not run in the validation container; only the MQMD→record mapping is unit-tested here.

- [ ] **Step 1: Write the failing test (pure mapping, no pymqi)**

```python
# tests/test_mq_mapping.py
from datetime import UTC, datetime
from rrecon.mq import put_time_from_mqmd

class _MD:
    def __init__(self, date, time):
        self.PutDate = date
        self.PutTime = time

def test_put_time_parsed_to_utc():
    md = _MD(b"20260101", b"10300000")  # 10:30:00 (hundredths ignored)
    assert put_time_from_mqmd(md) == datetime(2026, 1, 1, 10, 30, 0, tzinfo=UTC)

def test_unstamped_descriptor_is_none():
    assert put_time_from_mqmd(_MD(b"\x00\x00", b"\x00\x00")) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_mq_mapping.py -v`
Expected: FAIL — no module `rrecon.mq`.

- [ ] **Step 3: Implement (mapping + lazy adapter, browse-only)**

```python
# src/rrecon/mq.py
"""The live Browser, backed by pymqi (needs the MQ client). Read-only: browse only.

pymqi is imported lazily so importing this module does not require the client.
Excluded from coverage; the live browse path is integration-tested on a box.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rrecon.records import ArchivedRecord

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rrecon.config import MQConnection


def put_time_from_mqmd(md: Any) -> datetime | None:
    """Parse MQMD PutDate (YYYYMMDD) + PutTime (HHMMSShh) to a UTC datetime."""
    date_raw = bytes(md.PutDate).strip(b"\x00 ")
    time_raw = bytes(md.PutTime).strip(b"\x00 ")
    if not date_raw or not time_raw:
        return None
    stamp = date_raw.decode() + time_raw.decode()[:6]
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


class MQBrowser:
    """A single reconnectable client connection; opens queues MQOO_BROWSE only."""

    def __init__(self, connection: MQConnection) -> None:
        import pymqi  # type: ignore

        self._pymqi: Any = pymqi
        self._c: Any = pymqi.CMQC
        cd = pymqi.CD(
            ChannelName=connection.channel.encode(),
            ConnectionName=connection.conn_name().encode(),
            TransportType=pymqi.CMQC.MQXPT_TCP,
        )
        sco: Any = None
        if connection.tls is not None:
            cd.SSLCipherSpec = connection.tls.cipher.encode()
            sco = pymqi.SCO(KeyRepository=connection.tls.key_repo.encode())
            if connection.tls.cert_label is not None:
                sco.CertificateLabel = connection.tls.cert_label.encode()
        self._qmgr: Any = pymqi.QueueManager(None)
        opts = pymqi.CMQC.MQCNO_RECONNECT_Q_MGR
        if sco is not None:
            self._qmgr.connect_with_options(connection.qmgr, cd=cd, sco=sco, opts=opts)
        else:
            self._qmgr.connect_with_options(connection.qmgr, cd=cd, opts=opts)

    def _browse_iter(self, queue: str) -> Iterator[ArchivedRecord]:
        handle = self._pymqi.Queue(self._qmgr, queue, self._c.MQOO_BROWSE)
        options = self._c.MQGMO_BROWSE_FIRST | self._c.MQGMO_NO_WAIT
        while True:
            md = self._pymqi.MD()
            gmo = self._pymqi.GMO(Options=options)
            try:
                body = handle.get(None, md, gmo)
            except self._pymqi.MQMIError as exc:
                if exc.reason == self._c.MQRC_NO_MSG_AVAILABLE:
                    return
                raise
            yield ArchivedRecord(
                msg_id=bytes(md.MsgId),
                correl_id=bytes(md.CorrelId),
                put_time=put_time_from_mqmd(md),
                body=body,
            )
            options = self._c.MQGMO_BROWSE_NEXT | self._c.MQGMO_NO_WAIT

    def browse(self, queue: str) -> list[ArchivedRecord]:
        return list(self._browse_iter(queue))

    def disconnect(self) -> None:
        self._qmgr.disconnect()
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_mq_mapping.py -v`
Expected: PASS (2 tests). Confirm `mq.py` is in the coverage `omit` list so the lazy pymqi branch does not fail coverage.

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/mq.py tests/test_mq_mapping.py
git commit -m "feat(mq): browse-only pymqi adapter + MQMD put-time mapping"
```

---

### Task 7: Cached HTTP serve

**Files:**
- Create: `src/rrecon/exporter_server.py`
- Test: `tests/test_serve_cache.py`

**Interfaces:**
- Consumes: `render` (Task 5), `ReconConfig` (Task 3), `Browser` (Task 1).
- Produces: `ScanCache(browser, config, top_n=200)` with `def refresh(self, now: datetime) -> None` and `def exposition(self, now: datetime) -> str` (returns cached text plus a `{prefix}scan_age_seconds{stream}` line computed from `now - last_scan_time`); `def verify_archives(browser, pair) -> None` (fails loud if either queue is unbrowsable); `serve(connection, config, port, scan_interval=60, top_n=200) -> None` wiring an `MQBrowser`, a background refresh loop, and an `HTTPServer`.

**Note:** the HTTP/socket/threading wiring in `serve()` is thin and integration-only (coverage-excluded). The **cache + scan-age + verify** logic is fully unit-tested against a `FakeBrowser` with an injected clock.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_serve_cache.py
from datetime import UTC, datetime, timedelta
import pytest
from rrecon.config import ArchivePair, ReconConfig
from rrecon.exporter_server import ScanCache, verify_archives
from rrecon.fake import FakeBrowser
from rrecon.records import ArchivedRecord

PAIR = ArchivePair("REQ", "RPY")

def _req(mid, minute):
    return ArchivedRecord(mid, b"", datetime(2026, 1, 1, 10, minute, tzinfo=UTC), b"")

def test_exposition_serves_cached_scan_without_rescanning():
    b = FakeBrowser(); b.seed("REQ", [_req(b"a", 0)])
    cache = ScanCache(b, ReconConfig(pair=PAIR, stream="orders"))
    cache.refresh(datetime(2026, 1, 1, 10, 30, tzinfo=UTC))
    b.seed("REQ", [_req(b"b", 1)])  # changes AFTER refresh
    out = cache.exposition(datetime(2026, 1, 1, 10, 31, tzinfo=UTC))
    assert 'rr_requests{stream="orders"} 1' in out  # still the cached value, not 2

def test_scan_age_seconds_reflects_staleness():
    b = FakeBrowser(); b.seed("REQ", [_req(b"a", 0)])
    cache = ScanCache(b, ReconConfig(pair=PAIR, stream="orders"))
    cache.refresh(datetime(2026, 1, 1, 10, 0, tzinfo=UTC))
    out = cache.exposition(datetime(2026, 1, 1, 10, 0, 45, tzinfo=UTC))
    assert 'rr_scan_age_seconds{stream="orders"} 45' in out

def test_verify_archives_fails_loud_on_missing_queue():
    b = FakeBrowser(); b.seed("REQ", [])  # RPY never seeded
    class Guarded(FakeBrowser):
        def browse(self, queue):
            if queue == "RPY":
                raise RuntimeError("MQRC_UNKNOWN_OBJECT_NAME")
            return super().browse(queue)
    with pytest.raises(RuntimeError, match="RPY"):
        verify_archives(Guarded(), PAIR)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_serve_cache.py -v`
Expected: FAIL — no module `rrecon.exporter_server`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/exporter_server.py
"""HTTP serving of a cached exposition — scan cadence decoupled from scrape cadence.

Each scrape returns the last cached scan in O(1); a background loop re-scans on
--scan-interval. rr_scan_age_seconds exposes staleness. The socket/threading
wiring is integration-only (coverage-excluded); the cache logic is unit-tested.
"""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, Any

from rrecon.exporter import render
from rrecon.mq import MQBrowser

if TYPE_CHECKING:
    from datetime import datetime

    from rrecon.config import ArchivePair, MQConnection, ReconConfig
    from rrecon.records import Browser


def verify_archives(browser: Browser, pair: ArchivePair) -> None:
    """Fail loud if either archive queue cannot be browsed (declare-and-verify)."""
    for queue in (pair.request_archive, pair.reply_archive):
        try:
            browser.browse(queue)
        except Exception as exc:  # noqa: BLE001 — re-raised with queue context
            msg = f"archive queue {queue!r} is not browsable: {exc}"
            raise RuntimeError(msg) from exc


class ScanCache:
    """Holds the last rendered exposition and the instant it was produced."""

    def __init__(self, browser: Browser, config: ReconConfig, top_n: int = 200) -> None:
        self._browser = browser
        self._config = config
        self._top_n = top_n
        self._text = ""
        self._scanned_at: datetime | None = None

    def refresh(self, now: datetime) -> None:
        self._text = render(self._browser, self._config, now, top_n=self._top_n)
        self._scanned_at = now

    def exposition(self, now: datetime) -> str:
        if self._scanned_at is None:
            return ""
        age = int((now - self._scanned_at).total_seconds())
        p, s = self._config.prefix, self._config.stream
        return f'{self._text}{p}scan_age_seconds{{stream="{s}"}} {age}\n'


def _make_handler(cache: ScanCache, clock: Any) -> type[BaseHTTPRequestHandler]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path not in ("/metrics", "/"):
                self.send_response(404)
                self.end_headers()
                return
            body = cache.exposition(clock()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            return

    return _Handler


def serve(
    connection: MQConnection,
    config: ReconConfig,
    port: int,
    scan_interval: int = 60,
    top_n: int = 200,
) -> None:  # pragma: no cover — integration wiring
    from datetime import UTC, datetime

    browser = MQBrowser(connection)
    verify_archives(browser, config.pair)
    cache = ScanCache(browser, config, top_n=top_n)
    cache.refresh(datetime.now(UTC))

    def _loop() -> None:
        while True:
            time.sleep(scan_interval)
            cache.refresh(datetime.now(UTC))

    threading.Thread(target=_loop, daemon=True).start()
    handler = _make_handler(cache, lambda: datetime.now(UTC))
    HTTPServer(("", port), handler).serve_forever()
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_serve_cache.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/exporter_server.py tests/test_serve_cache.py
git commit -m "feat(serve): cached exposition, scan-interval loop, scan-age, declare-and-verify"
```

---

### Task 8: Dashboard generator

**Files:**
- Create: `src/rrecon/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: nothing (emits queries by prefix/stream).
- Produces: `build_dashboard(qmgr: str, stream: str, prefix: str = "rr_") -> dict` — a Grafana dashboard JSON dict with KPI tiles (requests/replied/outstanding/latency), a backlog timeseries, a scan-cost timeseries, and an outstanding table, all with generic titles and `qmgr` parameterized into panel targets.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dashboard.py
from rrecon.dashboard import build_dashboard

def test_dashboard_has_generic_titles_and_qmgr():
    d = build_dashboard("QM1", "orders")
    assert d["title"]
    titles = {p["title"] for p in d["panels"]}
    assert {"Requests", "Replied", "Outstanding"} <= titles
    text = str(d)
    assert "QM1" in text          # qmgr parameterized in
    assert "counterparty" not in text  # no finance vocabulary
    assert "trade" not in text.lower()

def test_dashboard_uses_prefix_in_targets():
    d = build_dashboard("QM1", "orders", prefix="rr_")
    assert "rr_outstanding" in str(d)
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_dashboard.py -v`
Expected: FAIL — no module `rrecon.dashboard`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/dashboard.py
"""Code-generated Grafana board for the reconciliation metrics (QM-parameterized)."""
from __future__ import annotations


def _stat(title: str, expr: str, panel_id: int) -> dict:
    return {
        "id": panel_id,
        "title": title,
        "type": "stat",
        "targets": [{"expr": expr, "refId": "A"}],
    }


def build_dashboard(qmgr: str, stream: str, prefix: str = "rr_") -> dict:
    sel = f'{{stream="{stream}",qmgr="{qmgr}"}}'
    panels = [
        _stat("Requests", f"{prefix}requests{sel}", 1),
        _stat("Replied", f"{prefix}replied{sel}", 2),
        _stat("Outstanding", f"{prefix}outstanding{sel}", 3),
        _stat("Mean latency (s)", f"{prefix}latency_seconds{sel}", 4),
        {
            "id": 5,
            "title": "Backlog",
            "type": "timeseries",
            "targets": [{"expr": f"{prefix}outstanding{sel}", "refId": "A"}],
        },
        {
            "id": 6,
            "title": "Scan cost",
            "type": "timeseries",
            "targets": [
                {"expr": f"{prefix}scanned_headers{sel}", "refId": "A"},
                {"expr": f"{prefix}scan_seconds{sel}", "refId": "B"},
            ],
        },
        {
            "id": 7,
            "title": "Outstanding requests",
            "type": "table",
            "targets": [{"expr": f"{prefix}outstanding_message{sel}", "refId": "A"}],
        },
    ]
    return {
        "title": f"Request-reply reconciliation — {stream} @ {qmgr}",
        "schemaVersion": 39,
        "panels": panels,
    }
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_dashboard.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): generic Grafana board generator"
```

---

### Task 9: CLI

**Files:**
- Create: `src/rrecon/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_dashboard` (Task 8), `render` (Task 5), `ReconConfig`/`ArchivePair`/`Correlation`/`Window`/`MQConnection`/`TLSConfig` (Task 3), `MQBrowser` (Task 6), `serve` (Task 7).
- Produces: `build_parser() -> argparse.ArgumentParser`; `parse_thresholds(spec: str) -> list[tuple[str, int]]`; `main(argv: Sequence[str] | None = None) -> int` with subcommands `render` / `serve` / `dashboard`.

**Note:** `render`/`serve` handlers construct an `MQBrowser` (live) — those handler bodies are integration-only (coverage-excluded via a `# pragma: no cover` on the construction lines). Parser construction and `parse_thresholds` are unit-tested.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
import json
import pytest
from rrecon.cli import build_parser, main, parse_thresholds

def test_parse_thresholds():
    assert parse_thresholds("within-sla:0,awaiting:600,aged:1800") == [
        ("within-sla", 0), ("awaiting", 600), ("aged", 1800)
    ]

def test_dashboard_subcommand_emits_json(capsys):
    rc = main(["dashboard", "--qmgr", "QM1", "--stream", "orders"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["panels"]

def test_render_requires_archives():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["render"])  # missing required --request-archive/--reply-archive
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_cli.py -v`
Expected: FAIL — no module `rrecon.cli`.

- [ ] **Step 3: Implement**

```python
# src/rrecon/cli.py
"""The rrecon CLI: render / serve / dashboard."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from rrecon.config import ArchivePair, Correlation, MQConnection, ReconConfig, TLSConfig, Window
from rrecon.dashboard import build_dashboard

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def parse_thresholds(spec: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for part in spec.split(","):
        name, _, floor = part.partition(":")
        out.append((name, int(floor)))
    return out


def _connection(args: argparse.Namespace) -> MQConnection:
    tls = None
    if args.tls_cipher is not None:
        tls = TLSConfig(args.tls_cipher, args.key_repo or "", args.cert_label)
    return MQConnection(args.qmgr, args.channel, args.host, args.port, tls=tls)


def _config(args: argparse.Namespace) -> ReconConfig:
    window = Window(
        start=datetime.fromisoformat(args.window_start) if args.window_start else None,
        end=datetime.fromisoformat(args.window_end) if args.window_end else None,
    )
    return ReconConfig(
        pair=ArchivePair(args.request_archive, args.reply_archive),
        stream=args.stream,
        correlation=Correlation(args.request_field, args.reply_field),
        window=window,
        thresholds=parse_thresholds(args.thresholds) if args.thresholds else None,
        prefix=args.prefix,
    )


def render_cmd(args: argparse.Namespace) -> int:  # pragma: no cover — live browser
    from rrecon.exporter import render
    from rrecon.mq import MQBrowser

    browser = MQBrowser(_connection(args))
    print(render(browser, _config(args), datetime.now(UTC), top_n=args.top_n), end="")
    return 0


def serve_cmd(args: argparse.Namespace) -> int:  # pragma: no cover — live server
    from rrecon.exporter_server import serve

    serve(_connection(args), _config(args), args.port, args.scan_interval, args.top_n)
    return 0


def dashboard_cmd(args: argparse.Namespace) -> int:
    print(json.dumps(build_dashboard(args.qmgr, args.stream, args.prefix), indent=2))
    return 0


def _add_recon_args(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--request-archive", required=True)
    sub.add_argument("--reply-archive", required=True)
    sub.add_argument("--stream", default="default")
    sub.add_argument("--qmgr", required=True)
    sub.add_argument("--channel", required=True)
    sub.add_argument("--host", required=True)
    sub.add_argument("--port", type=int, default=1414)
    sub.add_argument("--request-field", default="msg_id")
    sub.add_argument("--reply-field", default="correl_id")
    sub.add_argument("--window-start")
    sub.add_argument("--window-end")
    sub.add_argument("--thresholds")
    sub.add_argument("--prefix", default="rr_")
    sub.add_argument("--top-n", type=int, default=200)
    sub.add_argument("--tls-cipher")
    sub.add_argument("--key-repo")
    sub.add_argument("--cert-label")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rrecon", description="MQ request-reply reconciliation")
    sub = parser.add_subparsers(dest="command", required=True)

    render_p = sub.add_parser("render", help="emit the exposition once")
    _add_recon_args(render_p)
    render_p.set_defaults(handler=render_cmd)

    serve_p = sub.add_parser("serve", help="serve cached /metrics over HTTP")
    _add_recon_args(serve_p)
    serve_p.add_argument("--serve-port", dest="port", type=int, default=9109)
    serve_p.add_argument("--scan-interval", type=int, default=60)
    serve_p.set_defaults(handler=serve_cmd)

    dash_p = sub.add_parser("dashboard", help="emit the Grafana board JSON")
    dash_p.add_argument("--qmgr", required=True)
    dash_p.add_argument("--stream", default="default")
    dash_p.add_argument("--prefix", default="rr_")
    dash_p.set_defaults(handler=dashboard_cmd)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = cast("Callable[[argparse.Namespace], int]", args.handler)
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
```

Note: `serve`'s port flag is `--serve-port` (dest `port`) to avoid colliding with the connection `--port`; default 9109.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_cli.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/rrecon/cli.py tests/test_cli.py
git commit -m "feat(cli): render / serve / dashboard subcommands"
```

---

### Task 10: Order-fulfillment example + end-to-end test

**Files:**
- Create: `examples/order-fulfillment/scenario.py`
- Create: `examples/order-fulfillment/README.md`
- Test: `tests/test_example_order_fulfillment.py`

**Interfaces:**
- Consumes: `FakeBrowser` (Task 2), `render` (Task 5), `reconcile` (Task 4), config types (Task 3).
- Produces: `build_scenario(now) -> tuple[FakeBrowser, ReconConfig]` seeding `ORD.REQ.ARCHIVE` / `ORD.RPY.ARCHIVE` with order requests and warehouse confirmations, with a contiguous **outage window** of unconfirmed orders; `main() -> None` printing the exposition and the outstanding table.

- [ ] **Step 1: Write the failing end-to-end test**

```python
# tests/test_example_order_fulfillment.py
from datetime import UTC, datetime
from examples.order_fulfillment.scenario import OUTAGE_IDS, build_scenario
from rrecon.core import reconcile

def test_outage_orders_are_exactly_the_outstanding_set():
    now = datetime(2026, 1, 1, 17, tzinfo=UTC)
    browser, config = build_scenario(now)
    r = reconcile(browser, config.pair, config.correlation, config.window, now)
    assert {o.msg_id for o in r.outstanding_detail} == set(OUTAGE_IDS)
    assert r.outstanding == len(OUTAGE_IDS)
    assert r.replied == r.requests - len(OUTAGE_IDS)
```

(Ensure `examples/__init__.py` and `examples/order_fulfillment/__init__.py` exist so the test can import the scenario; the directory on disk is `examples/order-fulfillment/` but the importable package is `examples/order_fulfillment/` — create the package dir with underscores and keep the README under it.)

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_example_order_fulfillment.py -v`
Expected: FAIL — no module `examples.order_fulfillment.scenario`.

- [ ] **Step 3: Implement the scenario**

```python
# examples/order_fulfillment/scenario.py
"""Order-fulfillment demo: order requests vs warehouse confirmations, with an outage."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from rrecon.config import ArchivePair, ReconConfig
from rrecon.core import reconcile
from rrecon.exporter import render
from rrecon.fake import FakeBrowser
from rrecon.records import ArchivedRecord

_DAY = datetime(2026, 1, 1, 9, tzinfo=UTC)
_TOTAL = 40
_OUTAGE = range(20, 28)  # orders placed during the warehouse outage window
OUTAGE_IDS = [f"ORDER-{i:04d}".encode() for i in _OUTAGE]


def build_scenario(now: datetime) -> tuple[FakeBrowser, ReconConfig]:
    browser = FakeBrowser()
    requests, replies = [], []
    for i in range(_TOTAL):
        oid = f"ORDER-{i:04d}".encode()
        placed = _DAY + timedelta(minutes=i)
        requests.append(ArchivedRecord(oid, b"", placed, b"order"))
        if i not in _OUTAGE:  # the warehouse confirmed everything except the outage window
            replies.append(ArchivedRecord(b"C" + oid, oid, placed + timedelta(seconds=30), b"ack"))
    browser.seed("ORD.REQ.ARCHIVE", requests)
    browser.seed("ORD.RPY.ARCHIVE", replies)
    config = ReconConfig(
        pair=ArchivePair("ORD.REQ.ARCHIVE", "ORD.RPY.ARCHIVE"),
        stream="orders",
        thresholds=[("within-sla", 0), ("awaiting", 600), ("aged", 1800)],
    )
    return browser, config


def main() -> None:
    now = datetime(2026, 1, 1, 17, tzinfo=UTC)
    browser, config = build_scenario(now)
    print(render(browser, config, now, top_n=200))
    report = reconcile(browser, config.pair, config.correlation, config.window, now)
    print(f"\nOutstanding orders ({report.outstanding}):")
    for item in sorted(report.outstanding_detail, key=lambda o: o.age, reverse=True):
        print(f"  {item.msg_id.decode()}  placed {item.put_time.isoformat()}  age {item.age}")


if __name__ == "__main__":
    main()
```

Create empty `examples/__init__.py` and `examples/order_fulfillment/__init__.py`. Write `examples/order-fulfillment/README.md` (or under the underscore package) with the metric→meaning table from spec §7 and a note on pointing the same `ReconConfig` at a live queue manager via `MQBrowser`.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_example_order_fulfillment.py -v`
Expected: PASS.

- [ ] **Step 5: Full validation + commit**

Run: `vrg-container-run -- vrg-validate`
Expected: PASS (all tasks' tests; coverage green with `mq.py`/`exporter_server.py` serve-wiring omitted).

```bash
git add examples/ tests/test_example_order_fulfillment.py
git commit -m "feat(example): order-fulfillment walkthrough + end-to-end outage test"
```

---

## Self-Review

**Spec coverage:**
- §3 repo shape → Task 0 (bootstrap, `vergil.toml` no `[vm]`, coverage omit).
- §4 record + Browser → Task 1; correlation/window → Task 3; reconcile (cohort window, latency earliest-wins, scanned_headers pre-filter, fail-loud, future-reply ignored) → Task 4.
- §5 config + MQ adapter (browse-only, TLS) + CLI → Tasks 3, 6, 9; declare-and-verify → Task 7.
- §6 metrics (six gauges + scan-age, top-N + truncation, ISO sent label, optional thresholds/state/aged) → Tasks 5, 7.
- §7 order-fulfillment example → Task 10.
- §8 error handling + testing → covered per task (fail-loud in Tasks 4 & 7; integration-marked pymqi in Task 6).
- §10 deferred items → intentionally not built (tail-latency histogram, orphan-reply signal, msgid opacity, incremental/local-bindings scan).

**Placeholder scan:** no TBD/TODO; every code step carries real code and exact commands.

**Type consistency:** `render(browser, config, now, timer, top_n)` used consistently (Tasks 5, 7, 10); `reconcile(browser, pair, correlation, window, now)` consistent (Tasks 4, 5, 10); `Correlation.request_key`/`reply_key`, `Window.contains`, `MQConnection.conn_name` all defined in Task 3 and used as defined; `MQBrowser(connection)` consistent (Tasks 6, 7, 9); `ScanCache`/`verify_archives` consistent (Task 7). `build_dashboard(qmgr, stream, prefix)` consistent (Tasks 8, 9).

## Execution Handoff

This plan is intentionally parked at the planning stage (see epic #59). When picked up, implement task-by-task via `superpowers:subagent-driven-development` (fresh subagent per task, review between) or `superpowers:executing-plans` (batch with checkpoints), starting once the `mq-request-reply-recon` repo exists (Task 0).
