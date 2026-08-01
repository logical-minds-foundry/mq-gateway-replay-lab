"""Tests for the in-memory FakeMessaging — exercising the UOW semantics."""

from datetime import UTC, datetime

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import MQFB_COA, MQFB_COD, MQRO_COA, MQRO_COD, Message, ReportOpts


def test_put_is_invisible_until_commit() -> None:
    fm = FakeMessaging()
    fm.put("Q", Message(b"a", b"1"))
    assert fm.browse("Q") == []
    fm.commit()
    assert [m.msg_id for m in fm.browse("Q")] == [b"1"]


def test_backout_discards_staged_puts() -> None:
    fm = FakeMessaging()
    fm.put("Q", Message(b"a", b"1"))
    fm.backout()
    fm.commit()
    assert fm.browse("Q") == []


def test_two_puts_one_commit_are_atomic() -> None:
    fm = FakeMessaging()
    fm.put("XMIT", Message(b"a", b"1"))
    fm.put("ARCH", Message(b"a", b"1"))
    fm.commit()
    assert len(fm.browse("XMIT")) == 1
    assert len(fm.browse("ARCH")) == 1


def test_get_and_copy_in_one_uow_consumes_and_audits() -> None:
    fm = FakeMessaging()
    fm.put("REPLY", Message(b"r", b"9"))
    fm.commit()

    got = fm.get("REPLY")
    assert got is not None
    assert got.msg_id == b"9"
    fm.put("REPLY.ARCH", got)
    fm.commit()

    assert fm.browse("REPLY") == []
    assert [m.msg_id for m in fm.browse("REPLY.ARCH")] == [b"9"]


def test_get_on_empty_queue_returns_none() -> None:
    assert FakeMessaging().get("EMPTY") is None


def test_get_then_backout_leaves_the_message() -> None:
    fm = FakeMessaging()
    fm.put("Q", Message(b"a", b"1"))
    fm.commit()
    fm.get("Q")
    fm.backout()
    assert len(fm.browse("Q")) == 1


def test_clock_stamps_only_unstamped_messages() -> None:
    fixed = datetime(2026, 7, 8, 12, 0, 0)  # noqa: DTZ001 — injected clock
    preset = datetime(2026, 7, 8, 8, 0, 0)  # noqa: DTZ001
    fm = FakeMessaging(clock=lambda: fixed)
    fm.put("Q", Message(b"a", b"1"))  # no put_time → stamped by the clock
    fm.put("Q", Message(b"b", b"2", put_time=preset))  # already stamped → kept
    fm.commit()
    stamped = {m.msg_id: m.put_time for m in fm.browse("Q")}
    assert stamped[b"1"] == fixed
    assert stamped[b"2"] == preset


def test_put_records_the_request_and_its_report_options() -> None:
    fm = FakeMessaging()
    opts = ReportOpts(MQRO_COA | MQRO_COD, "GW.SEC.REPORT", None)
    fm.put("GW.SEC.INSTRUCT", Message(b"instr", b"1"), report_opts=opts)
    fm.put("GW.SEC.OTHER", Message(b"plain", b"2"))  # no report options
    assert fm.puts[0].queue == "GW.SEC.INSTRUCT"
    assert fm.puts[0].report_opts == opts
    assert fm.puts[1].report_opts is None  # a plain put records no options


def test_enqueue_report_serves_a_body_less_report_over_browse_new() -> None:
    fm = FakeMessaging()
    ts = datetime(2026, 7, 8, 9, 30, 0, tzinfo=UTC)
    returned = fm.enqueue_report(
        "GW.SEC.REPORT", correl_id=b"MID-1", feedback=MQFB_COA, put_time=ts
    )
    (report,) = fm.browse_new("GW.SEC.REPORT")
    assert report is returned
    assert report.body == b""  # body-less — never tokenized
    assert report.correl_id == b"MID-1"  # = the instruction MsgId
    assert report.feedback == MQFB_COA
    assert report.put_time == ts


def test_enqueue_report_appends_to_the_ledger_across_calls() -> None:
    fm = FakeMessaging()
    ts = datetime(2026, 7, 8, 9, 30, 0, tzinfo=UTC)
    fm.enqueue_report("GW.SEC.REPORT", b"MID-1", MQFB_COA, ts)
    first = fm.browse_new("GW.SEC.REPORT")  # cursor advances past the COA
    fm.enqueue_report("GW.SEC.REPORT", b"MID-1", MQFB_COD, ts)
    second = fm.browse_new("GW.SEC.REPORT")  # only the new COD is delivered
    assert [m.feedback for m in first] == [MQFB_COA]
    assert [m.feedback for m in second] == [MQFB_COD]
