"""Tests for the field-spec catalog validator (all data invented/anonymised)."""

from __future__ import annotations

from gwlab.swift.fields import FieldViolation, validate_field, validate_tree
from gwlab.swift.tokenizer import Field, tokenize


def test_valid_seme_passes() -> None:
    assert validate_field(Field("20C", "SEME", "REF00000001")) is None


def test_seme_rejects_out_of_charset_value() -> None:
    # '!' is not in the SWIFT 'x' set (space and lowercase are).
    violation = validate_field(Field("20C", "SEME", "bad ref!"))
    assert violation == FieldViolation("20C:SEME", "bad ref!", "charset")


def test_unqualified_known_field_passes() -> None:
    # 23G has no qualifier -> catalog key is the bare tag.
    assert validate_field(Field("23G", None, "NEWM")) is None


def test_iprc_value_outside_domain_is_a_domain_violation() -> None:
    violation = validate_field(Field("25D", "IPRC", "ABCD"))
    assert violation == FieldViolation("25D:IPRC", "ABCD", "domain")


def test_iprc_value_inside_domain_passes() -> None:
    assert validate_field(Field("25D", "IPRC", "PACK")) is None


def test_fixed_length_overrun_is_a_format_violation() -> None:
    # A 4!c status code overrun: char-set clean, but one over the max length.
    violation = validate_field(Field("25D", "IPRC", "REJTX"))
    assert violation == FieldViolation("25D:IPRC", "REJTX", "format")


def test_unknown_field_key_is_not_a_violation() -> None:
    assert validate_field(Field("70E", "NARR", "FREE TEXT")) is None


def test_multiline_composite_field_is_lenient() -> None:
    field = Field("35B", None, "ISIN XX0000000001\nGENERIC EQUITY NPV")
    assert validate_field(field) is None


def test_decimal_amount_field_is_lenient() -> None:
    # 19A:SETT is a 'd' amount carrying a currency prefix; validated leniently, and
    # its SETT qualifier must NOT be domain-checked (that gate is 25D-only).
    assert validate_field(Field("19A", "SETT", "USD1000,00")) is None


def test_validate_tree_aggregates_violations() -> None:
    text = (
        ":16R:GENL\n"
        ":20C::SEME//bad ref!\n"
        ":16R:LINK\n"
        ":20C::RELA//NONREF\n"
        ":16S:LINK\n"
        ":25D::IPRC//ZZZZ\n"
        ":16S:GENL\n"
    )
    violations = validate_tree(tokenize(text))
    assert violations == [
        FieldViolation("20C:SEME", "bad ref!", "charset"),
        FieldViolation("25D:IPRC", "ZZZZ", "domain"),
    ]


def test_validate_tree_clean_returns_empty() -> None:
    text = ":16R:GENL\n:20C::SEME//REF00000001\n:23G:NEWM\n:16S:GENL\n"
    assert validate_tree(tokenize(text)) == []
