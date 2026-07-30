"""Tests for the responder directive codec (encode/decode round-trip + guards)."""

from __future__ import annotations

import pytest

from gwlab.swift.directive import (
    Directive,
    DirectiveError,
    decode_directive,
    encode_directive,
)


def test_encode_clean_has_the_empty_reason_double_space() -> None:
    assert encode_directive(Directive("clean", "", (0, 0, 0))) == "GWDIR clean  0 0 0"


def test_encode_fail_with_reason_and_delays() -> None:
    assert encode_directive(Directive("fail", "LACK", (30, 60, 0))) == "GWDIR fail LACK 30 60 0"


@pytest.mark.parametrize(
    "directive",
    [
        Directive("clean", "", (0, 0, 0)),
        Directive("reject", "NARR", (5, 0, 0)),
        Directive("fail", "MONY", (30, 60, 90)),
        Directive("clean", "", (0, 0, 86_400)),
    ],
)
def test_round_trips_exactly(directive: Directive) -> None:
    assert decode_directive(encode_directive(directive)) == directive


def test_encode_of_decode_is_stable() -> None:
    line = "GWDIR fail LACK 30 60 0"
    assert encode_directive(decode_directive(line)) == line


def test_decode_clean_line_yields_empty_reason() -> None:
    directive = decode_directive("GWDIR clean  0 0 0")
    assert directive.outcome == "clean"
    assert directive.reason == ""
    assert directive.delay_vector == (0, 0, 0)


def test_decode_rejects_empty_line() -> None:
    with pytest.raises(DirectiveError):
        decode_directive("")


def test_decode_rejects_wrong_tag() -> None:
    with pytest.raises(DirectiveError):
        decode_directive("NOPE clean 0 0 0")


def test_decode_rejects_too_few_fields() -> None:
    with pytest.raises(DirectiveError):
        decode_directive("GWDIR clean 0 0")


def test_decode_rejects_too_many_fields() -> None:
    with pytest.raises(DirectiveError):
        decode_directive("GWDIR fail LACK 0 0 0 0")


def test_decode_rejects_unknown_outcome() -> None:
    with pytest.raises(DirectiveError):
        decode_directive("GWDIR bogus HUH 0 0 0")


def test_decode_rejects_non_integer_delay() -> None:
    with pytest.raises(DirectiveError):
        decode_directive("GWDIR fail LACK x 0 0")
