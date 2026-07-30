"""Tests for the ISO 15022 block-4 tokenizer and envelope/MT extraction."""

from __future__ import annotations

import pytest

from gwlab.swift.tokenizer import (
    Block,
    Field,
    FieldTree,
    TokenizeError,
    parse_envelope,
    tokenize,
)

# A realistic (fully anonymised — invented data only) MT541 envelope.
_MT541 = (
    "{1:F01GWLABXXA0XXX0000000000}{2:I541CPTYAAA0XXXN}{4:\n"
    ":16R:GENL\n"
    ":20C::SEME//GWSEC0000000001\n"
    ":23G:NEWM\n"
    ":16R:LINK\n"
    ":20C::RELA//NONREF\n"
    ":16S:LINK\n"
    ":16S:GENL\n"
    ":16R:TRADDET\n"
    ":98A::SETT//20260729\n"
    ":35B:ISIN XX0000000001\n"
    "GENERIC EQUITY NPV\n"
    ":16S:TRADDET\n"
    ":16R:SETDET\n"
    ":16R:SETPRTY\n"
    ":95R::DEAG/ECLR/24943\n"
    ":16S:SETPRTY\n"
    ":16S:SETDET\n"
    "-}"
)


def test_parses_genl_block_with_seme() -> None:
    tree = tokenize(":16R:GENL\n:20C::SEME//REF00000001\n:16S:GENL\n")
    assert tree.blocks[0].name == "GENL"
    seme = tree.find("20C", "SEME")
    assert len(seme) == 1
    assert seme[0].value == "REF00000001"


def test_unqualified_field_has_no_qualifier() -> None:
    tree = tokenize(":16R:GENL\n:23G:NEWM\n:16S:GENL\n")
    field = tree.find("23G")[0]
    assert field.qualifier is None
    assert field.value == "NEWM"


def test_data_source_scheme_qualifier_is_split() -> None:
    tree = tokenize(":16R:SETPRTY\n:95R::DEAG/ECLR/24943\n:16S:SETPRTY\n")
    field = tree.find("95R", "DEAG")[0]
    assert field.value == "ECLR/24943"


def test_multi_line_value_is_joined() -> None:
    tree = tokenize(":16R:TRADDET\n:35B:ISIN XX0000000001\nGENERIC EQUITY NPV\n:16S:TRADDET\n")
    field = tree.find("35B")[0]
    assert field.value == "ISIN XX0000000001\nGENERIC EQUITY NPV"


def test_nested_link_block_is_reachable_by_find() -> None:
    tree = tokenize(":16R:GENL\n:20C::SEME//S1\n:16R:LINK\n:20C::RELA//S0\n:16S:LINK\n:16S:GENL\n")
    genl = tree.blocks[0]
    assert genl.subblocks[0].name == "LINK"
    # find recurses into sub-blocks
    assert tree.find("20C", "RELA")[0].value == "S0"
    # both 20C fields (SEME in GENL, RELA in LINK) are found without a qualifier
    assert len(tree.find("20C")) == 2


def test_find_with_no_match_returns_empty() -> None:
    tree = tokenize(":16R:GENL\n:23G:NEWM\n:16S:GENL\n")
    assert tree.find("99Z") == []


def test_blank_and_terminator_lines_are_ignored() -> None:
    tree = tokenize(":16R:GENL\n\n:23G:NEWM\n:16S:GENL\n-\n")
    assert tree.find("23G")[0].value == "NEWM"


def test_unbalanced_close_with_no_open_block_raises() -> None:
    with pytest.raises(TokenizeError, match="unbalanced"):
        tokenize(":16S:GENL\n")


def test_unbalanced_close_with_wrong_name_raises() -> None:
    with pytest.raises(TokenizeError, match="unbalanced"):
        tokenize(":16R:GENL\n:16S:LINK\n")


def test_unclosed_block_raises() -> None:
    with pytest.raises(TokenizeError, match="unclosed"):
        tokenize(":16R:GENL\n:20C::SEME//S1\n")


def test_field_outside_any_block_raises() -> None:
    with pytest.raises(TokenizeError, match="outside any block"):
        tokenize(":20C::SEME//S1\n")


def test_stray_line_outside_a_field_raises() -> None:
    with pytest.raises(TokenizeError, match="stray line"):
        tokenize("not a field line\n")


def test_parse_envelope_reads_input_mt_and_block4() -> None:
    mt, block4 = parse_envelope(_MT541)
    assert mt == "541"
    assert block4.startswith("\n:16R:GENL")
    assert block4.rstrip().endswith(":16S:SETDET")


def test_parse_envelope_reads_output_mt() -> None:
    mt, _block4 = parse_envelope("{1:F01X}{2:O5480900...}{4:\n:16R:GENL\n:16S:GENL\n-}")
    assert mt == "548"


def test_parse_envelope_without_block2_raises() -> None:
    with pytest.raises(TokenizeError, match="Block 2"):
        parse_envelope("{4:\n:16R:GENL\n:16S:GENL\n-}")


def test_parse_envelope_without_block4_raises() -> None:
    with pytest.raises(TokenizeError, match="no Block 4"):
        parse_envelope("{1:F01X}{2:I541CPTYAAA0XXXN}")


def test_parse_envelope_unterminated_block4_raises() -> None:
    with pytest.raises(TokenizeError, match="not terminated"):
        parse_envelope("{2:I541CPTYAAA0XXXN}{4:\n:16R:GENL\n:16S:GENL\n")


def test_full_envelope_then_tokenize_round_trip() -> None:
    mt, block4 = parse_envelope(_MT541)
    assert mt == "541"
    tree = tokenize(block4)
    assert [b.name for b in tree.blocks] == ["GENL", "TRADDET", "SETDET"]
    assert tree.find("20C", "SEME")[0].value == "GWSEC0000000001"
    assert tree.find("20C", "RELA")[0].value == "NONREF"
    # the SETPRTY sub-block under SETDET is reachable
    assert tree.find("95R", "DEAG")[0].value == "ECLR/24943"


def test_dataclasses_compose_as_value_types() -> None:
    field = Field("20C", "SEME", "X")
    tree = FieldTree([Block("GENL", [field], [])])
    assert tree.blocks[0].fields[0].tag == "20C"
    # frozen value semantics: equal Fields compare equal, and find returns them
    assert tree.find("20C", "SEME") == [field]
    assert field == Field("20C", "SEME", "X")
