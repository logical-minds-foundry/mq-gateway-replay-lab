from datetime import datetime

from gwlab.swift.model import StatusItem, SwiftMessage
from gwlab.swift.reasons import ReasonFamily, classify


def _msg(*statuses: StatusItem) -> SwiftMessage:
    return SwiftMessage(
        mt="548",
        seme=None,
        rela="R1",
        statuses=statuses,
        put_time=datetime(2026, 1, 1),
        is_instruction=False,
        is_confirmation=False,
    )


def test_rejection_family():
    assert classify(_msg(StatusItem("IPRC", "REJT", "NARR"))) is ReasonFamily.REJECTION


def test_matching_family():
    assert classify(_msg(StatusItem("MTCH", "NMAT", "DSEC"))) is ReasonFamily.MATCHING


def test_settlement_vs_cash_family():
    assert classify(_msg(StatusItem("SETT", "PENF", "LACK"))) is ReasonFamily.SETTLEMENT
    assert classify(_msg(StatusItem("SETT", "PENF", "CMON"))) is ReasonFamily.CASH


def test_benign_status_has_no_family():
    assert classify(_msg(StatusItem("IPRC", "PACK", None))) is None
