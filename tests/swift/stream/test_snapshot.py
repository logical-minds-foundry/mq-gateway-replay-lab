"""Tests for the streaming collector's atomic snapshot holder (design §6).

Pure logic: a single-attribute reference swap. These tests exercise the
``publish`` / ``latest`` round-trip — the atomicity guarantee itself is the GIL's
(a bare reference assignment), not something a unit test can meaningfully assert.
"""

from __future__ import annotations

from gwlab.swift.stream.snapshot import SnapshotHolder


def test_holder_returns_the_initial_text_before_any_publish() -> None:
    holder = SnapshotHolder(initial="# empty")

    assert holder.latest() == "# empty"


def test_holder_returns_the_latest_published_text() -> None:
    holder = SnapshotHolder(initial="# empty")

    holder.publish("# v1")
    assert holder.latest() == "# v1"

    holder.publish("# v2")
    assert holder.latest() == "# v2"
