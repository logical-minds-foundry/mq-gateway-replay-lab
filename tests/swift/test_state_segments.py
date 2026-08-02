"""Macro-segment accounting and oldest-age in :class:`SettlementState`."""

from datetime import datetime

from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.stream.state import SettlementState


def _instr(seme: str, mt: str = "541") -> SwiftMessage:
    return SwiftMessage(mt, seme, None, (), datetime(2026, 1, 1), True, False)


def _resp(rela: str, *st: StatusItem) -> SwiftMessage:
    return SwiftMessage("548", None, rela, st, datetime(2026, 1, 1, 0, 1), False, False)


def test_segment_counts_key_on_state_family_msgtype() -> None:
    s = SettlementState()
    s.ingest(_instr("A", "541"))
    s.ingest(_resp("A", StatusItem("IPRC", "REJT", "NARR")))
    counts = s.segment_counts()
    assert counts[("rejected", "rejection", "541")] == 1


def test_segment_counts_benign_response_keeps_other_family() -> None:
    # A benign advice (IPRC//PACK) classifies to None, so the entry keeps its
    # default OTHER family while advancing to acknowledged.
    s = SettlementState()
    s.ingest(_instr("A", "541"))
    s.ingest(_resp("A", StatusItem("IPRC", "PACK", None)))
    counts = s.segment_counts()
    assert counts[("acknowledged", "other", "541")] == 1


def test_oldest_age_seconds_from_window() -> None:
    s = SettlementState()
    s.ingest(_instr("A", "541"))  # put_time = 2026-01-01 00:00:00
    assert s.oldest_age_seconds(datetime(2026, 1, 1, 0, 5)) == 300.0


def test_oldest_age_seconds_empty_is_zero() -> None:
    s = SettlementState()
    assert s.oldest_age_seconds(datetime(2026, 1, 1)) == 0.0
