# Load-simulation engine — design

**Epic:** logical-minds-foundry/mq-gateway-replay-lab#65 (Confirmation Tracking v2)
**Status:** design approved in brainstorming; ready for task breakdown → planning
**Relationship:** a follow-on within epic #65. The collector, metrics, and board
(the deliverable) are built and proven live on a small hand-driven batch. This
adds the engine that drives **real-world volume through the real distributed
queuing architecture**, so the board carries real load and the real corner cases
can be measured.

## 1. Motivation

The prototype proved the mechanism live, but on a dozen hand-driven messages put
straight onto the queues. To trust the design we must run it at **real volume**
(~100,000 messages/day, parameterized) through the **real distributed round-trip**
— our instructions crossing the inter-QM link to a responder that processes them
and routes replies back — and then answer the questions that only volume exposes:

- **Collector crash/restart recovery.** If the collector dies mid-day and
  restarts, how long to rebuild the settlement state by browsing the audit
  queues at scale? This recovery time is a headline operational number.
- **Throughput and scan cost** at 100k, on real iron.
- **Physical queue-file size → disk / IO contention** on the boxes — itself a
  thing the observability should surface.
- **Cross-validation:** an independent second view of the same lifecycle.

## 2. Architecture — three moving parts, one real round-trip

```
GATEWAY REQUESTER (our app-simulator, our QM side)          SVC RESPONDER (counterparty-sim, SVCQM side)
  ├─ generate N instructions (MT540-543) via the generator    ├─ drain instructions off the SVCQM landing queue
  ├─ PUT → GW.SEC.INSTRUCT (alias)                             ├─ read the embedded DIRECTIVE
  │     ├─ retained → GW.SEC.INSTRUCT.AUDIT ─stream──────────► │   compute the lifecycle plan → push timed events
  │     └─ duplicate → SVCQM landing (real inter-QM link) ────►│   onto the ONE min-heap scheduler (delays ÷ C)
  ├─ drain replies off GW.SEC.REPLY (does the real work)       └─ emit 548 / 544-547 → SVCQM QREMOTE → GW.SEC.REPLY
  └─ track its OWN app-side lifecycle  ── log source #2              └─ streamed → GW.SEC.STATUS.AUDIT

COLLECTOR (already built): browse GW.SEC.INSTRUCT.AUDIT + GW.SEC.STATUS.AUDIT → fold → metrics / board  ── source #1
```

**Two independent lifecycle views.** The collector derives each instruction's
lifecycle from the **audit queues**; the gateway app derives the same lifecycle
from its **own live put/get path** (the instructions it sent, folded against the
replies it drains) and logs each at its end state. Two code paths, one truth —
they cross-validate. (The gateway app is *our observability simulator*, not the
client's real gateway; but making it keep its own book is nearly free and gives a
second, independent log source — high value.)

Note: this is deliberately **not** the prototype's shortcut (responses put
straight onto the reply queue). Responses are produced by a **separate responder
app on the counterparty queue manager**, over the real link — the faithful
distributed architecture.

## 3. The distributed queue topology

**Our side** — already deployed (#102/#88): `GW.SEC.INSTRUCT` (alias) →
`GW.SEC.INSTRUCT.AUDIT` (retained, `STREAMQ MUSTDUP` → `GW.SEC.INSTRUCT.REMOTE`
`QREMOTE` → SVCQM) and `GW.SEC.REPLY` → `STREAMQ MUSTDUP` → `GW.SEC.STATUS.AUDIT`.

**Counterparty side** — new (`gw-svc-sec-queues` role, on SVCQM), the their-side
half of the round-trip, mirroring the v1 `gw-svc-queues` shape:

- a **landing `QLOCAL`** where our streamed instructions arrive (the target of our
  `QREMOTE`), which the responder drains;
- a **`QREMOTE`** back to our `GW.SEC.REPLY`, over the existing return
  transmission queue + channel (declare-and-verify, never create).

Parasite rule intact: create only the gateway's own `GW.SEC.*` objects on SVCQM;
declare-and-verify the QM and the return link.

## 4. The message generator (the test-data engine)

Generates N instructions (default target volume ~100,000; a parameter), each
comprising:

- **The fields we actually parse** — `:20C::SEME//` (unique), the settlement
  direction (MT type), `:35B:` ISIN, `:36B::SETT//` quantity, `:98A::SETT//`
  dates, `:97A::SAFE//` account. Invented, anonymised, deterministic.
- **A processing directive** (§5) in an ignored free-text region.
- **Size padding** — the same ignored region padded so each message hits a
  realistic **target size** (§8), because the physical queue-file size is a thing
  we observe.

Parameters: `count`, `target_size_bytes`, and the **scenario mix** — the
proportions of outcomes and the delay distributions (e.g. ~90% clean, a few %
rejected, a few % failing, a slow subclass), all knobs. Deterministic given a
seed, so a run is reproducible and re-generable at a bigger `count` by tweaking
the parameter.

PoC scope: we do **not** hand-model every real metadata field's content — this is
a proof of concept; the messages must be *syntactically valid and the right size*,
not field-complete. Hardening the parser against real-sample variety is a
future item (needs real samples).

## 5. The directive-in-metadata scheme

Each generated instruction carries a **deterministic directive** in a free-text
metadata region the collector never reads (its correlation keys are `SEME`/`RELA`
only, so anything else is inert junk from the collector's view). The directive
encodes:

```
directive = (outcome, delay_vector)
  outcome      ∈ { clean, reject(reason), fail(reason) }
  delay_vector = per-stage sim-time delays [ Δ_ack, Δ_match, Δ_settle ]
```

"Slow" is **not** a fourth outcome — it is a large Δ on a stage. "T+n" is a large
Δ on the settle stage. So the vocabulary is three outcomes × an orthogonal timing
vector — the same mechanism produces clean/reject/fail and every timing shape.

The **generator** stamps the directive; the **responder** parses *only* that field
to decide the response sequence, reason codes, and per-stage delays. The exact
carrier field and encoding are an implementation choice (deterministic write,
deterministic read) — it is opaque to the collector, so we have full freedom.

## 6. The responder — a single event scheduler (the scaling core)

Thread-per-request (a thread that sleeps between a request's stages) does not
scale to 100k. Instead:

- On draining an instruction, the responder reads the directive and computes the
  whole **lifecycle plan up front** — a small ordered list of `(emit_at,
  response_message)` events (clean → `[ack, matched, confirmation]`; reject →
  `[rejected]`; fail → `[ack, matched, failing]`), with `emit_at = now + Δ/C`.
- Every event is pushed onto **one shared min-heap** keyed by `emit_at`.
- **One scheduler loop** (or a tiny worker pool) pops the earliest due event and
  PUTs its response to the SVCQM `QREMOTE`.

So it is **one scheduler + N events**, a heap push/pop per event — not N threads.
This scales to millions of events. Batch phase: all Δ = 0 → everything emits
immediately (a fully-realised 100k day at once, for the volume/recovery
measurements). Timed phase: Δ > 0 → events spread across the (compressed) day.

## 7. Time compression — one factor `C`

To simulate a multi-day settlement window without waiting days, the whole run is
compressed by a single factor `C` (sim-time → real-time):

- the responder schedules every event at `Δ / C` (a 2-day sim runs in ~real
  `48h/C`);
- the queue manager stamps real (compressed) put-times; the collector computes
  real-compressed durations;
- the **Grafana display multiplies durations by `C`** (`metric * C` in the panel
  expression) to recover the sim-time values (latencies, ages, SLA).

The multiply is clean for **duration scalars**. A timeseries **time-axis** is real
(compressed) wall-clock, so a compressed run is a **compressed replay** on the
x-axis — which is a feature: watch a 2-day settlement play out in an hour, iterate
fast, regenerate different loads quickly. Setting **`C = 1` makes everything real**
(real timestamps, no display transform) — the production setting. `C` and the
display multiply are the only compression machinery.

Settlement window default **T+1** (US today); `C` and the delay vector carry T+2/
T+3 with no international assumption baked in.

## 8. Message size (grounded by measured examples)

Message sizes were measured from the public worked examples this repo already
cites (Euronext MT541/543; OeKB/T2S MT543/548/547) — block-4 bodies, CRLF-joined,
byte-counted:

| Message class | Measured (block 4) | Model as (typical) | Range |
|---|---|---|---|
| MT541 / MT543 instruction | 422–508 B (2–3 parties) | **~1 KB** | 0.45 KB lean → ~2 KB fully-populated |
| MT548 status advice | ~500–700 B | **~0.6–0.8 KB** | 0.3 → 0.75 KB |
| MT544–547 confirmation | ~770 B | **~0.8 KB** | 0.5 → 1.5 KB |

The measured examples are realistic-but-lean (2–3 settlement parties, one AMT, no
FIA sequence); a fully-populated MT54x (5–6 parties, FIA, multiple AMT/charge
amounts) runs larger — up to ~2 KB. The hard SWIFT FIN ceiling is **10,000
characters (~10 KB)** for the whole message (all blocks), so MT54x sit at a few
percent of the limit.

**Targets for the simulation** (the `target_size_bytes` parameters, defaulting
here, tunable): instructions **~1 KB** typical with a heavy-tail bucket to ~2 KB;
responses **~0.6–0.8 KB**; a hard per-message cap at the **10 KB** FIN ceiling. A
blended **~1 KB/message** is the round number for capacity math — at 100k
messages/day that is **~100 MB/day of raw block-4 payload** *before* the audit-copy
multiplier, which is the figure to carry into the disk/`MAXDEPTH`/IO sizing (§9).

Volume sanity check: ~80k–100k securities messages/day for a single large
custodian is realistic and mid-range — one instruction spawns several messages
(instruction + status advice(s) + confirmation), so 100k messages ≈ 15k–30k
instructions/day, well below T2S's ~700k instructions/day platform-wide and DTC's
~1.3M transactions/day. Sources: ECB TARGET2-Securities Annual Report 2023;
DTCC capabilities; SWIFT FIN traffic figures (securities ≈ half of ~44.8M
messages/day network-wide). The measured message-size figures come from the two
worked-example publishers above; the fully-populated upper bound is a reasoned
estimate, not a sourced measurement.

## 9. What we measure — the experiments

- **Collector crash/restart recovery time.** Run the volume; kill the collector
  mid-day; restart; measure the wall-clock to rebuild the full settlement state
  by browsing the audit queues. The headline operational number.
- **Throughput** (messages/sec the collector folds) and **scan cost** (messages
  scanned vs wall-clock) at 100k, on real iron.
- **Physical queue-file size** of the audit queues under a full day, and **disk /
  IO** on the boxes — surfaced as observability (a candidate demo-specific board).
- **Cross-validation:** the collector-derived lifecycle vs the gateway-app-derived
  lifecycle agree.

## 10. Task breakdown (filed from this design)

1. **Counterparty distributed queues** — `gw-svc-sec-queues` role (SVCQM landing
   `QLOCAL` + return `QREMOTE`) + live deploy.
2. **Message generator** — volume + directive + size-padding + scenario-mix,
   parameterized, deterministic.
3. **Gateway requester app** — put instructions, drain replies, **app-side
   lifecycle tracker** (source #2).
4. **SVC responder app** — drain, read directive, the min-heap scheduler, timed
   responses ÷ `C` (the scaling core).
5. **Time-compression + display rescale** — factor `C` in the responder + the
   Grafana `× C` transform.
6. **Scale & crash-recovery experiment + IO observability** — run 100k, measure
   recovery / throughput / scan-cost / queue-size / IO, add the panels; iterate
   the board on real data.

## 11. Out of scope / notes

- **The report queue** (a third audit of the reply queue so the app can consume
  COA/COD independently) is a **future** feature — not this. Data sources are the
  two audit queues plus the app's own live view.
- **Full metadata-field realism** is out (PoC): messages are syntactically valid
  and correctly sized, not field-complete. Real-sample parser hardening is future.
- The generator/responder/gateway apps are **our simulation tooling**; the client
  builds the real gateway that does the real processing on the live data stream.
