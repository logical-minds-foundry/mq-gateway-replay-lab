# Confirmation Tracking v2 — SWIFT MT540–548 settlement-lifecycle tracking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a live-runnable, reporting-only observability tool that parses SWIFT MT540–548 message bodies, correlates each response to its instruction by reference, drives every instruction through a 6-state settlement lifecycle, and surfaces state distribution, lifecycle latencies, attention sets, and anomalies over a multi-day settlement window.

**Architecture:** A parallel `gwlab.swift` subpackage (v1 untouched) with a **data-driven spine**: a generic ISO 15022 block-4 tokenizer, a field-spec catalog and a 6-state transition/reachability graph — all sourced by a **gating reference spike** and consumed as data by the parser, the fold, and the corpus. A read-only collector browses two `GW.SEC.*` streaming-audit queues (reusing the proven v1 streaming pattern and the `Messaging` protocol), folds each instruction's response stream monotonically along the reachability graph, and scopes by the open settlement window. Its own state-machine dashboard and CLI subcommands run beside v1's.

**Tech Stack:** Python 3.12, pytest, the existing `gwlab` `Messaging`/`config` layer (`fake_messaging` ↔ `pymqi_messaging`), Prometheus exposition + the existing dashboard/exporter scaffolding, MQSC + Ansible for the `GW.SEC.*` objects. Validation is `vrg-container-run -- vrg-validate` **only**.

## Global Constraints

Every task's requirements implicitly include these (verbatim from the spec):

- **100% public, zero proprietary.** No client-identifiable content anywhere; generic vocabulary only (Counterparty A/B, invented `GW.SEC.*` names). No copyrighted SWIFT MT Reference Guide text — formats implemented from public renderings.
- **MT540–548 only.** ISO 20022 XML is a documented mapping in the deliverable, **not** built.
- **Reporting / observability only.** No remediation/replay/resend/re-instruction action; no putter on any live path. v1's `recovery.py` is **not** carried forward.
- **Documented to the letter, via the gating spike.** No transition, qualifier, or field semantic is invented. Every `(verify)` value is resolved by Task 1 against a **cited public source**, applying the source-fallback policy (cited free MT rendering → free ISO 20022 code list with a *labelled* 15022↔20022 mapping → else mark unsupported and omit). Nothing asserted from memory.
- **Account-owner viewpoint.** We instruct (MT540–543); the counterparty (account servicer/CSD) acknowledges/matches/confirms (MT548, MT544–547).
- **Settlement-window scoped, not calendar-day.** Retention (`CAPEXPRY`) and reporting follow a configured settlement horizon (≈ T+2/T+3 + fails buffer), not v1's 24 h.
- **No silent failures.** Malformed / undocumented-qualifier / illegal-transition / orphan-reference → flagged, logged with the offending reference, never swallowed, never a hard crash of the run.
- **v1 is not touched.** New code lives under `src/gwlab/swift/`; the existing modules are read-only reuse.
- **Parasite rule.** v2 owns only its `GW.SEC.*` queues; never mutate or restart the lab QM; declare-and-verify what it doesn't own.
- **Repo conventions.** `vrg-git`/`vrg-gh`/`vrg-commit` wrappers; work on the assigned feature-branch worktree; `vrg-validate` is the only validation command.

---

## File Structure

```
src/gwlab/swift/
  __init__.py
  reference.py     [Task 1]  data tables produced/curated by the spike:
                             States, TRANSITIONS, REACHABILITY, FIELD_CATALOG,
                             STATUS_DOMAINS — plus a self-consistency validator
  tokenizer.py     [Task 2]  ISO 15022 block-4 text → FieldTree (blocks + fields)
  fields.py        [Task 3]  validate a FieldTree against reference.FIELD_CATALOG
  model.py         [Task 4]  FieldTree → SwiftMessage (mt, seme, rela, statuses, put_time)
  correlate.py     [Task 5]  group SwiftMessages by SEME/RELA; orphan detection
  lifecycle.py     [Task 6]  the monotonic fold along REACHABILITY → InstructionState
  window.py        [Task 7a] settlement-window scoping (in-scope / aged-out)
  collect.py       [Task 7b] read-only collector: browse both audits → DayReport
  observe.py       [Task 8]  metrics: state distribution, latencies, attention sets, anomalies
  corpus.py        [Task 9]  generate format-valid message flows, one per lifecycle path
  responder.py     [Task 10] emit realistic 548 / 544–547 sequences for a driven instruction
  simulation.py    [Task 11] multi-day settlement-window simulation driver
  dashboard.py     [Task 12] state-machine board (HTML/artifact)
  cliswift.py      [Task 13] `gwlab swift …` subcommands + exporter wiring
  swiftconfig.py   [Task 5/7] SwiftQueueSet (GW.SEC.* names) + horizon config

docs/
  swift-settlement-reference.md   [Task 1]  the cited reference (human-reviewed)
  mq-native-settlement-tracking.md [Task 14] the v2 deliverable design markdown

ansible/                          [Task 15] gw-sec-queues role (GW.SEC.* MQSC)
tests/swift/                      one test module per source module above
```

Each `src/gwlab/swift/<mod>.py` has a sibling `tests/swift/test_<mod>.py`. Files that change together live together; each module has one responsibility.

---

## Phase 0 — Reference grounding (gating)

### Task 1: Reference-grounding spike — the cited SWIFT-settlement reference

**Not a TDD code task — a research + data-curation task that GATES all others.** Its output is human-reviewed before any downstream code is written (spec §2, §16.1).

**Files:**
- Create: `docs/swift-settlement-reference.md` (prose + citations)
- Create: `src/gwlab/swift/reference.py` (the machine-usable tables)
- Create: `src/gwlab/swift/__init__.py` (empty package marker)
- Test: `tests/swift/test_reference.py` (self-consistency only — not semantics)

**Interfaces (Produces — every later task consumes this):**
```python
# reference.py
from enum import Enum

class State(str, Enum):
    INSTRUCTED = "instructed"
    ACKNOWLEDGED = "acknowledged"
    MATCHED = "matched"
    SETTLED = "settled"       # terminal +
    REJECTED = "rejected"     # terminal -
    FAILED = "failed"         # terminal -

TERMINAL: frozenset[State]                 # {SETTLED, REJECTED, FAILED}

# A trigger is what a parsed message contributes: either a message-type fact
# (an MT544-547 confirmation) or a 548 status qualifier (:25D: block).
# Produced by the spike, each row CITED in the markdown.
#   key:   ("mt", "544") | ("25D", "<qualifier>", "<value>")
#   value: the State it asserts
TRIGGERS: dict[tuple[str, ...], State]

# Directed reachability: from-state -> set of states a single trigger may advance to.
# Encodes the spike's finding on which in-flight states may drop to REJECTED/FAILED
# and whether skips are permitted. The fold (Task 6) advances ONLY along these edges.
REACHABILITY: dict[State, frozenset[State]]

# Per field/qualifier we consume: format spec, char-set, optional enumerated domain.
FIELD_CATALOG: dict[str, "FieldSpec"]      # keyed by tag+qualifier, e.g. ":20C::SEME"
STATUS_DOMAINS: dict[str, frozenset[str]]  # e.g. "25D:IPRC" -> {"PACK","REJT",...}

# Provenance: every TRIGGERS / STATUS_DOMAINS entry maps to a citation id present
# in docs/swift-settlement-reference.md. Inferred (ISO 20022-mapped) rows are
# marked source="inferred-iso20022".
CITATIONS: dict  # entry-key -> {"source": <url|"inferred-iso20022">, "note": str}
```

- [ ] **Step 1: Research the public sources and draft the reference markdown.** From the cited sources (spec §17: iotafinance MT540–548 renderings; the ISO 20022 free code lists/XSDs), fill `docs/swift-settlement-reference.md` with: the MT540–548 family roles; the block structure (`:16R:`/`:16S:` GENL, LINK, STAT, SETDET); the correlation fields (`:20C::SEME//`, `:20C::RELA//`, and whether `:20C::PREV//` participates); the `:25D:` status qualifiers and their value domains (IPRC/MTCH/SETT/…); the `:24B:` reason vocabulary; and the **reachability graph**. Each concrete value carries a citation. Apply the **source-fallback policy** for every gap and mark inferred rows explicitly.

- [ ] **Step 2: Encode the tables in `reference.py`.** Transcribe the markdown's findings into `State`, `TERMINAL`, `TRIGGERS`, `REACHABILITY`, `FIELD_CATALOG`, `STATUS_DOMAINS`, `CITATIONS`. No value appears in `reference.py` that is not backed by a `CITATIONS` entry.

- [ ] **Step 3: Write the self-consistency test.**
```python
# tests/swift/test_reference.py
from gwlab.swift import reference as ref

def test_every_trigger_targets_a_known_state():
    for state in ref.TRIGGERS.values():
        assert isinstance(state, ref.State)

def test_reachability_is_well_formed():
    # every edge endpoint is a real state; terminals have no out-edges
    for src, dsts in ref.REACHABILITY.items():
        assert isinstance(src, ref.State)
        for d in dsts:
            assert isinstance(d, ref.State)
    for term in ref.TERMINAL:
        assert ref.REACHABILITY.get(term, frozenset()) == frozenset()

def test_every_encoded_value_is_cited():
    for key in list(ref.TRIGGERS) + [f"25D:{q}" for q in ref.STATUS_DOMAINS]:
        assert key in ref.CITATIONS, f"uncited reference value: {key}"

def test_instructed_reaches_only_documented_next_states():
    assert ref.State.ACKNOWLEDGED in ref.REACHABILITY[ref.State.INSTRUCTED]
```

- [ ] **Step 4: Run the tests.** `vrg-container-run -- vrg-validate` — expect PASS (self-consistency only).

- [ ] **Step 5: Commit and STOP for human review.**
```bash
vrg-commit --type docs --scope epic-65 --message "reference-grounding spike: cited SWIFT settlement reference + tables"
```
**Gate:** the reachability graph and citations are human-reviewed (spec §4.3) before Phase 1 begins. Do not proceed until approved.

---

## Phase 1 — Parsing

### Task 2: ISO 15022 block-4 tokenizer

**Files:** Create `src/gwlab/swift/tokenizer.py`; Test `tests/swift/test_tokenizer.py`

**Interfaces:**
- Consumes: nothing (pure text).
- Produces:
```python
@dataclass(frozen=True)
class Field:
    tag: str            # "20C", "25D", "16R", ...
    qualifier: str | None   # "SEME", "RELA", "IPRC", ... (the :4!c after the tag) or None
    value: str          # raw value after the qualifier (or after the tag if none)

@dataclass(frozen=True)
class Block:
    name: str           # GENL, LINK, STAT, SETDET, ...
    fields: list[Field]
    subblocks: list["Block"]

@dataclass(frozen=True)
class FieldTree:
    blocks: list[Block]     # top-level blocks parsed from the message text
    def find(self, tag: str, qualifier: str | None = None) -> list[Field]: ...

def tokenize(text: str) -> FieldTree: ...   # raises TokenizeError on unparseable structure
class TokenizeError(ValueError): ...

# Envelope parsing (Issue [1]): read the MT number from Block 2 and return the
# block-4 payload to hand to tokenize(). The MT type is NOT in block 4, so this is
# the single owner of "which MT is this".
def parse_envelope(text: str) -> tuple[str, str]: ...   # -> (mt, block4_text); raises TokenizeError
```

- [ ] **Step 1: Failing test — a minimal GENL block with SEME.**
```python
# tests/swift/test_tokenizer.py
from gwlab.swift.tokenizer import tokenize
def test_parses_genl_block_with_seme():
    text = ":16R:GENL\n:20C::SEME//REF00000001\n:16S:GENL\n"
    tree = tokenize(text)
    seme = tree.find("20C", "SEME")
    assert len(seme) == 1
    assert seme[0].value == "REF00000001"
    assert tree.blocks[0].name == "GENL"
```
- [ ] **Step 2: Run — expect FAIL** (`tokenize` undefined). `vrg-container-run -- vrg-validate`
- [ ] **Step 3: Implement the tokenizer.** Line-scan the block-4 text: `:16R:<name>` opens a block, `:16S:<name>` closes it (nesting via a stack); `:NN[a]:` starts a field; a `//` after a `:4!c` qualifier splits qualifier from value; lines without a leading tag continue the previous field's value. Unbalanced `:16R:`/`:16S:` raises `TokenizeError`.
- [ ] **Step 3b: Add `parse_envelope`.** Recognize the SWIFT block framing `{1:…}{2:…}{4:…-}`; read the **MT number from Block 2** (`{2:O548…}` / `{2:I541…}`) and return `(mt, block4_text)`. Malformed framing raises `TokenizeError`. Test: `parse_envelope("{1:F01BANKBEBB…}{2:O5480900…}{4:\n:16R:GENL\n…\n-}")[0] == "548"`.
- [ ] **Step 4: Add tests — nested blocks, multi-line value, qualifier-less field, unbalanced-block error.**
```python
def test_nested_link_block_inside_genl():
    text = (":16R:GENL\n:20C::SEME//S1\n:16R:LINK\n:20C::RELA//S0\n"
            ":16S:LINK\n:16S:GENL\n")
    tree = tokenize(text)
    assert tree.find("20C", "RELA")[0].value == "S0"

def test_unbalanced_block_raises():
    import pytest
    from gwlab.swift.tokenizer import TokenizeError
    with pytest.raises(TokenizeError):
        tokenize(":16R:GENL\n:20C::SEME//S1\n")   # never closed
```
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "ISO 15022 block-4 tokenizer"`

### Task 3: Field-spec catalog validator

**Files:** Create `src/gwlab/swift/fields.py`; Test `tests/swift/test_fields.py`

**Interfaces:**
- Consumes: `reference.FIELD_CATALOG` (Task 1), `tokenizer.Field`/`FieldTree` (Task 2).
- Produces:
```python
@dataclass(frozen=True)
class FieldViolation:
    key: str            # ":20C::SEME"
    value: str
    reason: str         # "charset" | "format" | "domain"

def validate_field(field: Field) -> FieldViolation | None: ...
def validate_tree(tree: FieldTree) -> list[FieldViolation]: ...   # empty == clean
```
- [ ] **Step 1: Failing test — a valid SEME passes, a bad-charset SEME fails.**
```python
from gwlab.swift.tokenizer import Field
from gwlab.swift.fields import validate_field
def test_valid_seme_passes():
    assert validate_field(Field("20C","SEME","REF00000001")) is None
def test_seme_rejects_out_of_charset_value():
    v = validate_field(Field("20C","SEME","bad ref!"))  # space/lowercase/! not in 'x' set
    assert v is not None and v.reason in {"charset","format"}
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `validate_field`/`validate_tree`.** Look up `FIELD_CATALOG[key]`; apply the format spec (length, `!` fixed-length), the SWIFT character-set class (`x`/`z`/`n`/`c`/`a` → a compiled regex), and the enumerated domain from `STATUS_DOMAINS` where present. An unknown key is **not** a violation here (it is an anomaly surfaced later, Task 6/8) — keep responsibilities separate: this validates *known* fields.
- [ ] **Step 4: Add tests — a `:25D::IPRC//` value outside its domain is a `domain` violation; a fixed-length `4!c` overrun is a `format` violation.** (Use values drawn from `reference.STATUS_DOMAINS`.)
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "field-spec catalog validator"`

---

## Phase 2 — Correlation & lifecycle

### Task 4: Parsed-message model

**Files:** Create `src/gwlab/swift/model.py`; Test `tests/swift/test_model.py`

**Interfaces:**
- Consumes: `tokenizer.parse_envelope` + `tokenizer.tokenize` (Task 2), `Message.put_time` (`gwlab.messaging`).
- Produces:
```python
@dataclass(frozen=True)
class StatusItem:
    qualifier: str      # "IPRC","MTCH","SETT",...
    value: str          # "PACK","MACH","REJT",...
    reason: str | None  # from a paired :24B: in the same STAT block, if any

@dataclass(frozen=True)
class SwiftMessage:
    mt: str                    # "541","548","545",...
    seme: str | None           # this message's own :20C::SEME//
    rela: str | None           # linkage :20C::RELA// (None on an originating instruction)
    statuses: tuple[StatusItem, ...]   # all :25D: items (empty for instructions/confirmations)
    put_time: datetime         # from the MQ audit copy
    is_instruction: bool       # mt in {540,541,542,543}
    is_confirmation: bool      # mt in {544,545,546,547}

# Full-message entry point: envelope → mt + block4 → field tree → SwiftMessage.
def parse_swift(text: str, put_time: datetime) -> SwiftMessage: ...
# Lower-level (mt already known), used by parse_swift and by tests:
def parse_message(mt: str, tree: FieldTree, put_time: datetime) -> SwiftMessage: ...
```
- [ ] **Step 1: Failing test — parse a 548 carrying two statuses.**
```python
def test_parse_548_extracts_multiple_statuses():
    text = (":16R:GENL\n:20C::SEME::S9//\n".replace("::S9//","//S9")
            + ":16R:LINK\n:20C::RELA//S0\n:16S:LINK\n"
            + ":16R:STAT\n:25D::IPRC//PACK\n:16S:STAT\n"
            + ":16R:STAT\n:25D::MTCH//MACH\n:16S:STAT\n:16S:GENL\n")
    from gwlab.swift.tokenizer import tokenize
    from datetime import datetime
    msg = parse_message("548", tokenize(text), datetime(2026,7,29,9,0))
    assert msg.rela == "S0"
    assert {(s.qualifier,s.value) for s in msg.statuses} == {("IPRC","PACK"),("MTCH","MACH")}
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `parse_message` then `parse_swift`.** `parse_message`: extract `SEME`/`RELA` via `tree.find`; collect every `:25D:` as a `StatusItem`, pairing a same-STAT-block `:24B:` reason; set `is_instruction`/`is_confirmation` from `mt`. `parse_swift(text, put_time)`: call `parse_envelope` (Task 2) to get `(mt, block4)`, `tokenize(block4)`, then `parse_message`. The MT is now determined here (via the envelope), not supplied by callers.
- [ ] **Step 4: Add tests — `parse_swift` on a full `{1:}{2:}{4:}` 541 yields `mt=="541"`, `rela is None`, `statuses == ()`, `is_instruction is True`; a full 545 yields `is_confirmation is True`.**
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "parsed SwiftMessage model"`

### Task 5: Correlation (SEME/RELA grouping + orphans)

**Files:** Create `src/gwlab/swift/correlate.py`, `src/gwlab/swift/swiftconfig.py`; Test `tests/swift/test_correlate.py`

**Interfaces:**
- Consumes: `SwiftMessage` (Task 4).
- Produces:
```python
# swiftconfig.py
@dataclass(frozen=True)
class SwiftQueueSet:
    instruct_audit: str = "GW.SEC.INSTRUCT.AUDIT"
    status_audit: str = "GW.SEC.STATUS.AUDIT"

@dataclass(frozen=True)
class Horizon:
    days: int = 3          # settlement horizon incl. fails buffer (sized in Task 15)

# correlate.py
@dataclass(frozen=True)
class Correlated:
    instruction: SwiftMessage
    responses: list[SwiftMessage]      # all responses whose RELA == instruction.seme, put-time sorted

@dataclass(frozen=True)
class Orphan:
    rela: str
    message: SwiftMessage
    reason: str = "orphan"             # response RELA matches no sent instruction

def correlate(instructions: list[SwiftMessage],
              responses: list[SwiftMessage]) -> tuple[list[Correlated], list[Orphan]]: ...
```
- [ ] **Step 1: Failing test — one instruction, two responses, one orphan.**
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `correlate`.** Index instructions by `seme`; bucket responses by `rela`; a response whose `rela` matches no instruction `seme` becomes an `Orphan`; sort each bucket by `put_time`.
- [ ] **Step 3b (conditional, gated on Task 1 — Issue [3]): `:20C::PREV//` chains.** If the reference spike finds that `:20C::PREV//` participates in the tracked lifecycle (amendment/cancellation linking to a prior instruction's `SEME`), extend `correlate` to follow the previous-reference chain so an amendment attaches to its originating instruction. If the spike finds `PREV` does **not** apply, record that in a one-line note here and skip — no code. Do not implement speculatively before Task 1's finding.
- [ ] **Step 4: Add tests — put-time ordering is stable; an instruction with no responses yields `responses == []`; a response with `rela is None` is an orphan (`reason="orphan"`).**
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "SEME/RELA correlation + orphan detection"`

### Task 6: Lifecycle fold (monotonic advance along the reachability graph)

**Files:** Create `src/gwlab/swift/lifecycle.py`; Test `tests/swift/test_lifecycle.py`

**Interfaces:**
- Consumes: `reference` (Task 1), `Correlated`/`SwiftMessage` (Tasks 4/5).
- Produces:
```python
@dataclass(frozen=True)
class Hop:
    state: State
    at: datetime

@dataclass(frozen=True)
class TransitionAnomaly:
    seme: str
    attempted: State
    from_state: State
    reason: str        # "backward" | "unreachable"

@dataclass(frozen=True)
class InstructionState:
    seme: str
    current: State
    hops: list[Hop]                 # accepted, advancing transitions with timestamps
    anomalies: list[TransitionAnomaly]

def fold(correlated: Correlated) -> InstructionState: ...
def triggers_for(msg: SwiftMessage) -> list[State]:  # maps a message to the states it asserts, via reference.TRIGGERS
```
- [ ] **Step 1: Failing test — a clean settle path INSTRUCTED→ACKNOWLEDGED→MATCHED→SETTLED records four hops.**
```python
def test_clean_settle_records_four_hops():
    inst = _instruction("S0", t=0)
    r_ack = _status_548("S0", ("IPRC","PACK"), t=1)
    r_mat = _status_548("S0", ("MTCH","MACH"), t=2)
    r_set = _confirmation("545","S0", t=3)
    st = fold(Correlated(inst, [r_ack, r_mat, r_set]))
    assert st.current is State.SETTLED
    assert [h.state for h in st.hops] == [State.INSTRUCTED, State.ACKNOWLEDGED,
                                          State.MATCHED, State.SETTLED]
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `fold`.** Start at `INSTRUCTED` (hop at the instruction's put-time). For each response in put-time order, compute its asserted states via `triggers_for` (a 548 may assert several; apply in documented precedence). Apply a state **only if** it is reachable from `current` per `reference.REACHABILITY` and is *forward* → append a `Hop`, advance `current`. A repeat of `current` or an already-passed state is a **no-op** (not an anomaly). A backward or unreachable assertion appends a `TransitionAnomaly` and does not change `current`.
- [ ] **Step 4: Add tests — the untidy-traffic cases from spec §4.2:**
```python
def test_multi_status_message_advances_two_hops():
    st = fold(Correlated(_instruction("S0",0),
                         [_status_548_multi("S0", [("IPRC","PACK"),("MTCH","MACH")], t=1)]))
    assert st.current is State.MATCHED and len(st.hops) == 3

def test_repeat_status_is_noop_not_anomaly():
    st = fold(Correlated(_instruction("S0",0),
                         [_status_548("S0",("IPRC","PACK"),1),
                          _status_548("S0",("IPRC","PACK"),2)]))
    assert st.current is State.ACKNOWLEDGED and st.anomalies == []

def test_reject_from_instructed_is_terminal():
    st = fold(Correlated(_instruction("S0",0),[_status_548("S0",("IPRC","REJT"),1)]))
    assert st.current is State.REJECTED

def test_backward_or_unreachable_is_flagged_not_applied():
    # a confirmation (SETTLED) after REJECTED must not un-terminate; it is an anomaly
    st = fold(Correlated(_instruction("S0",0),
                         [_status_548("S0",("IPRC","REJT"),1), _confirmation("545","S0",2)]))
    assert st.current is State.REJECTED
    assert st.anomalies and st.anomalies[0].reason in {"backward","unreachable"}
```
(Provide the `_instruction`/`_status_548`/`_status_548_multi`/`_confirmation` helpers in the test module, building `SwiftMessage`s directly — no MT text needed here.)
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "monotonic lifecycle fold along the reachability graph"`

---

## Phase 3 — Collector & observability

### Task 7: Settlement window + read-only collector

**Files:** Create `src/gwlab/swift/window.py`, `src/gwlab/swift/collect.py`; Test `tests/swift/test_window.py`, `tests/swift/test_collect.py`

**Interfaces:**
- Consumes: `Messaging` (`gwlab.messaging`), `SwiftQueueSet`/`Horizon` (Task 5), `correlate` (Task 5), `fold` (Task 6). The collector needs the `mt` per audited message: instructions come from `instruct_audit`, responses from `status_audit`, and the `mt` within each is read from the body's message-type indicator (parsed in Task 4); the collector tags direction by queue of origin.
- Produces:
```python
# window.py
def in_window(put_time: datetime, now: datetime, horizon: Horizon) -> bool: ...
def is_aged_out(state: InstructionState, now: datetime, horizon: Horizon) -> bool:
    # non-terminal AND instruction older than horizon
# collect.py
@dataclass(frozen=True)
class Anomaly:
    kind: str          # "malformed" | "field-violation" | "orphan" | "transition"
    reference: str     # SEME/RELA/offending value — always populated for triage
    detail: str        # human-readable (the TokenizeError msg, the FieldViolation, etc.)
@dataclass(frozen=True)
class DayReport:
    states: list[InstructionState]
    aged_out: list[InstructionState]
    anomalies: list[Anomaly]         # UNIFIED channel: malformed + field-violation + orphan + transition
    scanned: int                     # messages browsed (scan-cost thread, spec §11)
def collect(messaging: Messaging, queues: SwiftQueueSet,
            now: datetime, horizon: Horizon) -> DayReport: ...
```
- [ ] **Step 1: Failing test — collect over a `FakeMessaging` seeded with one clean-settle flow and one late (T+2) confirmation, asserting the late confirmation still folds (not an anomaly).**
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `window.py` then `collect.py`.** `collect` browses `instruct_audit` and `status_audit`. It is the **graceful-degradation boundary** (spec §12): each body is parsed inside a try/except — a `TokenizeError` becomes a `malformed` `Anomaly`, a non-empty `validate_tree` (Task 3) becomes a `field-violation` `Anomaly` — and the run **continues** past the bad message. Valid messages are filtered to the settlement window via `in_window`, correlated (Task 5, whose `Orphan`s become `orphan` anomalies), folded (Task 6, whose `TransitionAnomaly`s become `transition` anomalies), and `aged_out` computed. All four anomaly kinds land in the one `anomalies` list; `scanned` counts every message browsed. Read-only: no puts.
- [ ] **Step 4: Add tests — (a) an instruction with no terminal state past the horizon lands in `aged_out`, not `anomalies`; (b) `scanned` equals the messages browsed; (c) a pre-window instruction is excluded; (d) *graceful degradation:* a batch with one un-tokenizable message and one field-violating message yields exactly two anomalies (`malformed`, `field-violation`) AND still reports every valid instruction — one bad message never aborts the run.**
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "settlement-window scoping + read-only collector"`

### Task 8: Observability metrics

**Files:** Create `src/gwlab/swift/observe.py`; Test `tests/swift/test_observe.py`

**Interfaces:**
- Consumes: `DayReport` (Task 7).
- Produces:
```python
@dataclass(frozen=True)
class LatencyStats:
    hop: str            # "instructed->acknowledged", ...
    mean_seconds: float
    p95_seconds: float
@dataclass(frozen=True)
class Metrics:
    distribution: dict[State, int]          # count per state (the hero)
    latencies: list[LatencyStats]           # per inter-state hop
    aging: list[InstructionState]           # in-flight, sorted by age
    rejected: list[InstructionState]
    failed: list[InstructionState]
    anomalies: int                          # total across all kinds
    anomalies_by_kind: dict[str, int]       # malformed / field-violation / orphan / transition
    scanned: int
def metrics(report: DayReport, now: datetime) -> Metrics: ...
```
- [ ] **Step 1: Failing test — distribution counts each state; latencies computed from hop timestamps.**
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement `metrics`.** Tally `distribution`; for each adjacent hop pair across all instructions compute mean/p95 from timestamp deltas; collect `aging` (non-terminal, sorted by `now - instructed_at`), `rejected`, `failed`; count `report.anomalies` into `anomalies_by_kind` and `anomalies` (the total); pass through `scanned`.
- [ ] **Step 4: Add tests — attention sets contain the right SEMEs; `anomalies_by_kind` tallies all four kinds and `anomalies` is their sum; empty report yields zeroed metrics (no divide-by-zero).**
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "settlement observability metrics"`

---

## Phase 4 — Corpus, responder, simulation

### Task 9: Sample-message corpus generator

**Files:** Create `src/gwlab/swift/corpus.py`; Test `tests/swift/test_corpus.py`

**Interfaces:**
- Consumes: `reference` (Task 1), `fields.validate_tree` (Task 3), `tokenize` (Task 2), `fold` (Task 6).
- Produces:
```python
@dataclass(frozen=True)
class Flow:
    name: str                 # "clean-settle","rejected-at-validation","matched-then-failing",...
    instruction_text: str     # MT540-543 body
    response_texts: list[str] # ordered MT548 / MT544-547 bodies
    expected_terminal: State | None   # None for in-flight/aging flows
def all_flows() -> list[Flow]: ...            # one per documented reachable path + anomaly cases
# Renderers emit FULL {1:}{2:}{4:} messages (Issue [1]) so parse_swift can read the MT.
def render_instruction(mt: str, seme: str, **econ) -> str: ...
def render_status(rela: str, statuses: list[tuple[str,str]]) -> str: ...   # an MT548
def render_confirmation(mt: str, rela: str) -> str: ...                    # an MT544-547
```
- [ ] **Step 1: Failing test — every generated message parses to the right MT and is format-valid against the catalog.**
```python
def test_all_corpus_messages_are_format_valid():
    from gwlab.swift.tokenizer import parse_envelope, tokenize
    from gwlab.swift.fields import validate_tree
    for flow in all_flows():
        for text in [flow.instruction_text, *flow.response_texts]:
            mt, block4 = parse_envelope(text)          # MT readable from Block 2
            assert mt.isdigit()
            assert validate_tree(tokenize(block4)) == []   # block-4 fields all catalog-valid
```
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement the renderers and `all_flows`.** Renderers emit **full `{1:}{2:}{4:}` messages** — a Block 2 carrying the correct MT and a Block 4 whose every field satisfies `FIELD_CATALOG` (correct qualifiers, char-sets, lengths) using realistic exemplars (ISINs, quantities, dates, references). `all_flows` enumerates one flow per **reachable** path in `REACHABILITY` (clean-settle, rejected-at-validation, matched-then-failing, acknowledged-and-aging) plus anomaly fixtures (orphan RELA, malformed field, undocumented qualifier).
- [ ] **Step 4: Add tests — folding each non-anomaly flow reproduces its `expected_terminal`; the anomaly flows produce the intended anomaly (orphan/transition/violation); every reachable path in `REACHABILITY` has at least one flow.**
- [ ] **Step 5: Run — expect PASS. Commit.** `vrg-commit --type feat --scope swift --message "format-valid lifecycle-path corpus"`

### Task 10: Responder (emits realistic response sequences)

**Files:** Create `src/gwlab/swift/responder.py`; Test `tests/swift/test_responder.py`

**Interfaces:**
- Consumes: `corpus` renderers (Task 9), `Messaging` (put + commit to `status_audit` in the fake; live path streams via MQ).
- Produces:
```python
@dataclass(frozen=True)
class Plan:
    path: str               # a Flow.name
    delays_seconds: list[float]   # inter-response delays, realistic pacing
def respond(messaging, queues, instruction: SwiftMessage, plan: Plan, clock) -> None: ...
```
- [ ] **Step 1: Failing test — given a matched-then-failing plan, the responder emits a 548-accepted, 548-matched, then 548-failing with correct RELA, put via the fake.**
- [ ] **Step 2: Run — FAIL. Step 3: Implement** (map `plan.path` to the corpus flow's response_texts; put each with the injected `clock` advancing by `delays_seconds`). **Step 4: tests** (RELA echoes the instruction SEME; ordering/timestamps monotonic). **Step 5: PASS + commit.** `vrg-commit --type feat --scope swift --message "settlement responder"`

### Task 11: Multi-day settlement simulation driver

**Files:** Create `src/gwlab/swift/simulation.py`; Test `tests/swift/test_simulation.py`

**Interfaces:**
- Consumes: `corpus`, `responder`, `collect`, injected `clock`.
- Produces: `def run_window(messaging, queues, horizon, scenario, clock) -> DayReport` — instructs a batch across simulated days, drives responders (including late T+2 confirmations, rejects, fails, and a handful left aging), and returns the collected report.
- [ ] **Step 1: Failing test — a scenario of N instructions where most settle over T…T+2, a few reject/fail, one ages out, and one orphan is injected; assert the collected `DayReport` distribution/aged_out/orphans match the scenario.**
- [ ] **Step 2–4: Run/Implement/expand** (deterministic via injected clock; no wall-clock). **Step 5: PASS + commit.** `vrg-commit --type feat --scope swift --message "multi-day settlement simulation"`

---

## Phase 5 — Dashboard, CLI, exporter

### Task 12: State-machine dashboard

**Files:** Create `src/gwlab/swift/dashboard.py`; Test `tests/swift/test_dashboard.py`

**Interfaces:** Consumes `Metrics` (Task 8). Produces `def render_board(metrics: Metrics, now: datetime) -> str` (self-contained HTML/artifact) with: the **state-distribution funnel** (hero), the **lifecycle-latency** panel, the **attention tables** (aging / rejected / failed, each by SEME + age), the **anomaly** panel broken out by kind (`anomalies_by_kind`: malformed / field-violation / orphan / transition), and the **scan-cost** line.
- [ ] **Step 1: Failing test — `render_board` output contains each state label, each attention section, and the scanned-count.** (String/DOM-contains assertions, mirroring v1's `test_dashboard.py` style.)
- [ ] **Step 2–4: Run/Implement/expand.** **Step 5: PASS + commit.** `vrg-commit --type feat --scope swift --message "state-machine settlement dashboard"`

### Task 13: CLI subcommands + exporter wiring

**Files:** Create `src/gwlab/swift/cliswift.py`; Modify `src/gwlab/cli.py` (register the `swift` group only — additive, v1 paths untouched); Test `tests/swift/test_cliswift.py`

**Interfaces:** a `gwlab swift` command group: `swift simulate` (run a scenario into the fake/live audits), `swift report` (collect + print metrics), `swift board` (write the dashboard), `swift serve` (Prometheus exposition of `Metrics` on a **port distinct from v1's**, per spec §7).
- [ ] **Step 1: Failing test — `gwlab swift report` on a seeded fake prints the state distribution; the exporter registers metrics on the configured port.**
- [ ] **Step 2–4: Run/Implement/expand** (reuse v1's exporter/HTTP-serve scaffolding; assert no v1 command or metric name/port is altered). **Step 5: PASS + commit.** `vrg-commit --type feat --scope swift --message "gwlab swift CLI + exporter"`

---

## Phase 6 — Deploy & live (operational)

### Task 14: Deliverable design markdown

**Files:** Create `docs/mq-native-settlement-tracking.md` (Modify nothing in v1's doc).
- [ ] Write the v2 deliverable mirroring v1's structure (prose → worked example → tech spec): the settlement lifecycle + transition table, the field catalog, the reference-based correlation, the settlement-window scoping, the observability, and — as noted-not-built — the **ISO 20022 sese.023/.024/.025 field mapping** and the **future third message family** extension point. Embed the hero board from Task 15. Fully generic. **Commit.** `vrg-commit --type docs --scope epic-65 --message "v2 settlement-tracking deliverable"`
- [ ] This task's PR is filed same-repo; it is **blocked-by** Task 15 (needs the live hero board).

### Task 15 (deployment → validation): GW.SEC.* objects, live run, hero board

Two GitHub issues: a **deployment** task (stand up the objects + board/exporter) that the **validation** task **#68** is blocked-by.

- [ ] **Deployment:** author an Ansible `gw-sec-queues` role + MQSC for `GW.SEC.INSTRUCT.AUDIT` / `GW.SEC.STATUS.AUDIT` — the streaming `MUSTDUP` audit pattern from v1, with `CAPEXPRY` sized to the **settlement horizon** (Task 5/7) and `MAXDEPTH`/disk sized for the horizon's peak volume (spec §7 arithmetic × horizon days). Declare-and-verify the counterparty QM/channels (parasite rule). Deploy the v2 board + exporter on a **distinct port** from v1. Human-gated for any release step; agent performs only install/sync (spec's deployment-autonomy boundary).
- [ ] **Validation (#68):** drive a realistic multi-day settlement window through the live `GW.SEC.*` streaming audits (all documented lifecycle paths + anomalies + a T+2 late confirmation + an aged-out instruction), confirm the collector folds correctly against real iron, and capture the **hero board** for the deliverable. Record `Outcome: SUCCESS` with the numbers as a comment.

---

## Self-Review

**Spec coverage** — every spec section maps to a task:
- §2 boundaries → Global Constraints + Tasks 1 (documented-to-the-letter), 5 (viewpoint via corpus/config), 7 (window).
- §3 message family → Tasks 4, 9. §4 state machine + fold → Tasks 1, 6. §4.4 window → Task 7. §5 correlation → Task 5. §6 architecture → file structure + all tasks. §7 MQ objects/coexistence → Task 15 (+ port note in Task 13). §8 data flow → Task 7. §9 catalog → Tasks 1, 3. §10 corpus → Task 9. §11 observability → Tasks 8, 12. §12/§13 anomalies/no-silent-failures → Tasks 3, 6, 8 (surfaced), never swallowed. §13 testing → every task is TDD + Task 11 e2e + Task 15 live. §14 deliverable → Task 14. §15 out-of-scope → honored (no putter; no ISO 20022 parser; no economic matching). §16 research → Task 1. §18 success criteria → Tasks 14/15.
- **Gap check (post-alignment):** three gaps found and closed. (1) **MT-type extraction** now has an owner — `parse_envelope` in Task 2 reads the MT from Block 2, and Task 4's `parse_swift` consumes it (no caller-supplied `mt`). (2) **Anomaly surfacing / graceful degradation** — Task 7's collector is the guard boundary: tokenize failures and field violations are caught, unified into `DayReport.anomalies` (kinds malformed/field-violation/orphan/transition), counted in Task 8 (`anomalies_by_kind`) and shown in Task 12; one bad message never aborts the run. (3) **`:20C::PREV//` chains** — a conditional Task 5 step gated on the Task 1 spike's finding.

**Placeholder scan** — no "TBD/handle-appropriately"; every code step shows real test + implementation shape; peripheral tasks (10–13) compress the run/implement steps but state concrete interfaces and the first failing test.

**Type consistency** — `State`/`TRIGGERS`/`REACHABILITY` (Task 1) are the single vocabulary used by Tasks 6/7/8/9/12; `SwiftMessage` (Task 4) flows through Tasks 5/6/7; `DayReport` (Task 7) → `Metrics` (Task 8) → dashboard (Task 12). Names checked consistent across tasks.
