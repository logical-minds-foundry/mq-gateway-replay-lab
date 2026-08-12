# Epic #59 — Generic MQ request-reply reconciliation tool — spec

**Status:** Design approved; ready for implementation planning
**Epic:** logical-minds-foundry/mq-gateway-replay-lab#59
**Origin:** Extraction of the domain-agnostic reconciliation kernel already
present in `mq-gateway-replay-lab` (`reconcile.py`, `exporter.py`,
`messaging.py`, `dashboard.py`) into a standalone tool.

## 1. Motivation

`mq-gateway-replay-lab` demonstrates resilient, replayable MQ request-reply
patterns in a trading back-office setting. Underneath the finance vocabulary
(trades, counterparty, trading day, SLA) sits a fully generic capability: given
two MQ **archive** queues holding matching request/reply pairs, browse both,
match each request to its reply on a correlation id, and emit a small set of
observability metrics — how many were sent, how many came back, which are
outstanding and for how long, and what the reconciliation scan itself costs.

That kernel is not finance-specific. This epic extracts it into a standalone
tool so the pattern can be pointed at *any* request-reply flow, with the finance
lab remaining one private worked example and a new order-fulfillment example
serving as the tool's own self-contained demonstration.

## 2. Thesis and boundaries

- **Domain-agnostic, not broker-agnostic.** The tool is deliberately, explicitly
  **MQ-native**. The value of the pattern is that the queue manager does the
  atomic audit copy for you (streaming queues); an abstraction across Kafka / DB /
  MQ would be a mirage, since their durability and browse semantics differ
  radically. Genericity runs along the *business domain* axis (trades → orders →
  RPCs → anything), never the broker axis.
- **Read-only collector.** The tool observes archive queues and emits metrics. It
  never puts to a live path. The write side (replay / recovery) is designed
  *around* — the output is shaped so a consumer could build resend on top — but
  ships no putter.
- **Documents, never provisions.** The recommended queue configuration is a
  documented precondition, not something the tool creates.

## 3. Identity and repo shape

- **Repo:** `mq-request-reply-recon` (new; created as the epic's first
  implementation task)
- **Visibility: public.** This is the point of the extraction — a generic,
  broadly-useful demonstration of the MQ request-reply reconciliation pattern,
  publishable as a portfolio/OSS artifact. It carries **zero** finance or
  client-adjacent content by construction, so the tool's domain-agnostic
  discipline (generic vocabulary, the order-fulfillment example, no lab
  coupling) is not a nicety but a **load-bearing** requirement for a public
  repo. Because the repo is public, epics *originating in it* home centrally in
  `<org>/.github`; this epic (#59) stays homed in the private gateway lab, since
  it is the lab's extraction effort — only the implementation PRs land in the
  new public repo.
- **Python package + CLI:** `rrecon`
- **Metric prefix:** `rr_` (configurable)
- **Per-stream label:** `stream` (replaces `counterparty`)

(Names are the working choice; all are trivially changeable.)

```text
mq-request-reply-recon/
  src/rrecon/
    core.py          # matcher, window, correlation — pure, clock-injected
    records.py       # ArchivedRecord model + the Browser port (protocol)
    mq.py            # pymqi adapter
    fake.py          # in-memory adapter for tests/examples
    exporter.py      # Prometheus text exposition
    dashboard.py     # Grafana JSON generator
    config.py        # ArchivePair, ReconConfig, Correlation, Window, MQConnection
    cli.py           # render / serve / dashboard
  examples/order-fulfillment/
    ...              # fake-driven scenario + walkthrough doc
  tests/
  docs/
  vergil.toml        # tooling / python; NO [vm] shared_from (needs no live box)
```

### In scope

- The generic **core** (matcher + window + correlation), de-financed from
  `reconcile.py`.
- The **MQ adapter** (`pymqi`, with the TLS support the lab already has) and a
  **fake** adapter, from `messaging.py` / `pymqi_messaging.py` /
  `fake_messaging.py`.
- The **exporter** (Prometheus text) and **dashboard generator** (Grafana JSON),
  de-financed from `exporter.py` / `dashboard.py`.
- The **CLI**: `render`, `serve`, `dashboard`.
- The **order-fulfillment reference example**.

### Out of scope

- **Recovery / replay** (write side) — designed around, not built.
- **Provisioning** — the recommended `STREAMQ` / `STRMQOS(MUSTDUP)` /
  `CAPEXPRY` setup is documented as a precondition; the tool creates nothing.
- **Retrofitting `mq-gateway-replay-lab` to consume the extracted tool** — a
  natural follow-on, but a separate effort (epic #59 follow-on bookend). The
  finance lab keeps working untouched.
- The finance lifecycle verbs (`start-of-day` / `run` / `end-of-day`) do **not**
  come across — they are gateway concerns, not collector concerns.

## 4. The core (generic reconcile)

`reconcile.py` with the finance skeleton removed: no trading day, no hardcoded
SLA thresholds, no `counterparty`. Pure, clock-injected, transport-free.

### Record and port (`records.py`)

The minimal contract an adapter must satisfy:

```python
@dataclass(frozen=True)
class ArchivedRecord:
    msg_id:    bytes
    correl_id: bytes
    put_time:  datetime | None   # when MQ archived it
    body:      bytes

class Browser(Protocol):
    def browse(self, queue: str) -> list[ArchivedRecord]: ...
```

### Correlation (declarative, serializable)

The two identity fields are the only match keys — this covers the canonical echo
and its reverse:

```python
@dataclass(frozen=True)
class Correlation:
    request_field: str = "msg_id"     # key extracted from each request record
    reply_field:   str = "correl_id"  # key it must equal on some reply record
```

Default: a request matches a reply when the reply's `correl_id` equals the
request's `msg_id` — the canonical MQ request-reply idiom, where the responder
echoes the request `MsgId` into the reply `CorrelId`.

### Window (optional put-time filter)

```python
@dataclass(frozen=True)
class Window:          # Window() ⇒ everything the archive holds
    start: datetime | None = None
    end:   datetime | None = None
```

`CAPEXPRY` already bounds the archive to one rolling retention period, so the
open window is the natural default. The window replaces the finance
"current trading day" scoping, which was a domain policy, not a core concern.

**The window is a request-cohort selector, not a filter on both sides.** It
selects *which requests* the run is about (`request.put_time ∈ [start, end)`);
replies are matched from the **full** browsed reply set, bounded only by `now`
(a reply with `put_time > now` is ignored — no future replies). This keeps
`window` (the cohort) and `now` (the as-of instant) orthogonal, and avoids the
boundary artifact where a matched pair straddling `end` — request at 09:59,
reply at 10:01, `end` at 10:00 — would otherwise be miscounted as outstanding.
The question the window answers is "of the requests put in this range, which are
still outstanding as of `now`, and how late are the answered ones?"

### Function and result

```python
def reconcile(browser, pair, correlation, window, now) -> Reconciliation
```

1. Browse both archives.
2. `scanned_headers` = total records browsed from both archives **before** window
   filtering — the true cost, one `MQGET(BROWSE)` round-trip per header. *(This
   is a deliberate correction from the finance version, which counted post-filter;
   the honest cost is every header the browse touched, and it is the backbone of
   the scale story.)*
3. Filter **requests** to the window by `put_time` (the window selects the
   request cohort). Replies are **not** window-filtered — they are matched from
   the full browsed set, bounded only by `now` (`put_time ≤ now`).
4. Build the set of reply keys (`put_time ≤ now`); a request is **replied** iff
   its correlation key is in that set, else **outstanding**.
5. **Latency** (matched): `reply.put_time − request.put_time`, reply keyed by
   correlation, **earliest reply wins** on duplicate keys. Mean over all matched.
6. **Outstanding detail**: `(msg_id, put_time, age = now − put_time)`.

```python
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
```

There is **no `state`** in the core. Age is emitted raw; threshold-bucketing is
the exporter's optional, consumer-configured job (§6).

### Invariants carried over on purpose

- **Clock injected, never read.** `now` is a parameter, so every reconcile is
  deterministic and testable.
- **Fail loud on missing `put_time`.** A record with no put-time cannot be
  windowed or aged — a data-integrity fault in the archive feed. It raises; it is
  never silently dropped.

## 5. MQ adapter and driving config

The driving thesis: *all you need to point the tool at a flow is the request
queue, its reply queue, and where they are archived.*

### Config (`config.py`)

Semantics, separated from the connection:

```python
@dataclass(frozen=True)
class ArchivePair:
    request_archive: str
    reply_archive:   str

@dataclass(frozen=True)
class ReconConfig:
    pair:        ArchivePair
    stream:      str = "default"              # the label on every metric
    correlation: Correlation = Correlation()  # default: msg_id → correl_id echo
    window:      Window = Window()            # default: everything in the archive
    thresholds:  list[tuple[str, int]] | None = None
    prefix:      str = "rr_"
```

Everything past `pair` has a sane default, so the minimal drive is two queue
names.

Connection (transport), kept separate:

```python
@dataclass(frozen=True)
class MQConnection:
    qmgr: str
    channel: str
    host: str
    port: int = 1414
    tls: TLSConfig | None = None   # cipher + client keystore, from pymqi_messaging.py
```

### Adapter (`mq.py`)

`browse(queue)` runs a non-destructive `MQGET(BROWSE)` loop, mapping each message
to `ArchivedRecord`: `msg_id` / `correl_id` from the MQMD, `put_time` from
`PutDate` + `PutTime`, `body` from the payload. Read-only by construction;
`CAPEXPRY` on the queues handles retention. This is `pymqi_messaging.py`
generalized, carrying its TLS support.

### CLI

Minimal invocation:

```bash
rrecon render \
  --request-archive ORD.REQ.ARCHIVE --reply-archive ORD.RPY.ARCHIVE \
  --stream orders --qmgr QM1 --channel APP.SVRCONN --host mq.example
```

Optional: `--window-start` / `--window-end` (ISO), `--request-field` /
`--reply-field`, `--thresholds within-sla:0,awaiting:600,aged:1800`, `--prefix`,
`--top-n` (per-request series cap, default 200), `--scan-interval` (`serve`
only, default 60 s), TLS flags — or `--config orders.toml` for all of it.

Subcommands:

- `rrecon render` — one-shot Prometheus text to stdout.
- `rrecon serve` — HTTP `/metrics`, serving a **cached** exposition. The scan
  cadence is decoupled from the scrape cadence: `serve` reconciles on its own
  `--scan-interval` (default 60 s) into a cached result, and each scrape returns
  that cached text in O(1) regardless of archive size. This is the key
  correction over the finance `exporter_server`, which re-browsed both archives
  on *every* scrape — safe at demo scale (122 headers) but a self-DoS at the
  ~21-minute full-archive scan the tool's own scale analysis measures at 1M/day.
  An `rr_scan_age_seconds` gauge exposes staleness so the board can alert if the
  scan loop stalls.
- `rrecon dashboard` — Grafana JSON to stdout; `--qmgr` a parameter, never
  hardcoded.

### Boundary behaviors (from the lab's parasite philosophy)

- **Documents, never provisions.** The precondition — each live queue set
  `STREAMQ(<archive>) STRMQOS(MUSTDUP)` with `CAPEXPRY(<retention>)` — lives in
  the docs as what you set up first. The tool creates nothing.
- **Declare-and-verify.** On startup, `render` / `serve` verify both archive
  queues are openable for browse and **fail loud** with a clear message if not. A
  missing archive is a signal, not a silent empty result.

## 6. Metrics and output

Prefix `rr_` (configurable), label `stream`. Six gauges — the
kernel-plus-scan-cost set:

```text
rr_requests{stream}          # in-window (cohort) request count
rr_replied{stream}           # matched
rr_outstanding{stream}       # unmatched — the backlog
rr_latency_seconds{stream}   # mean request→reply delay
rr_scanned_headers{stream}   # total headers the browse touched (the cost)
rr_scan_seconds{stream}      # wall-clock of the reconcile pass (injected timer)
rr_scan_age_seconds{stream}  # age of the cached scan (serve only; staleness)
```

`rr_latency_seconds` is the **mean** only — tail latency (a histogram / p95–p99)
is a deferred enhancement (§10), not v1.

The per-request series, value = age in seconds, **capped at the `--top-n` oldest**
(default 200) to bound Prometheus cardinality — the outstanding set is largest
during exactly the outage this tool observes, so an uncapped series-per-request
would risk destabilising Prometheus when it matters most:

```text
rr_outstanding_message{stream,msgid,sent} <age_seconds>   # top-N oldest
rr_outstanding_truncated{stream} <count_omitted>          # never a silent cap
```

The `sent` label is a full ISO-8601 timestamp (date + time), not time-of-day —
a window may span more than one day, so time-of-day alone would be ambiguous.
The complete outstanding set (beyond the top-N) remains available via one-shot
`rrecon render` for forensic use.

### SLA states as an optional exporter add-on

The demoted thresholds live here, not in the core. The exporter takes an
optional consumer-supplied bucket config:

```python
thresholds = [("within-sla", 0), ("awaiting", 600), ("aged", 1800)]  # name, min-age seconds
```

- **Absent** (default): ages emitted raw. No `state` label, no aged gauge.
- **Present**: the per-request series gains a `state` label, and one extra gauge
  `rr_outstanding_aged{stream}` counts requests in the top bucket (the
  highest-min-age entry). Nothing about "SLA" or "trading" is baked into the
  core — it is a policy the consumer hands in.

### Dashboard

`dashboard.py` de-financed: KPI tiles (requests / replied / outstanding /
latency), a backlog chart, a scan-cost chart, and the outstanding table. Generic
titles; `--qmgr` parameterized.

## 7. The order-fulfillment reference example

`examples/order-fulfillment/`, driven entirely by the **fake** adapter so it runs
with no live queue manager — a teaching artifact, not a deployment.

**Scenario.** Seed a request archive with N order-request records (`msg_id` =
order id) and a reply archive with fulfillment-confirmation records (`correl_id` =
echoed order id) for most of them, punching a gap that simulates a **warehouse
outage window** so that cluster of orders stays outstanding. Run `reconcile` +
`render`; print the metrics and the outstanding table.

**Walkthrough doc** maps every metric to its plain meaning:

| Metric | Order-fulfillment meaning |
|---|---|
| `rr_requests` | orders placed |
| `rr_replied` | orders confirmed by the warehouse |
| `rr_outstanding` | orders awaiting confirmation — the backlog |
| `rr_latency_seconds` | mean time-to-confirm |
| `rr_outstanding_message` | which orders are hanging, each with its age |
| `rr_scanned_headers` / `rr_scan_seconds` | what the reconciliation browse costs as the archive grows |

The example also flips on the optional `thresholds` to show
within-SLA / awaiting / aged bucketing, and closes with a note on pointing the
same config at a real queue manager via the MQ adapter.

## 8. Error handling and testing

**Error handling** (no silent failures, throughout): missing `put_time` raises; a
missing or unopenable archive queue fails loud at startup (declare-and-verify);
MQ connection errors propagate with context, never swallowed into an empty
result.

**Testing** mirrors the lab's suite:

- **Core:** matching; window as a **request-cohort** selector (a matched pair
  straddling `end` — request at 09:59, reply at 10:01, `end` at 10:00 — counts as
  replied, **not** outstanding); `put_time > now` replies ignored; latency
  including duplicate-reply earliest-wins; outstanding age; `scanned_headers` =
  pre-filter total; fail-loud on missing `put_time`; and the empty /
  all-matched / none-matched edges.
- **Correlation:** default echo and the reverse mapping.
- **Exporter:** exposition text, prefix, `stream` label, per-request series
  capped at `--top-n` with `rr_outstanding_truncated` = the omitted count, ISO
  `sent` label, and thresholds present/absent (the `state` label and
  `rr_outstanding_aged` appear only with thresholds); `scan_seconds` via injected
  timer; `serve` caching (scrape returns cached text without re-scanning) and
  `rr_scan_age_seconds`.
- **Dashboard:** JSON shape, generic titles, `--qmgr` parameterized.
- **MQ adapter:** verified against the `Browser` contract via the fake; the
  `pymqi` path behind an integration marker (as the lab separates live from
  fake); MQMD → `ArchivedRecord` mapping unit-tested.
- **Example:** an end-to-end test asserting the outage orders are *exactly* the
  outstanding set — the generic echo of the lab's "18 = exactly the outage
  trades."

**Validation.** The new repo carries the same conventions
(`vrg-container-run -- vrg-validate`; ruff / mypy / pytest via the pipeline) and
its own `vergil.toml` (tooling / python) with no `[vm] shared_from`, since the
example needs no live box.

## 9. Follow-ons (not this epic)

- Retrofit `mq-gateway-replay-lab` to consume `rrecon` (finance becomes a second
  example consumer).
- A recovery/replay layer built on the collector's outstanding-set output.
- Additional example domains if useful (device command/ack, print jobs, etc.).

## 10. Known limitations and deferred enhancements

Surfaced during spec pushback and deliberately **deferred** to keep v1 minimal.
Recorded here so a future revisit does not have to rediscover them:

- **Mean latency only, no tail.** `rr_latency_seconds` is a mean; it hides p95/p99.
  A Prometheus histogram (bucketed latency) would enable tail analysis without
  per-request cardinality — a clean v2 enhancement.
- **Orphan replies are not reported.** With the request-cohort semantics (§4), a
  reply whose correlation key matches no in-cohort request is simply not counted.
  A reply matching *no request at all* can indicate a correlation misconfiguration
  or a request that aged out of the window; an `rr_orphan_replies` signal is a
  possible v2 addition.
- **`msgid` is exposed as a metric label.** In some domains the message id is a
  business identifier (an order id, a customer reference), so it lands in the
  monitoring system as a label. v1 emits it verbatim; an opaque/hashed-id mode is
  a deferred option, and consumers with sensitive ids should weigh this before
  enabling the per-request series.
- **Full-archive scan, not incremental.** `serve` caches (§5) so scrapes are
  O(1), but each scan is still a full-archive browse. At high volume the scale
  analysis calls for a high-water-mark **incremental** scan and **local-bindings**
  transport; both are out of scope for v1 and left as the documented scale path.
