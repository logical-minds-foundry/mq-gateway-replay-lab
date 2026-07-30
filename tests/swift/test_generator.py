"""Tests for the deterministic day generator and its format-validity gate (§4)."""

from __future__ import annotations

from collections import Counter

from gwlab.swift.directive import decode_directive, encode_directive
from gwlab.swift.fields import validate_tree
from gwlab.swift.generator import (
    DEFAULT_MIX,
    GeneratedInstruction,
    Mix,
    generate,
)
from gwlab.swift.tokenizer import parse_envelope, tokenize

_TARGET = 1024


def _category(instruction: GeneratedInstruction) -> str:
    """Classify a generated instruction back into its scenario category."""
    if instruction.directive.outcome != "clean":
        return instruction.directive.outcome
    if instruction.directive.delay_vector[2] >= DEFAULT_MIX.slow_settle_delay:
        return "slow"
    return "clean"


def test_validity_gate_every_message_parses_tokenizes_and_validates() -> None:
    batch = generate(200, target_size_bytes=_TARGET)
    assert batch
    for instruction in batch:
        mt, block4 = parse_envelope(instruction.text)
        assert mt == instruction.mt
        assert mt.isdigit()
        tree = tokenize(block4)  # must not raise
        assert validate_tree(tree) == []


def test_directive_round_trips_and_lives_in_the_70e_narrative() -> None:
    for instruction in generate(50, target_size_bytes=_TARGET, seed=7):
        assert decode_directive(encode_directive(instruction.directive)) == instruction.directive
        _, block4 = parse_envelope(instruction.text)
        narrative = tokenize(block4).find("70E", "DECL")
        assert len(narrative) == 1
        first_line = narrative[0].value.splitlines()[0]
        assert decode_directive(first_line) == instruction.directive


def test_message_size_is_within_tolerance_of_target() -> None:
    target = 1500
    tolerance = 16
    for instruction in generate(100, target_size_bytes=target, seed=3):
        size = len(instruction.text.encode("ascii"))
        assert abs(size - target) <= tolerance


def test_semes_are_unique_and_zero_padded() -> None:
    batch = generate(500)
    semes = [instruction.seme for instruction in batch]
    assert len(set(semes)) == len(semes)
    assert semes[0] == "GWSEC0000000000"
    assert all(seme.startswith("GWSEC") for seme in semes)


def test_message_types_are_settlement_directions() -> None:
    for instruction in generate(200, seed=5):
        assert instruction.mt in DEFAULT_MIX.mts


def test_generation_is_deterministic() -> None:
    first = generate(100, target_size_bytes=_TARGET, seed=42)
    second = generate(100, target_size_bytes=_TARGET, seed=42)
    assert first == second


def test_different_seeds_differ() -> None:
    assert generate(100, seed=1) != generate(100, seed=2)


def test_mix_proportions_are_respected_over_a_large_batch() -> None:
    counts = Counter(_category(instruction) for instruction in generate(5000, seed=11))
    total = sum(counts.values())
    assert abs(counts["clean"] / total - DEFAULT_MIX.clean) < 0.03
    assert abs(counts["reject"] / total - DEFAULT_MIX.reject) < 0.02
    assert abs(counts["fail"] / total - DEFAULT_MIX.fail) < 0.02
    assert abs(counts["slow"] / total - DEFAULT_MIX.slow) < 0.02


def test_reject_only_mix_yields_reject_directives() -> None:
    mix = Mix(clean=0.0, reject=1.0, fail=0.0, slow=0.0, ack_delay=5)
    for instruction in generate(30, mix=mix, seed=0):
        assert instruction.directive.outcome == "reject"
        assert instruction.directive.reason in mix.reject_reasons
        assert instruction.directive.delay_vector == (5, 0, 0)


def test_fail_only_mix_yields_fail_directives() -> None:
    mix = Mix(clean=0.0, reject=0.0, fail=1.0, slow=0.0, ack_delay=1, match_delay=2, settle_delay=3)
    for instruction in generate(30, mix=mix, seed=0):
        assert instruction.directive.outcome == "fail"
        assert instruction.directive.reason in mix.fail_reasons
        assert instruction.directive.delay_vector == (1, 2, 3)


def test_slow_only_mix_yields_clean_with_big_settle_delay() -> None:
    mix = Mix(clean=0.0, reject=0.0, fail=0.0, slow=1.0, slow_settle_delay=99_999)
    for instruction in generate(30, mix=mix, seed=0):
        assert instruction.directive.outcome == "clean"
        assert instruction.directive.delay_vector[2] == 99_999


def test_clean_only_mix_yields_clean_directives() -> None:
    mix = Mix(clean=1.0, reject=0.0, fail=0.0, slow=0.0)
    for instruction in generate(30, mix=mix, seed=0):
        assert instruction.directive.outcome == "clean"
        assert instruction.directive.delay_vector == (0, 0, 0)


def test_small_target_skips_padding_but_stays_valid() -> None:
    # A target below the base envelope size exercises the no-padding branch.
    for instruction in generate(5, target_size_bytes=1, seed=0):
        assert len(instruction.text.encode("ascii")) > 1
        _, block4 = parse_envelope(instruction.text)
        tree = tokenize(block4)
        assert validate_tree(tree) == []
        narrative = tree.find("70E", "DECL")[0]
        assert narrative.value == encode_directive(instruction.directive)
