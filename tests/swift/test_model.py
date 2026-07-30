"""Tests for the parsed SwiftMessage model (envelope + field tree -> typed message)."""

from __future__ import annotations

from datetime import datetime

import pytest

from gwlab.swift.model import StatusItem, SwiftMessage, parse_message, parse_swift
from gwlab.swift.tokenizer import tokenize

_PUT_TIME = datetime(2026, 7, 29, 9, 0)

# A full (invented, anonymised) MT541 instruction envelope: SEME but no linkage
# (an originating instruction has no RELA) and no STAT sequences.
_MT541 = (
    "{1:F01GWLABXXA0XXX0000000000}{2:I541CPTYAAA0XXXN}{4:\n"
    ":16R:GENL\n"
    ":20C::SEME//GWSEC0000000010\n"
    ":23G:NEWM\n"
    ":16S:GENL\n"
    ":16R:TRADDET\n"
    ":98A::SETT//20260729\n"
    ":35B:ISIN XX0000000001\n"
    "GENERIC EQUITY NPV\n"
    ":16S:TRADDET\n"
    "-}"
)

# A full MT545 receive confirmation: SEME plus RELA linking back to the MT541,
# and — like all confirmations — no :25D: statuses.
_MT545 = (
    "{1:F01GWLABXXA0XXX0000000000}{2:O5450900260729CPTYBBB0XXXN}{4:\n"
    ":16R:GENL\n"
    ":20C::SEME//GWSEC0000000020\n"
    ":23G:NEWM\n"
    ":16R:LINK\n"
    ":20C::RELA//GWSEC0000000010\n"
    ":16S:LINK\n"
    ":16S:GENL\n"
    "-}"
)


def test_parse_message_extracts_two_statuses() -> None:
    block4 = (
        ":16R:GENL\n"
        ":20C::SEME//GWSEC0000000030\n"
        ":16R:LINK\n"
        ":20C::RELA//GWSEC0000000010\n"
        ":16S:LINK\n"
        ":16R:STAT\n"
        ":25D::IPRC//PACK\n"
        ":16S:STAT\n"
        ":16R:STAT\n"
        ":25D::MTCH//MACH\n"
        ":16S:STAT\n"
        ":16S:GENL\n"
    )
    msg = parse_message("548", tokenize(block4), _PUT_TIME)

    assert msg.mt == "548"
    assert msg.seme == "GWSEC0000000030"
    assert msg.rela == "GWSEC0000000010"
    assert {(s.qualifier, s.value) for s in msg.statuses} == {("IPRC", "PACK"), ("MTCH", "MACH")}
    # in-flight statuses without a REAS sub-block carry no reason
    assert all(s.reason is None for s in msg.statuses)
    assert msg.is_instruction is False
    assert msg.is_confirmation is False


def test_parse_message_pairs_reason_from_reas_subblock() -> None:
    block4 = (
        ":16R:GENL\n"
        ":20C::SEME//GWSEC0000000031\n"
        ":16R:STAT\n"
        ":25D::IPRC//REJT\n"
        ":16R:REAS\n"
        ":24B::REJT//NARR\n"
        ":16S:REAS\n"
        ":16S:STAT\n"
        ":16S:GENL\n"
    )
    msg = parse_message("548", tokenize(block4), _PUT_TIME)

    assert msg.statuses == (StatusItem("IPRC", "REJT", "NARR"),)


def test_parse_swift_models_full_instruction_envelope() -> None:
    msg = parse_swift(_MT541, _PUT_TIME)

    assert msg.mt == "541"
    assert msg.seme == "GWSEC0000000010"
    assert msg.rela is None
    assert msg.statuses == ()
    assert msg.put_time == _PUT_TIME
    assert msg.is_instruction is True
    assert msg.is_confirmation is False


def test_parse_swift_models_full_confirmation_envelope() -> None:
    msg = parse_swift(_MT545, _PUT_TIME)

    assert msg.mt == "545"
    assert msg.seme == "GWSEC0000000020"
    assert msg.rela == "GWSEC0000000010"
    assert msg.statuses == ()
    assert msg.is_instruction is False
    assert msg.is_confirmation is True


def test_status_without_a_qualifier_is_a_loud_failure() -> None:
    # A :25D: with no :4!c qualifier is malformed; the model refuses to guess.
    block4 = ":16R:GENL\n:16R:STAT\n:25D:PACK\n:16S:STAT\n:16S:GENL\n"
    with pytest.raises(ValueError, match="no qualifier"):
        parse_message("548", tokenize(block4), _PUT_TIME)


def test_swiftmessage_is_a_frozen_value_type() -> None:
    a = SwiftMessage("541", "S1", None, (), _PUT_TIME, is_instruction=True, is_confirmation=False)
    b = SwiftMessage("541", "S1", None, (), _PUT_TIME, is_instruction=True, is_confirmation=False)
    assert a == b
