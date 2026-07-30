"""Tests for the read-only collector (browse both audits -> DayReport).

Message bodies are full, invented, anonymised ``{1:}{2:}{4:}`` envelopes
(Counterparty A/B BICs, ``GWSEC...`` references, ``XX...`` ISINs) seeded onto a
:class:`FakeMessaging` via ordinary puts + commit — exactly how the live audit
copies would be browsed.
"""

from __future__ import annotations

from datetime import datetime

from gwlab.fake_messaging import FakeMessaging
from gwlab.messaging import Message
from gwlab.swift.collect import collect
from gwlab.swift.reference import State
from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet

_QUEUES = SwiftQueueSet()
_HORIZON = Horizon(days=3)
_NOW = datetime(2026, 7, 29, 17, 0)


def _instruction(seme: str, mt: str = "541") -> str:
    return (
        f"{{1:F01GWLABXXA0XXX0000000000}}{{2:I{mt}CPTYAAA0XXXN}}{{4:\n"
        ":16R:GENL\n"
        f":20C::SEME//{seme}\n"
        ":23G:NEWM\n"
        ":16S:GENL\n"
        ":16R:TRADDET\n"
        ":98A::SETT//20260729\n"
        ":35B:ISIN XX0000000001\n"
        "GENERIC EQUITY NPV\n"
        ":16S:TRADDET\n"
        "-}"
    )


def _status(own_seme: str, rela: str, statuses: list[tuple[str, str]], mt: str = "548") -> str:
    stat_blocks = ""
    for qualifier, value in statuses:
        stat_blocks += f":16R:STAT\n:25D::{qualifier}//{value}\n:16S:STAT\n"
    return (
        f"{{1:F01GWLABXXA0XXX0000000000}}{{2:O{mt}0900260729CPTYBBB0XXXN}}{{4:\n"
        ":16R:GENL\n"
        f":20C::SEME//{own_seme}\n"
        ":16R:LINK\n"
        f":20C::RELA//{rela}\n"
        ":16S:LINK\n"
        f"{stat_blocks}"
        ":16S:GENL\n"
        "-}"
    )


def _confirmation(own_seme: str, rela: str, mt: str = "545") -> str:
    return (
        f"{{1:F01GWLABXXA0XXX0000000000}}{{2:O{mt}0900260729CPTYBBB0XXXN}}{{4:\n"
        ":16R:GENL\n"
        f":20C::SEME//{own_seme}\n"
        ":16R:LINK\n"
        f":20C::RELA//{rela}\n"
        ":16S:LINK\n"
        ":16S:GENL\n"
        "-}"
    )


# A body whose only field sits outside any :16R:/:16S: block: it tokenizes to a
# TokenizeError (a ValueError) -> a "malformed" anomaly.
_UNTOKENIZABLE = (
    "{1:F01GWLABXXA0XXX0000000000}{2:O5480900260729CPTYBBB0XXXN}{4:\n"
    ":20C::SEME//GWSECBADBAD000001\n"
    "-}"
)

# An MT548 whose :25D: status carries no qualifier: it tokenizes cleanly but the
# model raises ValueError -> a "malformed" anomaly (spec's model-level guard).
_UNQUALIFIED_STATUS = (
    "{1:F01GWLABXXA0XXX0000000000}{2:O5480900260729CPTYBBB0XXXN}{4:\n"
    ":16R:GENL\n"
    ":20C::SEME//GWSEC0000000099\n"
    ":16R:STAT\n"
    ":25D:PACK\n"
    ":16S:STAT\n"
    ":16S:GENL\n"
    "-}"
)


def _seed(fake: FakeMessaging, queue: str, body: str, put_time: datetime | None) -> None:
    fake.put(queue, Message(body=body.encode(), put_time=put_time))


def test_late_confirmation_still_folds_to_settled() -> None:
    fake = FakeMessaging()
    instructed = datetime(2026, 7, 27, 9, 0)
    accepted = datetime(2026, 7, 27, 9, 5)
    confirmed = datetime(2026, 7, 29, 9, 0)  # T+2: late, but still in the window
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000010"), instructed)
    _seed(
        fake,
        _QUEUES.status_audit,
        _status("GWSEC0000000021", "GWSEC0000000010", [("IPRC", "PACK")]),
        accepted,
    )
    _seed(
        fake, _QUEUES.status_audit, _confirmation("GWSEC0000000022", "GWSEC0000000010"), confirmed
    )
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert [state.current for state in report.states] == [State.SETTLED]
    assert report.anomalies == []  # the late confirmation is NOT an orphan
    assert report.aged_out == []


def test_nonterminal_past_horizon_lands_in_aged_out_not_anomalies() -> None:
    fake = FakeMessaging()
    instructed = datetime(2026, 7, 26, 17, 0)  # exactly horizon days before now
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000030"), instructed)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert [state.seme for state in report.aged_out] == ["GWSEC0000000030"]
    assert report.states[0].current is State.INSTRUCTED
    assert report.anomalies == []


def test_scanned_counts_every_browsed_message() -> None:
    fake = FakeMessaging()
    recent = datetime(2026, 7, 29, 9, 0)
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000040"), recent)
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000041"), recent)
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000042", "GWSEC0000000040"), recent)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert report.scanned == 3


def test_pre_window_instruction_is_excluded() -> None:
    fake = FakeMessaging()
    stale = datetime(2026, 7, 24, 17, 0)  # 5 days before now, outside the horizon
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000050"), stale)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert report.states == []
    assert report.scanned == 1  # browsed, then filtered out of the window


def test_graceful_degradation_reports_valid_despite_bad_messages() -> None:
    fake = FakeMessaging()
    recent = datetime(2026, 7, 29, 9, 0)
    overlong_seme = "GWSEC" + "A" * 16  # 21 chars: a :20C::SEME// format violation
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000060"), recent)
    _seed(fake, _QUEUES.instruct_audit, _instruction(overlong_seme), recent)
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000061", "GWSEC0000000060"), recent)
    _seed(fake, _QUEUES.status_audit, _UNTOKENIZABLE, recent)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    kinds = [anomaly.kind for anomaly in report.anomalies]
    assert kinds.count("malformed") == 1
    assert kinds.count("field-violation") == 1
    assert len(report.anomalies) == 2
    # Both valid instructions are still reported; the one bad message never aborts.
    by_seme = {state.seme: state.current for state in report.states}
    assert by_seme["GWSEC0000000060"] is State.SETTLED
    assert by_seme[overlong_seme] is State.INSTRUCTED
    assert report.scanned == 4


def test_orphan_response_becomes_an_orphan_anomaly() -> None:
    fake = FakeMessaging()
    recent = datetime(2026, 7, 29, 9, 0)
    _seed(fake, _QUEUES.status_audit, _confirmation("GWSEC0000000071", "GWSECORPHAN00000"), recent)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert report.states == []
    assert [(a.kind, a.reference) for a in report.anomalies] == [("orphan", "GWSECORPHAN00000")]


def test_unreachable_status_becomes_a_transition_anomaly() -> None:
    fake = FakeMessaging()
    instructed = datetime(2026, 7, 28, 9, 0)
    confirmed = datetime(2026, 7, 28, 10, 0)
    late_match = datetime(2026, 7, 28, 11, 0)  # asserts MATCHED after SETTLED: unreachable
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000080"), instructed)
    _seed(
        fake, _QUEUES.status_audit, _confirmation("GWSEC0000000081", "GWSEC0000000080"), confirmed
    )
    _seed(
        fake,
        _QUEUES.status_audit,
        _status("GWSEC0000000082", "GWSEC0000000080", [("MTCH", "MACH")]),
        late_match,
    )
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert report.states[0].current is State.SETTLED
    transitions = [a for a in report.anomalies if a.kind == "transition"]
    assert [a.reference for a in transitions] == ["GWSEC0000000080"]


def test_unqualified_status_becomes_a_malformed_anomaly() -> None:
    fake = FakeMessaging()
    recent = datetime(2026, 7, 29, 9, 0)
    _seed(fake, _QUEUES.status_audit, _UNQUALIFIED_STATUS, recent)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert report.states == []
    assert [anomaly.kind for anomaly in report.anomalies] == ["malformed"]


def test_message_with_no_put_time_becomes_a_malformed_anomaly() -> None:
    fake = FakeMessaging()  # no clock: a put-time of None stays None
    _seed(fake, _QUEUES.instruct_audit, _instruction("GWSEC0000000090"), None)
    fake.commit()

    report = collect(fake, _QUEUES, _NOW, _HORIZON)

    assert [anomaly.kind for anomaly in report.anomalies] == ["malformed"]
    assert "put-time" in report.anomalies[0].detail
