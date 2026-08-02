"""Classify the exception family a settlement response signals (macro, non-sensitive)."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gwlab.swift.model import SwiftMessage


class ReasonFamily(StrEnum):
    SETTLEMENT = "settlement"  # lack of securities
    CASH = "cash"  # lack of cash
    MATCHING = "matching"  # unmatched / disagreement
    REJECTION = "rejection"  # validation reject
    OTHER = "other"


_CASH_REASONS = frozenset({"MONY", "CMON"})
_SETTLEMENT_REASONS = frozenset({"LACK", "CLAC"})


def classify(message: SwiftMessage) -> ReasonFamily | None:
    """The exception family this response signals, or None if it is benign."""
    for status in message.statuses:
        if status.qualifier == "IPRC" and status.value == "REJT":
            return ReasonFamily.REJECTION
        if status.qualifier == "MTCH" and status.value == "NMAT":
            return ReasonFamily.MATCHING
        if status.qualifier == "SETT" and status.value == "PENF":
            if status.reason in _CASH_REASONS:
                return ReasonFamily.CASH
            return ReasonFamily.SETTLEMENT
    return None
