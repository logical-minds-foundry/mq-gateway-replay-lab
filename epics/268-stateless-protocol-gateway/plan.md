# Stateless MQ Protocol Gateway — Implementation Plan

> **For agentic workers:** each task below corresponds to a GitHub issue under
> epic #268 and is implemented with `vergil:issue-implement` (or
> `issue-validate` / `issue-deploy` for operational tasks). Steps use checkbox
> (`- [ ]`) syntax for tracking. PR submission and merge remain human gates.

**Goal:** Produce a working stateless MQ protocol gateway and the evidence that
a protocol layer factored out of a stateful application must not itself be
stateful.

**Architecture:** Two stateless C++ processes in a new component repo — an
outbound protocol translator and an inbound ack-gated consumer with
exclusive-open standby — driven by a Python reference sending application in
this repo that owns the state. Evidence is gathered against the live 3+3 HA/DR
lab and published as four standalone reference documents.

**Tech Stack:** C++ with the IBM MQ C++ classes (`imqi.hpp`), CMake, Vergil's
C++ profile (clang-format, clang-tidy, cppcheck, ASan/UBSan, coverage);
Python 3.12, `pymqi`, SQLite, pytest; Ansible for lab deployment;
Prometheus/Grafana for observability.

**Spec:** [`spec.md`](spec.md) — the plan argues from the spec; executors read
both.

## Global Constraints

Copied verbatim from spec §10. Every task's requirements implicitly include
these.

- **Anonymisation is absolute.** No employer/client-identifiable content in any
  file, commit, diagram, or dummy data. The counter-example is a *generic
  anti-pattern exemplar*, never a reproduction of shared source.
- **Self-owning bolt-on rule.** Any change to the underlying lab is automated,
  owned, replayable from scratch, and stays generic in the base lab.
- **Reuse none of the lab's tooling.** This repo ships its own CLI and apps.
- **XA is out — permanently.** `MQBEGIN` is not valid on a client connection,
  and the gateway will always connect as a client.
- **Latency is not a design constraint.** Back-office settlement, hours and
  days (T+1, T+2), by contract. No task optimises throughput.
- **100% branch + line coverage, zero omissions**, in *both* repositories.
  `[tool.coverage.run] omit` stays empty here; the component repo's coverage
  gate is established by C1.
- **Validation is `vrg-container-run -- vrg-validate`** and nothing else.
- Commit with `vrg-commit`; branch `feature/<issue>-<slug>` off `develop`.

## Known-red baseline

`develop` currently fails `vrg-validate` on **19 pre-existing `ansible-lint`
violations** in `ansible/roles/gw-*` (role-name pattern, name casing, YAML comma
spacing), with the profile message indicating a tooling upgrade raised the
required bar. **This blocks every PR in this repo, including the docs PR.**
Task 0 clears it. Do not begin any other lab-repo task until it is green.

---

## Sequencing

The spec contains three distinct modes of work. They are sequenced
deliberately rather than interleaved, because the reference documents are
largely independent of the build and are the natural cut line if the epic has
to be shortened.

| Phase | Mode | Tasks | Rationale |
|---|---|---|---|
| 0 | Unblock | T0 | Nothing merges until validation is green |
| 1 | Reference documents | T1–T3 | No build dependency; publishable alone; two are already substantially researched |
| 2 | Foundations | T4–T6 | Toolchain risk front-loaded; protocol fixed before either side implements it |
| 3 | Build | T7–T9 | Gateways and the reference app |
| 4 | Deploy and measure | T10–T13, incl. **T12b** | Evidence gathering against the live lab |
| 5 | Synthesis | T14–T15 | The arithmetic and the argument, which need the measurements |
| 6 | Tail | T16–T18 | Counter-example, rename, closing bookends |

**The critical path is T0 → T4 → T6 → T8 → T10 → T11 → T14.** Everything else
parallelises against it.

---

## File Structure

**New repo `mq-protocol-gateway`** (created in T4):

| Path | Responsibility |
|---|---|
| `vergil.toml` | Vergil C++ profile, compiler matrix, private repo |
| `CMakeLists.txt` | Top-level build; targets for both gateways and tests |
| `src/protocol/frame.{hpp,cpp}` | Wire framing only — encode/decode, no I/O |
| `src/protocol/transport.{hpp,cpp}` | Socket I/O over frames; the seam that tests replace |
| `src/mqi/session.{hpp,cpp}` | Connect/open/close lifecycle; the mockable error seam |
| `src/outbound/main.cpp` | Outbound gateway process |
| `src/inbound/main.cpp` | Inbound gateway process, standby state machine |
| `src/inbound/state.{hpp,cpp}` | `CONNECTING_APP`/`STANDBY`/`ACTIVE`/`FAILED` model |
| `conformance/frames/*.bin` | Byte-level fixtures |
| `conformance/manifest.json` | Expected parse results and acks |
| `tests/` | Unit tests; layout fixed by C1's framework choice |

**This repo** (`mq-gateway-replay-lab`, renamed in T17):

| Path | Responsibility |
|---|---|
| `src/gwlab/refapp/state.py` | SQLite instruction state store |
| `src/gwlab/refapp/retry.py` | SEME-preserving, `PDE`-marking retry |
| `src/gwlab/refapp/client.py` | Wire-protocol client; endpoint failover |
| `src/gwlab/refapp/receiver.py` | Inward listener; accept-and-record, ack |
| `src/gwlab/swift/trailers.py` | Block 5 trailer parse/emit (`PDE`) |
| `src/gwlab/swift/correlate.py` | **Modify** — arrival-order honesty (T5) |
| `tests/refapp/`, `tests/swift/test_trailers.py` | Tests |
| `ansible/roles/gw-gateway-deploy/` | Install binaries, units, config |
| `docs/mqget-failure-modes.md` | L6 |
| `docs/swift-message-ordering.md` | L9 |
| `docs/gateway-failure-envelope.md` | L2 |
| `docs/gateway-risk-model.md` | L11 |
| `docs/stateless-gateway-argument.md` | L4 |

---

## Task 0: Clear the ansible-lint baseline

**Deliverable:** `vrg-container-run -- vrg-validate` is green on `develop`.

**Files:**

- Modify: `ansible/roles/gw-observability/`, `gw-queues/`, `gw-sec-collector/`,
  `gw-sec-observability/`, `gw-sec-queues/`, `gw-sec-svc-queues/`,
  `gw-svc-queues/` — directory names and `handlers/main.yml` task names
- Modify: `ansible/roles/gw-sec-queues/defaults/main.yml:79,80,83,85,89`

**Interfaces:**

- Consumes: nothing
- Produces: a green baseline every later task depends on

- [ ] **Step 1: Reproduce and capture the full violation list**

```bash
cd <worktree> && vrg-container-run -- vrg-validate --check common 2>&1 | tee /tmp/lint-baseline.txt
```

Expected: 19 fatal violations — 7 `role-name`, 8 `yaml`, 4 `name[casing]`.

- [ ] **Step 2: Fix `yaml[commas]` (lowest risk, no references to update)**

Remove the extra spaces after commas at `gw-sec-queues/defaults/main.yml`
lines 79, 80, 83, 85, 89.

- [ ] **Step 3: Fix `name[casing]` — capitalise handler and task names**

```yaml
# before
- name: reload prometheus
# after
- name: Reload prometheus
```

Apply in `gw-observability/handlers/main.yml:2`,
`gw-sec-collector/handlers/main.yml:4`,
`gw-sec-observability/handlers/main.yml:2` and `:8`.

- [ ] **Step 4: Decide the `role-name` fix and record it**

`ansible-lint` requires `^[a-z][a-z0-9_]*$`; all seven roles use hyphens.
Renaming directories means updating every `roles:` reference in
`ansible/site*.yml` and any `--limit`/`--tags` documentation.

Two options — **choose and record the choice in the PR body**:
(a) rename `gw-observability` → `gw_observability` etc. and update all
references; (b) add a scoped `.ansible-lint` `skip_list` entry for `role-name`
with a comment explaining that the naming predates the profile bump.

Prefer (a) — it satisfies the profile rather than silencing it. Choose (b) only
if the reference sweep proves larger than the fix warrants, and say so.

- [ ] **Step 5: Verify green, then commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type fix --scope ansible --message "bring gw-* roles up to the ansible-lint production profile"
```

Expected: all six stages pass.

---

## Task 1 (L6): `MQGET` failure-mode analysis

**Deliverable:** `docs/mqget-failure-modes.md` — a standalone, publishable
reference, and the verdict that decides whether §4.5 holds.

**Files:**

- Create: `docs/mqget-failure-modes.md`

**Interfaces:**

- Consumes: nothing
- Produces: the verdict §4.5 and §5.2 depend on. **If a permanent,
  content-dependent failure reachable by a content-opaque consumer is found,
  §5.2 must reinstate poison handling and T8 changes.**

- [ ] **Step 1: Enumerate every documented `MQGET` reason code**

Source: IBM MQ 9.4 reason-code reference. Record each with its `MQCC`
completion code — the completion code is what separates a warning that still
returns the message from a genuine failure.

- [ ] **Step 2: Build the four-axis table**

Columns: reason code · meaning · `MQCC` · transient vs permanent ·
content-dependent vs not · **reachable when** (which `MQGMO` options expose
it) · **testability tier** (induced / mocked-only / cited).

Seed rows, already researched:

| Code | `MQCC` | Perm? | Content-dep? | Reachable when | Tier |
|---|---|---|---|---|---|
| 2080 `TRUNCATED_MSG_FAILED` | WARNING | No | Size only | Without `MQGMO_ACCEPT_TRUNCATED_MSG` | Induced |
| 2110 `FORMAT_ERROR` | WARNING | No | Yes | Only with `MQGMO_CONVERT` | Induced |
| 2119 `NOT_CONVERTED` | WARNING | No | Yes | Only with `MQGMO_CONVERT` | Induced |
| 2033 `NO_MSG_AVAILABLE` | FAILED | No | No | Always | Induced |
| 2009 `CONNECTION_BROKEN` | FAILED | No | No | Always | Induced |
| 2035 `NOT_AUTHORIZED` | FAILED | Yes | No (queue-level) | Always | Induced |
| 2016 `GET_INHIBITED` | FAILED | Yes | No (queue-level) | Always | Induced |
| 2101 `OBJECT_DAMAGED` | FAILED | Yes | No (queue-level) | Always | Cited |
| 2549 `CALL_INTERRUPTED` | FAILED | No | No | Reconnectable connection only | Mocked |

- [ ] **Step 3: Write the verdict section**

State the definition first: *a poison message is a failure that is permanent
**and** whose permanence depends on the message itself*. Then state whether
that cell is empty for a content-opaque consumer, and show the reasoning — 2080
and 2110/2119 are `MQCC_WARNING` and return the message; queue-level permanent
failures affect every message equally, so diverting one is meaningless and
halt-and-alert is correct.

- [ ] **Step 4: Add the preventions column**

For each avoidable condition, name the *architectural* prevention rather than
defensive code: content-opacity (never set `MQGMO_CONVERT`), buffer sizing plus
2080 resize-and-re-get, `MAXMSGL` declare-and-verify.

- [ ] **Step 5: Verify and commit**

```bash
vrg-container-run -- vrg-validate --check common
vrg-commit --type docs --scope mqget --message "MQGET failure-mode analysis: transient/permanent x content-dependent x reachability x testability"
```

---

## Task 2 (L9): SWIFT message-ordering reference

**Deliverable:** `docs/swift-message-ordering.md`. **Research already
complete** — this task writes it up.

**Files:**

- Create: `docs/swift-message-ordering.md`

**Interfaces:**

- Consumes: nothing
- Produces: the standards basis §5.2 and §11.4 already cite

- [ ] **Step 1: Write the three findings with their sources**

Findings and scope are recorded verbatim in spec §11.4; carry them across
unchanged:

1. No status-effective timestamp exists — MT548's A2 subsequence carries only
   `25D` and `24B`; `98a::PREP` is *"date/time at which the message was
   prepared"*.
2. No superseding rule between advices; no error code for an advice arriving
   before its predecessor.
3. ISN/OSN is session integrity — loss and duplicate detection recovered by
   retrieval (MT020 → MT021) — not end-to-end business ordering.

- [ ] **Step 2: State the scope of the negatives explicitly**

Two findings are negatives, so name the publications and editions searched:
*FIN System Messages* (`ufsm`), *FIN Error Codes* (`ufec`), and *Standards MT
Category 5 — Securities Markets, Message Reference Guide* (`us5mc`), all
2026-07-17. Link the SWIFT Knowledge Centre generally; do not use deep URLs,
which are session-gated and will fail for a reader.

- [ ] **Step 3: Record the vendor-usage trap**

A vendor usage note describes `PREP` as the time an order received its status.
That is local practice, not the ISO 15022 definition, and generalising it would
be an error. This is the most useful paragraph in the document for a working
implementer.

- [ ] **Step 4: State the consequence**

Ordering derives from status semantics — the `25D` state machine and linkage
references — never from a timestamp sort. Receivers must tolerate arbitrary
advice order.

- [ ] **Step 5: Verify and commit**

```bash
vrg-container-run -- vrg-validate --check common
vrg-commit --type docs --scope swift --message "SWIFT message ordering: guarantees, out-of-order handling, and why no timestamp key exists"
```

---

## Task 3 (L5): Duplicate-handling reference

**Deliverable:** `docs/swift-settlement-reference.md` extended with the
duplicate conventions it currently omits entirely.

**Files:**

- Modify: `docs/swift-settlement-reference.md`

**Interfaces:**

- Consumes: T2's findings (same standards, same editions)
- Produces: the standards basis for §4.2, and the behaviour T7 implements

- [ ] **Step 1: Add the three-layer table**

| Layer | Mechanism | Held by |
|---|---|---|
| FIN network (block 5) | `PDE` trailer, `{PDE:[<time><MIR>]}` | Sender, referencing its prior emission's MIR |
| ISO 15022 business | `:20C::SEME//` unique by instructing party; `23G` function | Sender |
| ISO 20022 (BAH) | `CpyDplct` (`CODU`/`COPY`/`DUPL`), `PssblDplct`, mandatory `Rltd` | Sender |

- [ ] **Step 2: Contrast `PDE` with `PDM`**

`PDE` is sender-added; `PDM` is system-added (`{PDM:[<time><MOR>]}`) when the
network redelivers. The standard distinguishes "the transport is redelivering"
from "the sender may be retransmitting", and only the latter is the sender's to
assert.

- [ ] **Step 3: State the operational reality**

`PDE`-marked messages are commonly routed to exception handling rather than
silently discarded. The claim to make is the **asymmetry** — a duplicate is
detectable, markable and recoverable; a lost message is none of those — not
that duplicates are harmless.

- [ ] **Step 4: State the rule the exemplar demonstrates**

**A retry must reuse the original SEME and mark `PDE`. A retry that mints a
fresh SEME is not a retry — it is a second settlement instruction.**

- [ ] **Step 5: Verify and commit**

```bash
vrg-container-run -- vrg-validate --check common
vrg-commit --type docs --scope swift --message "duplicate-handling conventions: PDE/PDM, SEME uniqueness, ISO 20022 CpyDplct"
```

---

## Task 4 (C1): Component repo bootstrap

**Deliverable:** `mq-protocol-gateway` exists, private, with a green Vergil C++
pipeline on an empty skeleton — **and a recorded `imqi.hpp` API surface** that
T7 and T8 consume.

**Files:**

- Create: `vergil.toml`, `CMakeLists.txt`, `README.md`, `CLAUDE.md`, `LICENSE`
- Create: `src/.gitkeep`, `tests/` (layout per the chosen framework)
- Create: `docs/mqi-api-surface.md`

**Interfaces:**

- Consumes: nothing
- Produces: the build/test/lint invocation, the test framework choice, and the
  **verified `imqi.hpp` signatures** every later C-task depends on. Until this
  lands, C2–C5 cannot be specified without inventing API details.

- [ ] **Step 1: Create the repo, private**

Confirm with the human before creating — this is an outward-facing action.
Private, per spec §5.5. Epic tasks link with
`vrg-issue-create --epic logical-minds-foundry/mq-gateway-replay-lab#268 --repo logical-minds-foundry/mq-protocol-gateway`.

- [ ] **Step 2: Write `vergil.toml` with the C++ profile**

```toml
[project]
repository-type = "tooling"
versioning-scheme = "semver"
branching-model = "library-release"
primary-language = "cpp"

[ci]
versions = ["clang-20", "gcc-15"]

[vm]
shared_from = "logical-minds-foundry/mq-resiliency-lab-for-linux"
```

Confirm the exact `primary-language` token and `[ci].versions` tag format
against the installed tooling before committing — the compiler-family prefix is
`<family>-<version>`.

- [ ] **Step 3: Minimal CMake skeleton with one trivially-true test**

The point is a green pipeline, not functionality. One test asserting `1 == 1`
proves the whole chain: configure, build, run, coverage, sanitisers.

- [ ] **Step 4: Run the full pipeline and fix until green**

```bash
vrg-container-run -- vrg-validate
```

Expected: clang-format, clang-tidy, cppcheck, the ASan/UBSan build and the
coverage build all pass. **This step is the actual deliverable** — it front-
loads every toolchain surprise before any gateway code exists.

- [ ] **Step 5: Record the `imqi.hpp` API surface**

On the lab box, read the shipped header and record in `docs/mqi-api-surface.md`
the exact signatures for: `ImqQueueManager` connect/disconnect and
`setConnectOptions`; `ImqQueue` open/close with `MQOO_*`; `ImqMessage` buffer
and descriptor accessors; `ImqGetMessageOptions` / `ImqPutMessageOptions`; and
`reasonCode()`/`completionCode()` on each.

**Also answer §7 Q1 here**: does the shipped header accept `MQCNO_RECONNECT`
via `setConnectOptions`? Record the header's own option list verbatim. This is
the empirical answer L3 needs, and it is cheapest to take while reading the
header anyway.

- [ ] **Step 6: Commit**

```bash
vrg-commit --type feat --scope build --message "bootstrap: Vergil C++ profile, CMake, green pipeline, recorded MQI API surface"
```

---

## Task 5 (L10): Make ordering semantics honest

**Deliverable:** No code or docstring implies MT548 carries a business ordering
key, and a shuffle test proves terminal state is order-independent.

**Files:**

- Modify: `src/gwlab/swift/correlate.py:31` (docstring), `:57` (docstring),
  `:79` (the sort — **keep it**, rename for clarity)
- Modify: any timeline/hop output presenting arrival time as event time
- Test: `tests/swift/test_correlate.py`

**Interfaces:**

- Consumes: T2's findings
- Produces: an executable guarantee that ordering is semantic, not temporal

- [ ] **Step 1: Write the failing shuffle test**

```python
import random
from gwlab.swift import correlate, lifecycle

def test_terminal_state_is_order_independent(sample_instruction, sample_responses):
    """Folding a shuffled response stream reaches the same terminal state.

    MT548 carries no status-effective timestamp (docs/swift-message-ordering.md),
    so ordering is semantic, not temporal. This test is the executable form of
    that finding: it fails loudly if anyone reintroduces an order dependency.
    """
    forward, _ = correlate.correlate([sample_instruction], sample_responses)
    expected = lifecycle.fold(forward[0]).state

    for seed in range(10):
        shuffled = list(sample_responses)
        random.Random(seed).shuffle(shuffled)
        got, _ = correlate.correlate([sample_instruction], shuffled)
        assert lifecycle.fold(got[0]).state == expected
```

- [ ] **Step 2: Run it**

Run: `vrg-container-run -- vrg-validate --check test`
Expected: PASS if the fold is genuinely monotonic. **A failure here is a real
finding** — it would mean the fold has an order dependency the spec claims it
does not, and §5.2 would need revisiting. Report rather than patch.

- [ ] **Step 3: Correct the docstrings**

`correlate.py:31` currently reads *"sorted by MQ put-time, so the lifecycle
fold can replay it in order"*, which implies business order. Replace with:

```python
    ``responses`` is sorted by **arrival order** (MQ put-time). That is the
    transport's clock, not a business timestamp — MT548 carries no
    status-effective time (docs/swift-message-ordering.md), so no business
    ordering key exists to sort on. Correctness comes from the monotonic fold,
    not from this order; the sort exists only so timelines read in the order we
    observed events.
```

- [ ] **Step 4: Audit the timeline output**

Grep for hop/timeline emission and confirm each timestamp is labelled as
observed arrival. Where a field name or label implies business event time,
rename it.

- [ ] **Step 5: Verify and commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type fix --scope correlate --message "arrival order is arrival order: no business ordering key exists in MT548"
```

---

## Task 6 (C4): Wire protocol and conformance corpus

**Deliverable:** The frame format, a C++ implementation, and a language-neutral
corpus both sides run.

**Files:**

- Create: `src/protocol/frame.{hpp,cpp}` (component repo)
- Create: `conformance/manifest.json`, `conformance/frames/*.bin`
- Test: component-repo unit tests driving the corpus

**Interfaces:**

- Consumes: T4's build and test framework
- Produces: the frame codec and the corpus that T7's client consumes

**Frame format** (spec §5.0 — trivial but specified):

```text
+--------+--------+------------------+
| type   | length | payload          |
| 1 byte | 4 byte | length bytes     |
|        | BE u32 |                  |
+--------+--------+------------------+

type: 0x01 REQUEST   0x02 RESPONSE   0x03 DELIVER   0x04 ACK
RESPONSE and ACK payloads begin with a 2-byte BE status code; 0 = success.
Maximum length must be >= the queue's MAXMSGL (declare-and-verify).
```

- [ ] **Step 1: Write the corpus manifest**

```json
{
  "cases": [
    {"name": "round-trip",     "file": "round-trip.bin",   "type": 1, "length": 11,      "payload_sha256": "...", "expect": "parse-ok"},
    {"name": "max-frame",      "file": "max-frame.bin",    "type": 1, "length": 4194304, "payload_sha256": "...", "expect": "parse-ok"},
    {"name": "zero-length",    "file": "zero-length.bin",  "type": 1, "length": 0,       "payload_sha256": "...", "expect": "parse-ok"},
    {"name": "ack-frame",      "file": "ack.bin",          "type": 4, "length": 2,       "status": 0,             "expect": "parse-ok"},
    {"name": "split-read",     "file": "split-read.bin",   "type": 1, "length": 11,      "split_at": 3,           "expect": "parse-ok-after-reassembly"}
  ]
}
```

Five cases, deliberately — this is a conformance check, not a protocol test
suite.

- [ ] **Step 2: Write the failing C++ corpus test**

A test that walks `conformance/manifest.json`, decodes each `.bin`, and asserts
the decoded type, length and payload hash match. `split-read` must be fed in
two chunks to prove the decoder reassembles rather than assuming one read
returns a whole frame.

- [ ] **Step 3: Run it — expect failure (no codec yet)**

- [ ] **Step 4: Implement `frame.{hpp,cpp}`**

Encode and decode only. **No socket I/O in this file** — that lives in
`transport.cpp` so the codec is testable without a network.

- [ ] **Step 5: Write the failing frame-size floor test**

```text
Given the configured maximum frame size
And the MAXMSGL of the reply queue it will carry
Then maximum frame size MUST be >= MAXMSGL
```

Assert it against the **actual** queue attribute, read from configuration
rather than hard-coded, so a later `MAXMSGL` increase fails the build.

This is the declare-and-verify assertion from spec §5.0, and it is checked
here rather than at drill time on purpose: it is a static relationship between
a protocol constant and a queue attribute. If the frame ceiling sat below
`MAXMSGL`, messages between the two limits would be undeliverable forever and
the receiving application would never see them — **a poison class manufactured
by the one layer specified to create none**. Catching that when the corpus is
built beats catching it during a drill, and an unverified assertion is exactly
what §4.6 objects to.

- [ ] **Step 6: Run until green, then commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type feat --scope protocol --message "wire frame codec, conformance corpus, and the MAXMSGL frame-size floor"
```

---

## Task 7 (L1): Reference sending application

**Deliverable:** A Python application that owns instruction state, retries with
SEME preserved and `PDE` set, and receives inbound deliveries.

**Files:**

- Create: `src/gwlab/refapp/{__init__,state,retry,client,receiver}.py`
- Create: `src/gwlab/swift/trailers.py`
- Test: `tests/refapp/`, `tests/swift/test_trailers.py`
- Modify: `src/gwlab/cli.py` — add a `refapp` subcommand

**Interfaces:**

- Consumes: T6's `conformance/manifest.json` (as a test fixture);
  T3's `PDE` rules
- Produces:
  - `state.record_prepared(seme: str, body: bytes) -> None`
  - `state.mark_sent(seme: str, msg_id: bytes) -> None`
  - `state.mark_failed(seme: str, reason: str) -> None`
  - `state.record_inbound(seme: str, body: bytes, valid: bool) -> None`
  - `retry.mark_pde(text: str, prior_mir: str | None) -> str`
  - `client.send(body: bytes, endpoints: list[tuple[str, int]]) -> int`

- [ ] **Step 1: Write the failing trailer test**

```python
from gwlab.swift.trailers import mark_pde, has_pde

def test_pde_trailer_is_added_to_block_5():
    original = "{1:F01BANKBEBB0000000000}{4:\n:20C::SEME//ABC123\n-}"
    retried = mark_pde(original, prior_mir=None)
    assert has_pde(retried)
    assert ":20C::SEME//ABC123" in retried, "a retry must preserve the original SEME"
```

- [ ] **Step 2: Run it — expect `ModuleNotFoundError`**

- [ ] **Step 3: Implement `trailers.py`**

Parse and emit block 5. `mark_pde` appends `{5:{PDE:}}` (or merges into an
existing block 5) and **must not touch block 4**, so the SEME is preserved by
construction rather than by care.

- [ ] **Step 4: Run until green**

- [ ] **Step 5: Write the failing durability test**

```python
def test_ack_is_not_sent_before_the_record_is_committed(tmp_path):
    """The ack means durable custody (spec 5.0). Committing after acking would
    make it a lie that MQ acts on."""
    store = state.Store(tmp_path / "refapp.db")
    store.record_inbound("SEME1", b"body", valid=True)
    reopened = state.Store(tmp_path / "refapp.db")
    assert reopened.get("SEME1") is not None
```

- [ ] **Step 6: Implement `state.py` on SQLite**

Single writer. Every mutation inside an explicit transaction, committed before
the caller may ack.

- [ ] **Step 7: Write the failing invalid-message test**

```python
def test_unparseable_message_is_recorded_and_acked_not_refused():
    """Spec 4.5: the receiver must never refuse a message on content grounds."""
    result = receiver.handle(b"\xff\xfe not swift at all")
    assert result.acked is True
    assert result.recorded_invalid is True
```

- [ ] **Step 8: Implement `receiver.py` and `client.py`**

`client.send` randomises the endpoint list, tries in order, falls through on
connection failure or non-zero status. `receiver.handle` accepts, records, and
acks — never refuses on content.

- [ ] **Step 9: Add the corpus conformance test**

Walk the same `conformance/manifest.json` from T6 and assert the Python codec
agrees. **This is the point of the corpus** — divergence fails a build.

- [ ] **Step 10: Verify and commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type feat --scope refapp --message "reference sending app: SQLite state, SEME-preserving PDE retry, accept-and-record receiver"
```

---

## Task 8 (C3): Inbound gateway

**Deliverable:** The ack-gated consumer with exclusive-open standby and the
exported state model.

**Files:**

- Create: `src/inbound/main.cpp`, `src/inbound/state.{hpp,cpp}`
- Create: `src/mqi/session.{hpp,cpp}`
- Test: component-repo unit tests against the mocked MQI seam

**Interfaces:**

- Consumes: T4's recorded API surface; T6's frame codec
- Produces: the binary T10 deploys and T12 measures, plus **the status stream
  T11 consumes** — an append-only file of single-line JSON records, one per
  state change *and* one per heartbeat interval:

```json
{"ts":"2026-08-20T11:04:22.417Z","instance":"inbound-2","state":"STANDBY","reason":"2042","seq":1487}
```

  `state` ∈ `CONNECTING_APP` | `STANDBY` | `ACTIVE` | `FAILED`; `seq` is a
  monotonic per-instance counter so the collector can detect gaps and restarts.
  The gateway knows nothing about Prometheus, alarms, or thresholds (spec §10).

- [ ] **Step 1: Write the failing startup-order test**

```text
Given the receiving application is unreachable
When the gateway starts
Then it must NOT open the queue
And its state must be CONNECTING_APP
```

This is the single most important test in the task — it is what prevents the
partial-failure deadlock (spec §5.6).

- [ ] **Step 2: Write the failing standby test**

```text
Given the queue is already open for input
When this instance attempts MQOO_INPUT_EXCLUSIVE
Then it receives 2042
And its state becomes STANDBY
And it retries on a jittered interval without exiting
```

- [ ] **Step 3: Write the failing ack-gating test**

```text
Given a message has been got under syncpoint
When the receiver does not acknowledge within the bounded window
Then the GET is backed out
And the consumer halts and circuit-breaks
And MQCMIT is never called
```

- [ ] **Step 4: Implement `session.cpp` with the mockable seam**

The seam is the point: every MQI call goes through an interface whose test
double can return an arbitrary reason code. Without it the §9 *mocked* tier —
2549 especially — is unreachable, and the spec commits to proving those paths.

- [ ] **Step 5: Implement the state machine and the get-loop**

Order: connect inward → connect MQ → open `MQOO_INPUT_EXCLUSIVE` → loop.
Content-opaque: never set `MQGMO_CONVERT`. Handle 2080 by reading `DataLength`,
resizing and re-getting. Back out **only** when the receiver is unreachable.
Abort if the inward connection cannot be recovered within its bounded window.

- [ ] **Step 6: Write the failing heartbeat test**

```text
Given the instance has been in STANDBY for three heartbeat intervals
And its state has not changed
When the status stream is read
Then it contains at least three records for this instance
And their seq values are strictly increasing
```

**This is not optional decoration.** A scraped endpoint reports a dead process
automatically — the scrape fails. A file does not: a crashed gateway leaves a
final record saying `ACTIVE` and nothing ever contradicts it, so the "no
instance `ACTIVE`" alarm would never fire in precisely the failure it exists to
detect. The heartbeat is what makes staleness detectable.

- [ ] **Step 7: Implement the status writer**

One line of JSON per state change and per heartbeat interval, appended and
flushed. No Prometheus, no thresholds, no alarm logic — the gateway does not
know what is downstream of it (spec §10).

- [ ] **Step 8: Run until green, then commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type feat --scope inbound --message "inbound gateway: ack-gated commit, exclusive-open standby, JSON status stream"
```

---

## Task 9 (C2): Outbound gateway

**Deliverable:** The protocol translator — one request, one put, one
deterministic outcome.

**Files:**

- Create: `src/outbound/main.cpp`
- Test: component-repo unit tests against the mocked MQI seam

**Interfaces:**

- Consumes: T4's API surface; T6's codec; T8's `session` seam
- Produces: the binary T11 deploys

- [ ] **Step 1: Write the failing open-once test**

```text
Given the gateway has started
When it handles 100 requests
Then MQOPEN is called exactly once
```

Directly refutes the per-message-open anti-pattern, and the assertion is
trivially checkable through the mocked seam.

- [ ] **Step 2: Write the failing deterministic-outcome test**

```text
Given MQPUT returns a failure reason code
When the request is handled
Then the response status is non-zero
And the reason code is surfaced to the caller
And no state is retained anywhere in the gateway
```

- [ ] **Step 3: Implement**

Connect once, open once, one put per request, return the outcome. No state, no
replay, no custody.

- [ ] **Step 4: Run until green, then commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type feat --scope outbound --message "outbound gateway: open-once translator with deterministic outcomes"
```

---

## Task 10 (L7 + L13): Lab deployment — **deployment task**

**Deliverable:** Binaries installed and running in the lab; three inbound
contenders, multiple outbound instances.

**Kind:** `deployment` — run with `vergil:issue-deploy`, closes only on
`Outcome: SUCCESS`. Not PR-workable.

**Files:**

- Create: `ansible/roles/gw-gateway-deploy/{tasks,templates,defaults}/`
- Modify: `ansible/site.yml`

**Interfaces:**

- Consumes: T8, T9 binaries
- Produces: a running deployment T12 measures

**Blocked-by:** T8, T9

- [ ] **Step 1: Precondition self-check**

Confirm binaries build on the box, the lab queue managers are up, and
`GW.SEC.REPLY` exists. If unmet: comment "blocked: preconditions not met" and
stop. Never fabricate.

- [ ] **Step 2: Write the Ansible role**

Install binaries, systemd units, and config. Three inbound units and two
outbound units. **Process-level, not host-level** — the inventory has one host
per group, and the queue manager arbitrates by connection rather than host, so
this is faithful (spec §5.6).

- [ ] **Step 3: Wire into `site.yml` so the committed deploy matches live**

Per the self-owning bolt-on rule, every lab change is automated and owned.

- [ ] **Step 4: Deploy and verify state**

Expected: exactly one inbound in `ACTIVE`, two in `STANDBY`, outbound
instances accepting requests.

- [ ] **Step 5: Record the outcome as a comment**

`Outcome: SUCCESS` with the observed states, or `Outcome: FAILURE` with what
was seen. The task stays open on failure.

---

## Task 11 (L14): Gateway observability

**Deliverable:** State and alarms exported into the existing Prometheus/Grafana
tier.

**Files:**

- Create: `src/gwlab/gateway_exporter.py`
- Modify: `ansible/roles/gw-sec-observability/` — scrape config
- Test: `tests/test_gateway_exporter.py`

**Interfaces:**

- Consumes: T8's **status stream** — the append-only single-line-JSON file
  whose record schema is fixed in T8's Produces block. The collector tails it,
  reopening on inode change so rotation and truncation do not silently stop the
  feed.
- Produces: the signals T12's scenarios verify

- [ ] **Step 1: Write the failing metric test**

```python
def test_standby_is_not_an_alarm_but_missing_active_is():
    """Three conditions look like 'not consuming' from outside (spec 5.6).
    A healthy standby is one of them, so idleness cannot be the alarm."""
    text = render({"i1": "ACTIVE", "i2": "STANDBY", "i3": "STANDBY"})
    assert 'gw_inbound_state{instance="i2",state="STANDBY"} 1' in text
    assert "gw_inbound_active_total 1" in text
```

- [ ] **Step 2: Write the failing staleness test**

```python
def test_a_stale_record_is_UNKNOWN_not_its_last_known_state():
    """A crashed gateway leaves a final record saying ACTIVE. Trusting it would
    make 'no instance ACTIVE' unfirable in exactly the failure it detects."""
    records = [
        {"ts": "2026-08-20T11:00:00Z", "instance": "i1", "state": "ACTIVE",  "seq": 10},
        {"ts": "2026-08-20T11:04:50Z", "instance": "i2", "state": "STANDBY", "seq": 99},
    ]
    text = render(records, now="2026-08-20T11:05:00Z", heartbeat_s=10, stale_after=3)
    assert 'gw_inbound_state{instance="i1",state="UNKNOWN"} 1' in text
    assert 'gw_inbound_state{instance="i1",state="ACTIVE"} 0' in text
    assert "gw_inbound_active_total 0" in text
```

- [ ] **Step 3: Implement the collector**

Tail the status file, reopening on inode change so rotation and truncation do
not silently stop the feed. Keep the newest record per instance. Any instance
whose newest record is older than `stale_after × heartbeat_s` renders as
`UNKNOWN` — **never as its last-known state**. Gauges per instance per state,
plus `gw_inbound_active_total` and `gw_inbound_standby_total`.

- [ ] **Step 4: Add the four alarms**

No instance `ACTIVE`; standby count below expected (**silently degraded
redundancy — the failure that hides until it matters**); any instance in
`CONNECTING_APP`; any instance `UNKNOWN` (stale — died without saying so).

- [ ] **Step 5: Verify and commit**

```bash
vrg-container-run -- vrg-validate
vrg-commit --type feat --scope observability --message "gateway state model and alarms"
```

---

## Task 12 (L2 + L12): Failure-envelope validation — **validation task**

**Deliverable:** Every §6 row exercised at its §9 tier, with observations
recorded; `docs/gateway-failure-envelope.md` written from what was seen.

**Kind:** `validation` — run with `vergil:issue-validate`.

**Blocked-by:** T10, T11

- [ ] **Step 1: Precondition self-check** — deployment healthy, three inbound
      instances in the expected states.

- [ ] **Step 2: Induce the outbound rows (O1–O4)** and record reason codes.

- [ ] **Step 3: Induce the inbound rows (I1–I4)**, including the unparseable
      message that must be absorbed with **no backout and no queue growth**.

- [ ] **Step 4: Severed inward route** — break only the active instance's route
      to the receiving application. Expected: fault reported, bounded window
      exhausted, abort, standby takes over. **Stronger than killing the
      process**, and the case the design was shaped to survive.

- [ ] **Step 5: Measure failover** — with `HBINT` at 300s default and tuned, so
      the handle-release contribution is isolated from the retry interval.
      Record both numbers; they are the headline availability figures and the
      MTTR input to T14.

- [ ] **Step 6: Restart-into-2042** — kill the only instance, restart
      immediately, confirm recovery by retry.

- [ ] **Step 7: Custody survives receiver restart** — kill the receiver
      immediately after it acks; confirm the record survived.

- [ ] **Step 8: Inbound throughput ceiling** — measure the serial rate and
      state it against expected reply volume, with margin shown.

- [ ] **Step 8a: Downstream outage that must divert nothing.** Take the
      receiving application down for **longer than any plausible backout
      threshold would have been** — duration is the whole point, since a short
      outage does not test this. Required observation: the consumer halts and
      backs off, queue depth grows, and **no message is diverted anywhere**. On
      recovery the flow resumes with nothing lost and nothing duplicated beyond
      what §6 I2 predicts. **This is the scenario that validates §4.5** — the
      empirical proof that removing `BOTHRESH`/`BOQNAME` was safe rather than
      merely convenient.

- [ ] **Step 8b: Oversized message.** Put a message larger than the gateway's
      initial `MQGET` buffer. Confirm the 2080 path reads `DataLength`, resizes
      and re-gets, and that the message is delivered rather than stuck. (The
      static `MAXMSGL` ≥ frame-size assertion is checked at build time in T6
      step 5, not here.)

- [ ] **Step 8c: Start with the inward route already broken.** Bring an
      instance up with no route to the receiving application. Confirm it never
      opens the queue, reports `CONNECTING_APP` in the T11 stream, and does not
      become a contender — i.e. a healthy standby still wins the open. T8
      step 1 proves the code path; this proves the *deployed* instance behaves
      that way, which is a claim about configuration rather than logic.

- [ ] **Step 9: Write the document, tagging every row with its tier**
      (induced / mocked / cited), and record `Outcome: SUCCESS` or `FAILURE`.
      **Blocked on T12b** — the O3/I4 rows describe connection-interruption
      windows, and the failover drills are what actually enter them. Writing
      the envelope before T12b would document windows nobody visited.

---

## Task 12b: HA failover and DR drills — **validation task**

**Deliverable:** Both gateways exercised across a Native HA failover and a DR
event, with observations recorded — including the empirical answer to §7 Q2.

**Kind:** `validation` — run with `vergil:issue-validate`.

**Blocked-by:** T10, T11. **Blocks T12 step 9** (the envelope document cannot
be written without these observations) and **T13** (Q2's answer comes from
here).

Split from T12 deliberately. Drills are **re-run** as the gateway changes, and
a task that also writes a document cannot be re-run cleanly. Keeping this
separate also gives a partial failure somewhere granular to land: an eleven-
step task reduces to one `Outcome: SUCCESS` comment that hides which scenario
failed.

- [ ] **Step 1: Precondition self-check**

Deployment healthy with one inbound `ACTIVE` and two `STANDBY`; outbound
instances accepting requests; the base lab's HA/DR drill machinery available;
a traffic generator able to sustain flow on both paths. If unmet: comment
"blocked: preconditions not met" and stop. Never fabricate a result.

- [ ] **Step 2: Establish a measured baseline**

Start steady traffic on both paths. Record the rate, the instance states, and
the queue depths **before** touching anything — a drill without a baseline
produces anecdotes rather than measurements, and these results must be
comparable across re-runs.

- [ ] **Step 3: Native HA failover with traffic in flight**

Fail over the app-facing queue manager while both paths are actively carrying
messages. Record for each gateway:

- the reason code(s) observed, verbatim;
- whether any message was **lost** (the answer must be none);
- whether any message was **duplicated**, and whether it matches what §6 I2
  predicts;
- the state transitions each instance passed through, from the T11 stream;
- wall-clock time until each path resumed.

- [ ] **Step 4: Capture the Q2 answer deliberately**

§7 Q2's working hypothesis is that **reconnection re-establishes a connection,
never a unit of work**. Determine it by observation, not inference: was an
in-flight unit of work preserved across the failover, or rolled back?

This is the single most valuable observation in the task. If the hypothesis
holds, the recovery logic is mandatory regardless of API, the C++ classes cost
boilerplate rather than correctness, and §7 Q1 stops being load-bearing.
Record what was seen even if it contradicts the hypothesis — **especially**
then.

- [ ] **Step 5: DR event**

Repeat steps 3 and 4 for a DR scenario. Record the same observations, plus any
behaviour that differs from the HA case.

- [ ] **Step 6: Reconcile against §6**

Confirm or correct the O3 and I4 rows, and record **which reason code actually
appeared** — 2549 or 2009. That resolves the §6 note left open pending §7 Q1
and feeds T13.

- [ ] **Step 7: Record parameters and outcome**

Record the drill parameters used (`HBINT`, retry intervals, traffic rate) so a
re-run is comparable, then `Outcome: SUCCESS` with the observations, or
`Outcome: FAILURE` with what was seen. The task stays open on failure.

---

## Task 13 (L3): API findings

**Deliverable:** `docs/mqi-cpp-api-findings.md` — Q1 and Q2 answered
empirically.

**Blocked-by:** T4 (which records the header), **T12b** (which observes the
codes under failover — the only condition that produces them)

- [ ] **Step 1: State Q1's answer from the shipped header** — does
      `setConnectOptions` accept `MQCNO_RECONNECT`? Quote the header's option
      list.

- [ ] **Step 2: State Q2's answer from observation** — did reconnection
      preserve a unit of work, or only a connection? The working hypothesis is
      the latter; report what was seen either way.

- [ ] **Step 3: Finalise §6's O3/I4 rows** — 2549 or 2009, per Q1.

- [ ] **Step 4: Record the XMS adoption note** — SupportPac IA94, a separate
      download rather than a base-product component. Do **not** assert
      stabilisation of the C++ classes; that statement is documented for the
      *Java* classes only.

- [ ] **Step 5: Verify and commit.**

---

## Task 14 (L11): Risk model

**Deliverable:** `docs/gateway-risk-model.md` — the arithmetic, with
lab-measured inputs and their business parameters as named variables.

**Blocked-by:** T12 (supplies MTTR)

- [ ] **Step 1: Define the named variables** — message volume, host MTBF,
      measured failover (from T12), tolerable stall, cost per duplicate, cost
      per lost message. **Never invent their values.**

- [ ] **Step 2: Write the exposure-window model** — two nodes are redundant
      only until the first failure; three require two failures inside the
      repair window, so the improvement is roughly quadratic.

- [ ] **Step 3: State the independence assumption explicitly** — the quadratic
      benefit assumes independent failure domains, and co-located processes do
      not have them. **Omitting this would make the document the hand-waving
      §4.6 objects to.**

- [ ] **Step 4: Work the duplicate-versus-loss asymmetry** — expected
      duplicates per year under stateless retry, against the cost of one lost
      message.

- [ ] **Step 5: State the threshold** at which added gateway complexity earns
      its place, and show this design falls below it.

- [ ] **Step 6: Verify and commit.**

---

## Task 15 (L4): The argument document

**Deliverable:** `docs/stateless-gateway-argument.md` — the reader-facing
writeup.

**Blocked-by:** T12, T14

- [ ] **Step 1: Lead with the thesis** — the gateway is a protocol layer
      factored out of an already-stateful application.

- [ ] **Step 2: Carry the six arguments**, each with its evidence.

- [ ] **Step 3: Concede accurately.** Outbound syncpoint is defensible; the
      shared open is not a defect. **An argument that criticises everything
      loses to one that concedes precisely** — and these concessions are what
      buy the get-path critique its credibility.

- [ ] **Step 4: State the irreducible case plainly** — without XA a caller can
      be told "failed" for a message that went. The answer is `PDE`, not
      concealment.

- [ ] **Step 5: Close on availability** — the recommendation removes a tier
      *without* removing availability, and improves it via standby.

- [ ] **Step 6: Verify and commit.**

---

## Task 16 (C5): Counter-example — **deferrable**

**Deliverable:** The stateful gateway, demonstrating it does not remove the
window it was built to remove.

**Blocked-by:** T9, T12

- [ ] **Step 1: Build the stateful variant** — records intent, replays on
      restart. Generic exemplar only; **never a reproduction of shared source**.

- [ ] **Step 2: Run the O4 scenario against it** — put succeeds, response to
      caller lost.

- [ ] **Step 3: Demonstrate the window survives** — the caller still cannot
      distinguish acceptance from a lost response. The state changed nothing.

- [ ] **Step 4: Add the named anti-patterns** from §5.4 as runnable
      demonstrations: per-message open, get outside syncpoint.

- [ ] **Step 5: Record results and commit.**

---

## Task 17 (L8): Repo rename

**Deliverable:** `mq-gateway-replay-lab` → `mq-protocol-gateway-lab`.

Full checklist and measured blast radius are in issue **#272** — 20 references
across 14 files here, none in the base lab, and one Claude project directory
keyed to the old path. Sequenced late to avoid churning in-flight branches.

---

## Task 18: Closing bookends

- **#270 — documentation review.** A sweep, not one edit. Spawn per-repo doc
  tasks where other repos need changes; each closed by a same-repo PR.
- **#271 — retrospective.** Terminal. `epic-retrospective` refuses to run until
  every other child is closed; its merge closes the epic.

---

## Self-Review

**Spec coverage.** C1→T4, C2→T9, C3→T8, C4→T6, C5→T16; L1→T7, L2→T12, L3→T13,
L4→T15, L5→T3, L6→T1, L7→T10, L8→T17, L9→T2, L10→T5, L11→T14, L12→T12,
L13→T10, L14→T11. Plus T0 for the known-red baseline and T18 for the bookends.
**All 19 deliverables are covered.**

**§9 scenario coverage**, checked separately because deliverable coverage did
not imply it — the first alignment pass found four §9 scenarios with no step:

| §9 scenario | Task |
|---|---|
| Induce each envelope row | T12 step 2–3 |
| Unparseable message absorbed | T12 step 3 |
| Severed inward route | T12 step 4 |
| Standby takeover, `HBINT` tuned and default | T12 step 5 |
| Restart-into-2042 | T12 step 6 |
| Custody survives receiver restart | T12 step 7 |
| Inbound throughput ceiling | T12 step 8 |
| **HA failover and DR event** | **T12b** |
| Downstream outage diverting nothing | T12 step 8a |
| Oversized message — 2080 resize live | T12 step 8b |
| `MAXMSGL` ≥ frame size — static assertion | **T6 step 5** (build time, not drill time) |
| Start with inward route broken (live) | T12 step 8c; T8 step 1 (unit) |

**All eleven §9 scenarios are now covered.** The split of the oversized-message
requirement is deliberate: the static frame-size floor is a build-time
assertion, while the 2080 resize path needs a live message.

**Known gaps, stated rather than hidden:**

1. **T8 and T9 specify behaviour and tests, not literal `imqi.hpp` call
   sequences.** T4 records the verified API surface first, because inventing
   signatures for an unread header is precisely the error §11.4 documents.
   Executors of T8/T9 read `docs/mqi-api-surface.md` from T4.
2. **T4's `vergil.toml` tokens need confirming** against the installed tooling
   before commit; the values above are the expected shape, not verified strings.
3. **The corpus `payload_sha256` values are generated in T6**, not invented
   here.

**Type consistency.** `state.record_prepared` / `mark_sent` / `mark_failed` /
`record_inbound`, `retry.mark_pde`, `client.send`, `receiver.handle` are used
consistently in T7 and referenced nowhere else. `gw_inbound_state`,
`gw_inbound_active_total`, `gw_inbound_standby_total` appear only in T11.

**Falsifiability, preserved deliberately.** If T1 finds a permanent,
content-dependent `MQGET` failure reachable by a content-opaque consumer, §4.5
weakens and T8 must reinstate poison handling. If T5's shuffle test fails, the
fold has an order dependency the spec denies. Both are reported, not patched.
