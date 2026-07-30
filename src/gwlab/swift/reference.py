"""The cited SWIFT MT540-548 settlement reference — the data-driven spine.

This module is the single source of truth the parser, the field validators, the
correlation layer, the lifecycle fold, and the corpus all consume (epic #65,
task #74). Everything here is **grounded against public sources** and each value
carries provenance in :data:`CITATIONS`; nothing is asserted from memory. Values
the sources did not yield are omitted, not guessed (the source-fallback policy —
see ``docs/swift-settlement-reference.md``).

The tables, not prose, are the documentation: an out-of-catalog qualifier or an
out-of-graph transition *is* the anomaly signal the collector surfaces.

Provenance summary (full detail and citations in the reference doc):

- MT548 ``:25D:`` / ``:24B:`` qualifiers and codes, and the ``:20C:`` linkage
  qualifiers: iotafinance MT548 rendering, cross-checked against the ISO 20022
  ``sese.024`` XSD status structure (``ProcessingStatus`` / ``MatchingStatus`` /
  ``SettlementStatus`` choices), which agrees.
- The ``:20C::SEME//`` -> ``:20C::RELA//`` echo (a response links back to the
  instruction it reports on): documented across public market-practice sources
  and shown live in a worked OeKB CSD MT548.
- Field formats: the MIT-licensed ``swift-parser`` ``metadata/patterns.json`` and
  the SWIFT ISO 15022 manual, confirmed against worked MT541/543/548/544 examples.

Design note (flagged for human review, see the reference doc): there is **no
MT548 status code meaning "settled"** — SETTLED is driven solely by the arrival
of an MT544-547 confirmation. ``SETT//PEND`` (pending) and other documented
in-flight codes are *recognised but non-advancing*: they are valid (so never an
anomaly) but do not move the state machine. Only the codes in
:data:`STATUS_TRIGGERS` advance state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class State(StrEnum):
    """The six settlement-lifecycle states (four progressive, two negative)."""

    INSTRUCTED = "instructed"
    ACKNOWLEDGED = "acknowledged"
    MATCHED = "matched"
    SETTLED = "settled"
    REJECTED = "rejected"
    FAILED = "failed"


#: The happy-path progression, in order. Rank in this tuple defines "forward".
PROGRESSIVE: tuple[State, ...] = (
    State.INSTRUCTED,
    State.ACKNOWLEDGED,
    State.MATCHED,
    State.SETTLED,
)

#: The two terminal-negative states (for observability bucketing).
TERMINAL_NEGATIVE: frozenset[State] = frozenset({State.REJECTED, State.FAILED})

#: Graph sinks — states with no outward transition. Note FAILED is NOT a sink: a
#: failing instruction can still settle within the settlement window (spec §4.4),
#: so FAILED -> SETTLED is permitted. REJECTED (a validation rejection) is a hard
#: sink.
SINK: frozenset[State] = frozenset({State.SETTLED, State.REJECTED})

_ALL_STATES: frozenset[State] = frozenset(State)

#: Directed reachability the fold enforces: from-state -> states a single trigger
#: may advance to. Monotonic and forward-only among the progressive states; the
#: terminal-negatives are reachable from the in-flight states. Permissive on
#: skips (e.g. INSTRUCTED -> MATCHED) so the fold tolerates missing/late advices.
#: The exact edge set is the load-bearing human-review decision for this spike.
#: INSTRUCTED reaches any other state; ACKNOWLEDGED reaches all but INSTRUCTED;
#: MATCHED only settles or fails; FAILED may still settle within the window;
#: SETTLED and REJECTED are hard sinks.
REACHABILITY: dict[State, frozenset[State]] = {
    State.INSTRUCTED: _ALL_STATES - {State.INSTRUCTED},
    State.ACKNOWLEDGED: _ALL_STATES - {State.INSTRUCTED, State.ACKNOWLEDGED},
    State.MATCHED: frozenset({State.SETTLED, State.FAILED}),
    State.FAILED: frozenset({State.SETTLED}),
    State.SETTLED: frozenset(),
    State.REJECTED: frozenset(),
}

#: Instruction message types (account owner -> account servicer).
INSTRUCTION_MTS: frozenset[str] = frozenset({"540", "541", "542", "543"})

#: Confirmation message types — their arrival drives SETTLED (there is no MT548
#: "settled" status code).
CONFIRMATION_MTS: frozenset[str] = frozenset({"544", "545", "546", "547"})

#: The status-advice message type.
STATUS_MT: str = "548"

#: State-advancing MT548 ``:25D:`` statuses: (qualifier, code) -> State. ONLY
#: these advance the machine; every other documented code in STATUS_DOMAINS is
#: recognised-but-non-advancing (valid, never an anomaly, no state change).
STATUS_TRIGGERS: dict[tuple[str, str], State] = {
    ("IPRC", "PACK"): State.ACKNOWLEDGED,  # Acknowledged/Accepted
    ("MTCH", "MACH"): State.MATCHED,  # Matched
    ("IPRC", "REJT"): State.REJECTED,  # Rejected for further processing
    ("SETT", "PENF"): State.FAILED,  # Pending/Failing settlement
}

#: Documented value domain of each MT548 ``:25D:`` status qualifier (codes valid
#: when no Data Source Scheme is present). A value outside its qualifier's domain
#: is an anomaly; a qualifier absent here is an undocumented-qualifier anomaly.
STATUS_DOMAINS: dict[str, frozenset[str]] = {
    "IPRC": frozenset({"CAND", "CANP", "CGEN", "CPRC", "MPRC", "PACK", "PPRC", "REJT", "REPR"}),
    "MTCH": frozenset({"MACH", "NMAT"}),
    "INMH": frozenset({"MACH", "NMAT"}),
    "SETT": frozenset({"PEND", "PENF"}),
    "CPRC": frozenset({"CAND", "CANP", "DEND", "PACK", "REJT"}),
    "SPRC": frozenset({"REJT"}),
    "CALL": frozenset({"CACK", "DEND"}),
    "TPRC": frozenset({"DEND", "MODC", "MOPN", "PACK", "REJT"}),
}

#: The linkage qualifier a response uses to point back at the instruction it
#: reports on. A response's ``:20C::RELA//`` echoes the instruction's
#: ``:20C::SEME//`` (``NONREF`` when none exists). ``PREV`` links a
#: cancellation/second-leg to a previously *sent* message.
CORRELATION_SENDER_REF: str = "SEME"
CORRELATION_RELATED_REF: str = "RELA"
CORRELATION_PREVIOUS_REF: str = "PREV"
NO_REFERENCE: str = "NONREF"


@dataclass(frozen=True)
class FieldSpec:
    """The catalog entry for one field/qualifier we consume.

    ``fmt`` is the SWIFT format string as published (e.g. ``:4!c//16x``);
    ``charset`` names the SWIFT character-set class of the value part (``x``
    SWIFT set, ``c`` upper-alphanumeric, ``n`` digits, ``a`` upper-letters) for
    the validator to compile; ``max_len`` bounds the value part where fixed.
    """

    tag: str
    qualifier: str | None
    fmt: str
    charset: str
    max_len: int
    note: str = ""


#: Field-spec catalog: canonical key -> FieldSpec. Keys are ``"<tag>"`` or
#: ``"<tag>:<qualifier>"``. Formats from the MIT ``swift-parser`` patterns and
#: the SWIFT manual; confirmed against the worked examples.
FIELD_CATALOG: dict[str, FieldSpec] = {
    "16R": FieldSpec("16R", None, "16c", "c", 16, "block open"),
    "16S": FieldSpec("16S", None, "16c", "c", 16, "block close"),
    "20C:SEME": FieldSpec("20C", "SEME", ":4!c//16x", "x", 16, "sender's message reference"),
    "20C:RELA": FieldSpec("20C", "RELA", ":4!c//16x", "x", 16, "related reference (echoes SEME)"),
    "20C:PREV": FieldSpec("20C", "PREV", ":4!c//16x", "x", 16, "previous (sent) reference"),
    "20C:COMM": FieldSpec("20C", "COMM", ":4!c//16x", "x", 16, "common reference"),
    "20C:TRRF": FieldSpec("20C", "TRRF", ":4!c//16x", "x", 16, "deal reference"),
    "23G": FieldSpec("23G", None, "4!c[/4!c]", "c", 4, "function of message (NEWM/CANC/INST)"),
    "25D:IPRC": FieldSpec("25D", "IPRC", ":4!c/[8c]/4!c", "c", 4, "instruction processing status"),
    "25D:MTCH": FieldSpec("25D", "MTCH", ":4!c/[8c]/4!c", "c", 4, "matching status"),
    "25D:SETT": FieldSpec("25D", "SETT", ":4!c/[8c]/4!c", "c", 4, "settlement status"),
    "24B": FieldSpec("24B", None, ":4!c/[8c]/4!c", "c", 4, "reason"),
    "35B": FieldSpec("35B", None, "[ISIN1!e12!c][4*35x]", "c", 12, "ISIN + description"),
    "36B:SETT": FieldSpec("36B", "SETT", ":4!c//4!c/15d", "d", 15, "quantity to settle"),
    "36B:ESTT": FieldSpec("36B", "ESTT", ":4!c//4!c/15d", "d", 15, "settled quantity"),
    "98A:SETT": FieldSpec("98A", "SETT", ":4!c//8!n", "n", 8, "settlement date YYYYMMDD"),
    "98A:TRAD": FieldSpec("98A", "TRAD", ":4!c//8!n", "n", 8, "trade date YYYYMMDD"),
    "98C:PREP": FieldSpec("98C", "PREP", ":4!c//8!n6!n", "n", 14, "preparation date+time"),
    "98C:ESET": FieldSpec("98C", "ESET", ":4!c//8!n6!n", "n", 14, "effective settlement date+time"),
    "97A:SAFE": FieldSpec("97A", "SAFE", ":4!c//35x", "x", 35, "safekeeping account"),
    "22F:SETR": FieldSpec("22F", "SETR", ":4!c/[8c]/4!c", "c", 4, "settlement transaction type"),
    "22H:REDE": FieldSpec("22H", "REDE", ":4!c//4!c", "c", 4, "receive/deliver indicator"),
    "22H:PAYM": FieldSpec("22H", "PAYM", ":4!c//4!c", "c", 4, "payment indicator"),
    "95P:PSET": FieldSpec("95P", "PSET", ":4!c//4!a2!a2!c[3!c]", "c", 11, "place of settlement"),
    "19A:SETT": FieldSpec("19A", "SETT", ":4!c//[N]3!a15d", "d", 15, "settlement amount"),
}


@dataclass(frozen=True)
class Citation:
    """Provenance for an encoded value.

    ``kind`` is one of ``"mt-rendering"`` (a public ISO 15022 field rendering),
    ``"iso20022-xsd"`` (the free sese XSD, used as the source-fallback and
    cross-check), ``"worked-example"`` (a published full message), or
    ``"inferred"`` (our labelled reasoning, not a primary-source statement).
    """

    source: str
    kind: str
    note: str = ""


_IOTA_MT548 = "https://www.iotafinance.com/en/SWIFT-ISO15022-Message-type-MT548.html"
_SESE024_XSD = (
    "https://raw.githubusercontent.com/mgruener/iso20022tpl/HEAD/assets/xsd/sese.024.001.10.xsd"
)
_SWIFT_MANUAL_20C = "https://pinas.synology.me/Swift_Manual/2015/books/us5mb/alif009.htm"
_PATTERNS_JSON = (
    "https://raw.githubusercontent.com/pstodulka/swift-parser/master/metadata/patterns.json"
)
_OEKB_EXAMPLES = "https://www.oekb-csd.at/dam/jcr:586d6dbf-109c-4261-aac7-e4ccca017e0b/Using-T2S-for-Transaction-Management-SWIFT-ISO15022-Examples.pdf"
_EURONEXT_MT541 = "https://www.euronext.com/sites/default/files/2022-09/4516%20-%20esempi%20MT541%20e%20MT543_0.pdf"
_ASX_RELA = "https://asxonline.com/public/notices/2021/June/0794.21.06.html"
_PAIEMENTOR_BLOCK2 = (
    "https://www.paiementor.com/swift-mt-message-block-2-application-header-description/"
)

#: Provenance for every encoded status qualifier, correlation field, and message
#: role. The self-consistency test asserts every STATUS_DOMAINS qualifier and
#: every STATUS_TRIGGERS entry is cited here.
CITATIONS: dict[str, Citation] = {
    "25D:IPRC": Citation(_IOTA_MT548, "mt-rendering", "cross-checked vs sese.024 ProcessingStatus"),
    "25D:MTCH": Citation(_IOTA_MT548, "mt-rendering", "cross-checked vs sese.024 MatchingStatus"),
    "25D:INMH": Citation(_IOTA_MT548, "mt-rendering", "inferred matching status; same value set"),
    "25D:SETT": Citation(_IOTA_MT548, "mt-rendering", "cross-checked vs sese.024 SettlementStatus"),
    "25D:CPRC": Citation(_IOTA_MT548, "mt-rendering", "cancellation processing status"),
    "25D:SPRC": Citation(_IOTA_MT548, "mt-rendering", "statement/status-request status"),
    "25D:CALL": Citation(_IOTA_MT548, "mt-rendering", "repo call request status"),
    "25D:TPRC": Citation(_IOTA_MT548, "mt-rendering", "processing-change command status"),
    "sese.024": Citation(_SESE024_XSD, "iso20022-xsd", "status structure cross-check / fallback"),
    "20C:SEME": Citation(_SWIFT_MANUAL_20C, "mt-rendering", "sender's message reference, GENL"),
    "20C:RELA": Citation(_ASX_RELA, "mt-rendering", "RELA echoes the instruction's SEME"),
    "20C:PREV": Citation(_SWIFT_MANUAL_20C, "mt-rendering", "previous (sent) message reference"),
    "field-formats": Citation(_PATTERNS_JSON, "mt-rendering", "MIT swift-parser field patterns"),
    "envelope": Citation(_PAIEMENTOR_BLOCK2, "mt-rendering", "block-1/2 envelope format"),
    "example:MT548": Citation(_OEKB_EXAMPLES, "worked-example", "OeKB CSD; shows :25D::IPRC//PACK"),
    "example:MT543": Citation(_OEKB_EXAMPLES, "worked-example", "OeKB CSD deliver-vs-payment"),
    "example:MT544": Citation(_OEKB_EXAMPLES, "worked-example", "OeKB CSD confirmation"),
    "example:MT541": Citation(_EURONEXT_MT541, "worked-example", "Euronext receive-vs-payment"),
    "fails-mapping": Citation("inferred", "inferred", "ISO15022 LACK/MONY <-> ISO20022 CLAC/CMON"),
}
