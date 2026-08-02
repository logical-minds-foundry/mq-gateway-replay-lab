"""The scenario catalog runner: deterministic metric dumps and per-scenario pages."""

from __future__ import annotations

import pytest

from gwlab.swift.catalog import (
    metric_dump,
    render_catalog_page,
    render_index,
    write_catalog,
)
from gwlab.swift.scenarios import SCENARIOS


def test_metric_dump_is_deterministic() -> None:
    assert metric_dump("lack_of_securities", 3) == metric_dump("lack_of_securities", 3)


def test_every_metric_dump_is_deterministic() -> None:
    for name in SCENARIOS:
        assert metric_dump(name, 7) == metric_dump(name, 7)


def test_metric_dump_distinct_seeds_diverge() -> None:
    assert metric_dump("counterparty_outage", 1) != metric_dump("counterparty_outage", 2)


def test_metric_dump_carries_scenario_identity() -> None:
    dump = metric_dump("rejection_burst", 0)
    assert dump["scenario"] == "rejection_burst"
    assert dump["seed"] == 0
    assert dump["expected_family"] == "rejection"
    assert dump["isolating_dimension"] == SCENARIOS["rejection_burst"].isolating_dimension


def test_metric_dump_captures_segments_and_deviations() -> None:
    dump = metric_dump("lack_of_securities", 0)
    assert dump["segments"], "segment_counts must be captured"
    # The concentrated family must deviate above its baseline (positive z).
    assert dump["deviations"]["settlement"] > 3.0
    # Segments are a sorted list of the (state, reason_family, msg_type) picture.
    seg = dump["segments"][0]
    assert set(seg) == {"state", "reason_family", "msg_type", "count"}


def test_metric_dump_unknown_scenario_raises() -> None:
    with pytest.raises(KeyError):
        metric_dump("no_such_scenario", 0)


def test_render_catalog_page_names_the_isolating_dimension() -> None:
    for name, scenario in SCENARIOS.items():
        page = render_catalog_page(scenario, metric_dump(name, 0))
        assert scenario.isolating_dimension in page


def test_render_catalog_page_names_the_scenario_and_family() -> None:
    scenario = SCENARIOS["funding_squeeze"]
    page = render_catalog_page(scenario, metric_dump("funding_squeeze", 0))
    assert "funding_squeeze" in page
    assert str(scenario.expected_family) in page


def test_render_catalog_page_references_the_live_screenshot_path() -> None:
    page = render_catalog_page(SCENARIOS["csd_incident"], metric_dump("csd_incident", 0))
    assert "img/csd_incident.png" in page


def test_render_catalog_page_shows_deviation_evidence() -> None:
    page = render_catalog_page(SCENARIOS["rejection_burst"], metric_dump("rejection_burst", 0))
    assert "deviation z" in page
    assert "| rejection |" in page


def test_render_index_lists_every_scenario() -> None:
    index = render_index()
    for name in SCENARIOS:
        assert name in index


def test_write_catalog_writes_seven_pages_and_an_index(tmp_path) -> None:  # noqa: ANN001
    paths = write_catalog(tmp_path, seed=0)
    written = {p.name for p in paths}
    assert "index.md" in written
    for name in SCENARIOS:
        assert f"{name}.md" in written
    assert (tmp_path / "index.md").read_text(encoding="utf-8")
    for name in SCENARIOS:
        assert (tmp_path / f"{name}.md").read_text(encoding="utf-8")


def test_write_catalog_is_byte_reproducible(tmp_path) -> None:  # noqa: ANN001
    first = {p.name: p.read_text(encoding="utf-8") for p in write_catalog(tmp_path / "a", seed=0)}
    second = {p.name: p.read_text(encoding="utf-8") for p in write_catalog(tmp_path / "b", seed=0)}
    assert first == second
