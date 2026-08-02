"""Segmented, audit-queue-health, and oldest-age metric exposition (design §7/§8).

Pure line-builders folded from the collector's per-cycle state:
:func:`render_segment_metrics` renders
:meth:`~gwlab.swift.stream.state.SettlementState.segment_counts` as the labelled
``swift_settlement_segment_total`` series; :func:`render_audit_queue_metrics`
renders the ``inquire_depth`` ``(current, max)`` readings as the three
``swift_audit_queue_*`` series (guarding the empty-queue ``max == 0`` divide);
and :func:`render_oldest_age_metric` renders
:meth:`~gwlab.swift.stream.state.SettlementState.oldest_age_seconds` as the
single ``swift_audit_oldest_age_seconds`` gauge. No MQ boundary, no clock.
"""

from __future__ import annotations

from gwlab.swift.stream.selfmetrics import (
    render_audit_queue_metrics,
    render_oldest_age_metric,
    render_segment_metrics,
)

# --- render_segment_metrics ---------------------------------------------------


def test_segment_metrics_emit_help_and_type_once_then_a_line_per_key() -> None:
    lines = render_segment_metrics(
        {
            ("settled", "other", "541"): 42,
            ("rejected", "rejection", "543"): 3,
        }
    )
    blob = "\n".join(lines)

    help_lines = [ln for ln in lines if ln.startswith("# HELP swift_settlement_segment_total")]
    type_lines = [ln for ln in lines if ln.startswith("# TYPE swift_settlement_segment_total")]
    assert len(help_lines) == 1
    assert type_lines == ["# TYPE swift_settlement_segment_total gauge"]
    assert 'swift_settlement_segment_total{state="settled",' in blob
    assert 'reason_family="other",msg_type="541"} 42' in blob
    assert 'swift_settlement_segment_total{state="rejected",' in blob
    assert 'reason_family="rejection",msg_type="543"} 3' in blob


def test_segment_metrics_are_sorted_for_a_stable_exposition() -> None:
    lines = render_segment_metrics(
        {
            ("settled", "other", "543"): 1,
            ("acknowledged", "other", "541"): 1,
        }
    )
    value_lines = [line for line in lines if line.startswith("swift_settlement_segment_total{")]
    assert value_lines == sorted(value_lines)  # deterministic, key-sorted order


def test_segment_metrics_with_no_segments_still_emit_help_and_type() -> None:
    lines = render_segment_metrics({})
    assert any(line.startswith("# HELP swift_settlement_segment_total") for line in lines)
    assert any(line.startswith("# TYPE swift_settlement_segment_total") for line in lines)
    assert not [line for line in lines if line.startswith("swift_settlement_segment_total{")]


# --- render_audit_queue_metrics -----------------------------------------------


def test_audit_queue_metrics_emit_depth_max_and_fill_ratio_per_queue() -> None:
    lines = render_audit_queue_metrics(
        {
            "GW.SEC.INSTRUCT.AUDIT": (1200, 5000),
            "GW.SEC.STATUS.AUDIT": (400, 5000),
        }
    )

    assert 'swift_audit_queue_depth{queue="GW.SEC.INSTRUCT.AUDIT"} 1200' in lines
    assert 'swift_audit_queue_max_depth{queue="GW.SEC.INSTRUCT.AUDIT"} 5000' in lines
    assert 'swift_audit_queue_fill_ratio{queue="GW.SEC.INSTRUCT.AUDIT"} 0.24' in lines
    assert 'swift_audit_queue_depth{queue="GW.SEC.STATUS.AUDIT"} 400' in lines
    assert 'swift_audit_queue_fill_ratio{queue="GW.SEC.STATUS.AUDIT"} 0.08' in lines


def test_audit_queue_metrics_help_and_type_emitted_once_per_metric_name() -> None:
    lines = render_audit_queue_metrics(
        {"GW.SEC.INSTRUCT.AUDIT": (1, 10), "GW.SEC.STATUS.AUDIT": (2, 10)}
    )
    for name in (
        "swift_audit_queue_depth",
        "swift_audit_queue_max_depth",
        "swift_audit_queue_fill_ratio",
    ):
        assert sum(1 for line in lines if line == f"# TYPE {name} gauge") == 1
        assert sum(1 for line in lines if line.startswith(f"# HELP {name} ")) == 1


def test_audit_queue_fill_ratio_guards_zero_max_depth() -> None:
    # An unconfigured / never-inquired queue reads (0, 0); fill_ratio must not divide by zero.
    lines = render_audit_queue_metrics({"GW.SEC.INSTRUCT.AUDIT": (0, 0)})
    assert 'swift_audit_queue_fill_ratio{queue="GW.SEC.INSTRUCT.AUDIT"} 0' in lines
    assert 'swift_audit_queue_depth{queue="GW.SEC.INSTRUCT.AUDIT"} 0' in lines
    assert 'swift_audit_queue_max_depth{queue="GW.SEC.INSTRUCT.AUDIT"} 0' in lines


def test_audit_queue_metrics_are_sorted_by_queue() -> None:
    lines = render_audit_queue_metrics(
        {"GW.SEC.STATUS.AUDIT": (1, 10), "GW.SEC.INSTRUCT.AUDIT": (1, 10)}
    )
    depth_lines = [line for line in lines if line.startswith("swift_audit_queue_depth{")]
    assert depth_lines == sorted(depth_lines)


def test_audit_queue_metrics_with_no_queues_still_emit_help_and_type() -> None:
    lines = render_audit_queue_metrics({})
    assert any(line == "# TYPE swift_audit_queue_depth gauge" for line in lines)
    assert not [line for line in lines if line.startswith("swift_audit_queue_depth{")]


# --- render_oldest_age_metric -------------------------------------------------


def test_oldest_age_metric_is_a_single_labelless_gauge() -> None:
    lines = render_oldest_age_metric(300.0)
    assert "# TYPE swift_audit_oldest_age_seconds gauge" in lines
    assert any(line.startswith("# HELP swift_audit_oldest_age_seconds ") for line in lines)
    assert "swift_audit_oldest_age_seconds 300.0" in lines


def test_oldest_age_metric_zero_when_window_empty() -> None:
    lines = render_oldest_age_metric(0.0)
    assert "swift_audit_oldest_age_seconds 0.0" in lines
