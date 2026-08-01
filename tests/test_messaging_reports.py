"""Tests for the report-request surface on the Messaging port.

Covers the value types epic #105 adds to :mod:`gwlab.messaging`: the ``feedback``
field on :class:`~gwlab.messaging.Message` (the transport-milestone kind on a
returned COA/COD report), the ``ReportOpts`` request tuple attached to a put, and
the MQMD constants whose values must match IBM MQ ``CMQC`` so the pymqi adapter
maps them straight onto the descriptor.
"""

from __future__ import annotations

from gwlab.messaging import (
    MQFB_COA,
    MQFB_COD,
    MQRO_COA,
    MQRO_COD,
    Message,
    ReportOpts,
)


def test_message_feedback_defaults_to_none() -> None:
    # A normal application message carries no feedback.
    assert Message(body=b"trade-1").feedback is None


def test_message_feedback_is_settable() -> None:
    report = Message(body=b"", correl_id=b"MID-1", feedback=MQFB_COA)
    assert report.feedback == MQFB_COA


def test_report_opts_fields_are_positional() -> None:
    opts = ReportOpts(MQRO_COA | MQRO_COD, "GW.SEC.REPORT", "OURQM")
    assert opts.report == MQRO_COA | MQRO_COD
    assert opts.reply_to_q == "GW.SEC.REPORT"
    assert opts.reply_to_qmgr == "OURQM"


def test_report_option_constants_match_cmqc_values() -> None:
    # The plain COA/COD request bits and the feedback codes, as IBM MQ defines
    # them — these values flow straight onto the live MQMD.
    assert MQRO_COA == 0x00000100
    assert MQRO_COD == 0x00000800
    assert MQFB_COA == 259
    assert MQFB_COD == 260
