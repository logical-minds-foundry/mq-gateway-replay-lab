"""Tests for SEME/RELA correlation and orphan detection."""

from __future__ import annotations

from datetime import datetime, timedelta

from gwlab.swift.correlate import REASON_ORPHAN, Orphan, correlate
from gwlab.swift.model import SwiftMessage

_BASE = datetime(2026, 7, 29, 9, 0)


def _instruction(seme: str | None, minute: int = 0) -> SwiftMessage:
    return SwiftMessage("541", seme, None, (), _BASE + timedelta(minutes=minute), True, False)


def _response(rela: str | None, minute: int = 0) -> SwiftMessage:
    return SwiftMessage("548", None, rela, (), _BASE + timedelta(minutes=minute), False, False)


def test_groups_responses_and_flags_orphans() -> None:
    inst = _instruction("S0")
    early = _response("S0", minute=1)
    late = _response("S0", minute=2)
    stray = _response("ZZ", minute=3)
    # pass responses out of order to prove put-time sorting
    correlated, orphans = correlate([inst], [late, stray, early])

    assert len(correlated) == 1
    assert correlated[0].instruction is inst
    assert correlated[0].responses == [early, late]

    assert len(orphans) == 1
    assert orphans[0].rela == "ZZ"
    assert orphans[0].reason == REASON_ORPHAN


def test_instruction_with_no_responses_has_empty_list() -> None:
    correlated, orphans = correlate([_instruction("S0")], [])
    assert correlated[0].responses == []
    assert orphans == []


def test_response_with_no_rela_is_orphan() -> None:
    _correlated, orphans = correlate([_instruction("S0")], [_response(None)])
    assert orphans[0].rela == ""
    assert orphans[0].reason == REASON_ORPHAN


def test_nonref_response_is_orphan() -> None:
    _correlated, orphans = correlate([_instruction("S0")], [_response("NONREF")])
    assert orphans[0].rela == "NONREF"


def test_seme_less_instruction_is_skipped() -> None:
    correlated, orphans = correlate([_instruction(None)], [])
    assert correlated == []
    assert orphans == []


def test_orphan_default_reason() -> None:
    assert Orphan(rela="X", message=_response("X")).reason == REASON_ORPHAN
