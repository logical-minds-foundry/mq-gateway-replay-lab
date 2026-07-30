"""Tests for the format-valid lifecycle-path corpus (all data invented/anonymised)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from gwlab.swift.corpus import (
    Flow,
    all_flows,
    anomaly_flows,
    render_instruction,
)
from gwlab.swift.correlate import correlate
from gwlab.swift.fields import validate_tree
from gwlab.swift.lifecycle import fold
from gwlab.swift.model import SwiftMessage, parse_swift
from gwlab.swift.reference import CONFIRMATION_MTS, INSTRUCTION_MTS, SINK, STATUS_MT, State
from gwlab.swift.tokenizer import TokenizeError, parse_envelope, tokenize

_BASE = datetime(2026, 7, 29, 9, 0)


def _at(minute: int) -> datetime:
    return _BASE + timedelta(minutes=minute)


def _messages(flow: Flow) -> tuple[SwiftMessage, list[SwiftMessage]]:
    instruction = parse_swift(flow.instruction_text, _at(0))
    responses = [
        parse_swift(text, _at(index + 1)) for index, text in enumerate(flow.response_texts)
    ]
    return instruction, responses


def _anomaly(kind: str) -> Flow:
    return next(flow for flow in anomaly_flows() if flow.anomaly == kind)


def test_all_flow_messages_parse_and_are_catalog_valid() -> None:
    for flow in all_flows():
        for text in [flow.instruction_text, *flow.response_texts]:
            mt, block4 = parse_envelope(text)
            assert mt.isdigit()
            assert validate_tree(tokenize(block4)) == []


def test_instruction_and_responses_have_the_expected_message_types() -> None:
    for flow in all_flows():
        instruction, responses = _messages(flow)
        assert instruction.mt in INSTRUCTION_MTS
        assert instruction.is_instruction
        for response in responses:
            assert response.mt == STATUS_MT or response.mt in CONFIRMATION_MTS
            assert response.rela == instruction.seme


def test_folding_each_flow_reaches_its_expected_terminal() -> None:
    for flow in all_flows():
        instruction, responses = _messages(flow)
        correlated, orphans = correlate([instruction], responses)
        assert orphans == []
        assert len(correlated) == 1
        state = fold(correlated[0])
        assert state.anomalies == []
        if flow.expected_terminal is None:
            assert state.current not in SINK
        else:
            assert state.current is flow.expected_terminal


def test_flows_cover_every_lifecycle_state() -> None:
    seen: set[State] = set()
    for flow in all_flows():
        instruction, responses = _messages(flow)
        correlated, _ = correlate([instruction], responses)
        for hop in fold(correlated[0]).hops:
            seen.add(hop.state)
    assert seen == set(State)


def test_render_instruction_accepts_economic_overrides() -> None:
    text = render_instruction(
        "541",
        "GWSEC0000000001",
        isin="XX0000000002",
        desc="GENERIC BOND 4PCT",
        sett_date="20260801",
        qty="500,",
        acct="GWSAFE0002",
    )
    mt, block4 = parse_envelope(text)
    tree = tokenize(block4)
    assert mt == "541"
    assert validate_tree(tree) == []
    assert tree.find("35B")[0].value.startswith("ISIN XX0000000002")
    assert tree.find("97A", "SAFE")[0].value == "GWSAFE0002"


def test_orphan_anomaly_correlates_as_an_orphan() -> None:
    flow = _anomaly("orphan")
    instruction = parse_swift(flow.instruction_text, _at(0))
    response = parse_swift(flow.response_texts[0], _at(1))
    correlated, orphans = correlate([instruction], [response])
    assert len(orphans) == 1
    assert orphans[0].rela == response.rela
    assert all(item.responses == [] for item in correlated)


def test_malformed_anomaly_raises_tokenize_error() -> None:
    flow = _anomaly("malformed")
    with pytest.raises(TokenizeError):
        parse_swift(flow.response_texts[0], _at(0))


def test_field_violation_anomaly_has_a_nonempty_validate_tree() -> None:
    flow = _anomaly("field-violation")
    _, block4 = parse_envelope(flow.response_texts[0])
    violations = validate_tree(tokenize(block4))
    assert violations != []
    assert violations[0].key == "25D:IPRC"
