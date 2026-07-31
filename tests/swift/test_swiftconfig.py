"""Tests for the SWIFT queue set and settlement horizon config."""

from __future__ import annotations

from gwlab.swift.swiftconfig import Horizon, SwiftQueueSet


def test_queue_set_defaults_are_the_gw_sec_audits() -> None:
    queues = SwiftQueueSet()
    assert queues.instruct_audit == "GW.SEC.INSTRUCT.AUDIT"
    assert queues.status_audit == "GW.SEC.STATUS.AUDIT"


def test_queue_set_names_are_overridable() -> None:
    queues = SwiftQueueSet(instruct_audit="A", status_audit="B")
    assert (queues.instruct_audit, queues.status_audit) == ("A", "B")


def test_horizon_default_covers_a_settlement_cycle() -> None:
    assert Horizon().days == 3
    assert Horizon(days=5).days == 5


def test_horizon_compression_defaults_to_real_time_and_is_overridable() -> None:
    assert Horizon().compression == 1.0  # production default: real time
    assert Horizon(days=3, compression=60.0).compression == 60.0
