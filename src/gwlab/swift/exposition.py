"""Shared Prometheus text-exposition line builders.

A single leaf helper that both the batch settlement exposition
(:func:`gwlab.swift.cliswift.render_exposition`) and the streaming collector's
self-metrics (:func:`gwlab.swift.stream.selfmetrics.render_collector_metrics`)
fold their gauge lines through, so the ``# HELP`` / ``# TYPE`` / value shape is
written once and stays identical across both exporters.
"""

from __future__ import annotations


def append_gauge(lines: list[str], name: str, help_text: str, value: object) -> None:
    """Append one single-value gauge (``# HELP``, ``# TYPE``, value) to ``lines``.

    Mutates ``lines`` in place — the caller owns the accumulator so the helper
    composes with the labelled and multi-line series each exposition also emits.
    """
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    lines.append(f"{name} {value}")
