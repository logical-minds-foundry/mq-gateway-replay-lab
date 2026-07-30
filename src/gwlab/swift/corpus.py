"""Format-valid sample-message corpus: one flow per reachable lifecycle path.

This module renders **full ``{1:}{2:}{4:}`` SWIFT envelopes** — the MT number
lives in Block 2 (so :func:`gwlab.swift.tokenizer.parse_envelope` can read it),
and every Block 4 field satisfies :data:`gwlab.swift.reference.FIELD_CATALOG`
(correct qualifiers, char-sets, lengths). The renderers are pure text builders;
they apply no validation, so the anomaly fixtures can deliberately break a field.

:func:`all_flows` enumerates one flow per **reachable** path in
:data:`gwlab.swift.reference.REACHABILITY` (clean-settle, rejected-at-validation,
matched-then-failing, acknowledged-aging). :func:`anomaly_flows` returns the
negative fixtures — an orphaned ``RELA``, a body that will not tokenize, and a
field that breaks its catalog format — each tagged via :attr:`Flow.anomaly` so a
test can assert the intended failure.

All institution data is **invented and anonymised** (Counterparty A/B, invented
BICs, ``GWSEC…`` references, ``XX…`` ISINs, ``GWSAFE…`` accounts); nothing is
copied from the research examples (epic #65, anonymization rule).
"""

from __future__ import annotations

from dataclasses import dataclass

from gwlab.swift.reference import State

#: Invented, anonymised BICs (12-char logical-terminal addresses). Ours vs. the
#: counterparty's — direction only, the parser reads neither.
_OUR_BIC = "GWLABXXA0XXX"
_CPTY_BIC = "CPTYAAA0XXX"

#: A response carries its own sender's reference; we correlate on ``RELA`` (which
#: echoes the instruction's ``SEME``), so a fixed own-reference is sufficient.
_STATUS_SEME = "GWSTS0000000001"
_CONF_SEME = "GWCNF0000000001"

#: One instruction reference per reachable-path flow.
_SEME_CLEAN = "GWSEC0000000001"
_SEME_REJECT = "GWSEC0000000002"
_SEME_FAILING = "GWSEC0000000003"
_SEME_AGING = "GWSEC0000000004"


@dataclass(frozen=True)
class Flow:
    """One instruction plus its ordered response stream, and where it should land.

    ``expected_terminal`` is the state the lifecycle fold should reach; ``None``
    for an in-flight/aging flow. ``anomaly`` is ``None`` for a well-formed flow
    and otherwise names the intended defect (``"orphan"``, ``"malformed"``,
    ``"field-violation"``) so a test can assert it.
    """

    name: str
    instruction_text: str
    response_texts: list[str]
    expected_terminal: State | None
    anomaly: str | None = None


def _envelope(mt: str, sender: str, dest: str, block4_body: str) -> str:
    """Wrap a Block 4 body in a full ``{1:}{2:}{4:}`` envelope carrying ``mt``."""
    block1 = "{1:F01" + sender + "0000000000}"
    block2 = "{2:I" + mt + dest + "N}"
    block4 = "{4:\n" + block4_body + "\n-}"
    return block1 + block2 + block4


def render_instruction(mt: str, seme: str, **econ: str) -> str:
    """Render a full MT540-543 instruction envelope with catalog-valid fields.

    Economic exemplars (``isin``, ``desc``, ``sett_date``, ``qty``, ``acct``) may
    be overridden via keyword; all default to invented, anonymised values.
    """
    isin = econ.get("isin", "XX0000000001")
    desc = econ.get("desc", "GENERIC EQUITY NPV")
    sett_date = econ.get("sett_date", "20260729")
    qty = econ.get("qty", "1000,")
    acct = econ.get("acct", "GWSAFE0001")
    body = "\n".join(
        [
            ":16R:GENL",
            ":20C::SEME//" + seme,
            ":23G:NEWM",
            ":16S:GENL",
            ":16R:TRADDET",
            ":98A::SETT//" + sett_date,
            ":35B:ISIN " + isin,
            desc,
            ":16S:TRADDET",
            ":16R:FIAC",
            ":36B::SETT//UNIT/" + qty,
            ":97A::SAFE//" + acct,
            ":16S:FIAC",
        ]
    )
    return _envelope(mt, _OUR_BIC, _CPTY_BIC, body)


def render_status(rela: str, statuses: list[tuple[str, str]]) -> str:
    """Render a full MT548 status advice whose ``:20C::RELA//`` echoes ``rela``.

    Each ``(qualifier, code)`` becomes a ``:25D::<qualifier>//<code>`` in the STAT
    sequence; the value is emitted verbatim, so a caller may pass an out-of-domain
    code to build a deliberately field-violating fixture.
    """
    stat_lines = [":25D::" + qualifier + "//" + code for qualifier, code in statuses]
    body = "\n".join(
        [
            ":16R:GENL",
            ":20C::SEME//" + _STATUS_SEME,
            ":23G:INST",
            ":16R:LINK",
            ":20C::RELA//" + rela,
            ":16S:LINK",
            ":16S:GENL",
            ":16R:STAT",
            *stat_lines,
            ":16S:STAT",
        ]
    )
    return _envelope("548", _CPTY_BIC, _OUR_BIC, body)


def render_confirmation(mt: str, rela: str) -> str:
    """Render a full MT544-547 settlement confirmation linked back via ``rela``."""
    body = "\n".join(
        [
            ":16R:GENL",
            ":20C::SEME//" + _CONF_SEME,
            ":23G:NEWM",
            ":16R:LINK",
            ":20C::RELA//" + rela,
            ":16S:LINK",
            ":16S:GENL",
        ]
    )
    return _envelope(mt, _CPTY_BIC, _OUR_BIC, body)


def _malformed_message() -> str:
    """A full envelope whose Block 4 is a stray line — it will not tokenize."""
    return _envelope("548", _CPTY_BIC, _OUR_BIC, "THIS LINE IS NOT A FIELD")


def all_flows() -> list[Flow]:
    """One well-formed flow per reachable lifecycle path in ``REACHABILITY``."""
    return [
        Flow(
            name="clean-settle",
            instruction_text=render_instruction("541", _SEME_CLEAN),
            response_texts=[
                render_status(_SEME_CLEAN, [("IPRC", "PACK")]),
                render_status(_SEME_CLEAN, [("MTCH", "MACH")]),
                render_confirmation("545", _SEME_CLEAN),
            ],
            expected_terminal=State.SETTLED,
        ),
        Flow(
            name="rejected-at-validation",
            instruction_text=render_instruction("541", _SEME_REJECT),
            response_texts=[render_status(_SEME_REJECT, [("IPRC", "REJT")])],
            expected_terminal=State.REJECTED,
        ),
        Flow(
            name="matched-then-failing",
            instruction_text=render_instruction("543", _SEME_FAILING),
            response_texts=[
                render_status(_SEME_FAILING, [("IPRC", "PACK")]),
                render_status(_SEME_FAILING, [("MTCH", "MACH")]),
                render_status(_SEME_FAILING, [("SETT", "PENF")]),
            ],
            expected_terminal=State.FAILED,
        ),
        Flow(
            name="acknowledged-aging",
            instruction_text=render_instruction("540", _SEME_AGING),
            response_texts=[render_status(_SEME_AGING, [("IPRC", "PACK")])],
            expected_terminal=None,
        ),
    ]


def anomaly_flows() -> list[Flow]:
    """The negative fixtures: orphan ``RELA``, un-tokenizable body, bad field."""
    return [
        Flow(
            name="orphan-rela",
            instruction_text=render_instruction("541", "GWSEC0000000009"),
            response_texts=[render_status("GWSEC0009999999", [("IPRC", "PACK")])],
            expected_terminal=None,
            anomaly="orphan",
        ),
        Flow(
            name="malformed-body",
            instruction_text=render_instruction("541", "GWSEC0000000011"),
            response_texts=[_malformed_message()],
            expected_terminal=None,
            anomaly="malformed",
        ),
        Flow(
            name="field-violation",
            instruction_text=render_instruction("541", "GWSEC0000000012"),
            response_texts=[render_status("GWSEC0000000012", [("IPRC", "ZZZZ")])],
            expected_terminal=None,
            anomaly="field-violation",
        ),
    ]
