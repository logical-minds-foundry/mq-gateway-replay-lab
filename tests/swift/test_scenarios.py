"""The seven deterministic failure episodes and their isolating signatures."""

from datetime import datetime

import pytest

from gwlab.swift import model
from gwlab.swift.directive import Directive
from gwlab.swift.generator import GeneratedInstruction
from gwlab.swift.reasons import ReasonFamily, classify
from gwlab.swift.scenarios import (
    _CLUSTER,
    SCENARIOS,
    _baseline_responses,
    _instruct_queue,
    _status_queue,
)
from gwlab.swift.swiftconfig import SwiftQueueSet

_EXPECTED_NAMES = frozenset(
    {
        "counterparty_outage",
        "csd_incident",
        "lack_of_securities",
        "stale_ssi",
        "rejection_burst",
        "funding_squeeze",
        "silent_degradation",
    }
)

_EXPECTED_FAMILY = {
    "counterparty_outage": ReasonFamily.MATCHING,
    "csd_incident": ReasonFamily.SETTLEMENT,
    "lack_of_securities": ReasonFamily.SETTLEMENT,
    "stale_ssi": ReasonFamily.MATCHING,
    "rejection_burst": ReasonFamily.REJECTION,
    "funding_squeeze": ReasonFamily.CASH,
    "silent_degradation": ReasonFamily.MATCHING,
}

_WHEN = datetime(2026, 7, 29)


def _families(pairs: list[tuple[str, str]]) -> list[ReasonFamily]:
    """Classify every status/confirmation message in an episode's status stream."""
    out: list[ReasonFamily] = []
    for alias, text in pairs:
        if alias != _status_queue():
            continue
        family = classify(model.parse_swift(text, _WHEN))
        if family is not None:
            out.append(family)
    return out


def test_seven_named_scenarios() -> None:
    assert len(SCENARIOS) == 7
    assert set(SCENARIOS) == _EXPECTED_NAMES


def test_scenario_name_matches_its_key() -> None:
    for name, scenario in SCENARIOS.items():
        assert scenario.name == name


def test_rejection_burst_isolates_on_rejection() -> None:
    sc = SCENARIOS["rejection_burst"]
    assert sc.expected_family is ReasonFamily.REJECTION


@pytest.mark.parametrize(("name", "family"), _EXPECTED_FAMILY.items())
def test_expected_family(name: str, family: ReasonFamily) -> None:
    assert SCENARIOS[name].expected_family is family


def test_every_scenario_declares_an_isolating_dimension() -> None:
    for scenario in SCENARIOS.values():
        assert scenario.isolating_dimension


def test_episode_is_deterministic() -> None:
    assert SCENARIOS["lack_of_securities"].episode(3) == SCENARIOS["lack_of_securities"].episode(3)


def test_every_episode_is_deterministic() -> None:
    for scenario in SCENARIOS.values():
        assert scenario.episode(11) == scenario.episode(11)


def test_distinct_seeds_diverge() -> None:
    sc = SCENARIOS["counterparty_outage"]
    assert sc.episode(1) != sc.episode(2)


def test_episode_uses_only_the_two_audit_aliases() -> None:
    queues = SwiftQueueSet()
    aliases = {alias for alias, _ in SCENARIOS["csd_incident"].episode(0)}
    assert aliases == {queues.instruct_audit, queues.status_audit}


def test_instructions_go_to_instruct_audit_statuses_to_status_audit() -> None:
    for alias, text in SCENARIOS["funding_squeeze"].episode(0):
        parsed = model.parse_swift(text, _WHEN)
        if parsed.is_instruction:
            assert alias == _instruct_queue()
        else:
            assert alias == _status_queue()


@pytest.mark.parametrize(("name", "family"), _EXPECTED_FAMILY.items())
def test_signature_carries_the_expected_family(name: str, family: ReasonFamily) -> None:
    families = _families(SCENARIOS[name].episode(7))
    assert family in families


def test_concentrated_families_clear_the_cluster_floor() -> None:
    # Each concentrated (non-time-boxed) burst injects at least _CLUSTER copies of
    # its signature, so the family count clears that floor even above baseline noise.
    for name in ("counterparty_outage", "stale_ssi", "funding_squeeze", "rejection_burst"):
        families = _families(SCENARIOS[name].episode(5))
        assert families.count(_EXPECTED_FAMILY[name]) >= _CLUSTER


def test_funding_squeeze_and_lack_of_securities_split_on_reason() -> None:
    # Same SETT//PENF status; the injected 24B reason (CMON vs LACK) is the only
    # thing that separates the cash squeeze from the securities-lack chain.
    cash = _families(SCENARIOS["funding_squeeze"].episode(2))
    settle = _families(SCENARIOS["lack_of_securities"].episode(2))
    assert cash.count(ReasonFamily.CASH) >= _CLUSTER
    assert settle.count(ReasonFamily.SETTLEMENT) >= _CLUSTER


def _gi(outcome: str, reason: str, mt: str = "541") -> GeneratedInstruction:
    return GeneratedInstruction(
        seme="GWSEC0000000042",
        mt=mt,
        directive=Directive(outcome, reason, (0, 0, 0)),
        text="",
    )


def test_baseline_clean_yields_a_confirmation() -> None:
    pairs = _baseline_responses(_gi("clean", "", "543"))
    assert len(pairs) == 3
    parsed = [model.parse_swift(text, _WHEN) for _, text in pairs]
    assert parsed[-1].is_confirmation


def test_baseline_reject_yields_a_rejection() -> None:
    pairs = _baseline_responses(_gi("reject", "NARR"))
    assert [classify(model.parse_swift(t, _WHEN)) for _, t in pairs] == [ReasonFamily.REJECTION]


def test_baseline_fail_carries_its_reason() -> None:
    settle = _baseline_responses(_gi("fail", "LACK"))
    cash = _baseline_responses(_gi("fail", "MONY"))
    assert classify(model.parse_swift(settle[-1][1], _WHEN)) is ReasonFamily.SETTLEMENT
    assert classify(model.parse_swift(cash[-1][1], _WHEN)) is ReasonFamily.CASH
