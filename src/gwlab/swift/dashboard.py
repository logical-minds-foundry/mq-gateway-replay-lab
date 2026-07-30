"""The v2 board: a self-contained, state-machine-oriented settlement page.

Where v1's :mod:`gwlab.dashboard` code-templates a Grafana dict wired to live
exporter series, v2 renders a single self-contained HTML string (inline CSS, no
external assets) directly from a folded :class:`~gwlab.swift.observe.Metrics`
snapshot. It is the reporting face of spec §11, and it is deliberately
state-machine oriented rather than queue-depth oriented:

- **State distribution (hero)** — the funnel INSTRUCTED -> ACKNOWLEDGED ->
  MATCHED -> SETTLED, with REJECTED and FAILED broken out, each with its count.
- **Lifecycle latencies** — one row per inter-state hop, mean and p95.
- **Attention sets** — the in-flight-and-aging chase list, plus the REJECTED,
  FAILED and aged-out sets, each an instruction by its ``SEME`` with its current
  state and its age (``now`` minus the first hop's timestamp).
- **Anomalies** — the four surfaced-not-swallowed kinds, broken out, plus the
  total (the v1 Algorithm-3 analog).
- **Scan cost** — how many messages the browse scanned (the scale thread).

Everything is a pure function of the passed :class:`Metrics` and an explicit
``now``; there is no clock read and no I/O, so the rendered page is deterministic
and testable by string/DOM-contains, mirroring v1's board tests.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

from gwlab.swift.reference import PROGRESSIVE, State

if TYPE_CHECKING:
    from datetime import datetime

    from gwlab.swift.lifecycle import InstructionState
    from gwlab.swift.observe import LatencyStats, Metrics

#: The two terminal-negative states, in a fixed reporting order (REJECTED then
#: FAILED) so the hero's broken-out tail is stable across renders.
_NEGATIVE: tuple[State, ...] = (State.REJECTED, State.FAILED)

#: Attention-set section headers. Each is also the string the tests assert on.
_HEADER_AGING = "In-flight and aging"
_HEADER_REJECTED = "Rejected"
_HEADER_FAILED = "Failed"
_HEADER_AGED_OUT = "Aged out"

_STYLE = """
* { box-sizing: border-box; }
body { margin: 0; background: #0f1720; color: #e6edf3;
       font-family: system-ui, -apple-system, sans-serif; }
.board { max-width: 1100px; margin: 0 auto; padding: 24px; }
header h1 { margin: 0 0 4px; font-size: 20px; }
.ts { margin: 0 0 20px; color: #8b98a5; font-size: 13px; }
.panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px;
         padding: 16px; margin-bottom: 16px; }
.panel h2 { margin: 0 0 12px; font-size: 14px; text-transform: uppercase;
            letter-spacing: 0.05em; color: #8b98a5; }
.funnel, .negatives { display: flex; gap: 8px; flex-wrap: wrap; }
.negatives { margin-top: 8px; }
.stage { flex: 1 1 0; min-width: 120px; background: #21262d;
         border: 1px solid #30363d; border-radius: 6px; padding: 12px; }
.stage.neg { border-color: #6e2a2a; }
.stage .label { display: block; font-size: 11px; color: #8b98a5; }
.stage .count { display: block; font-size: 28px; font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #21262d; }
th { color: #8b98a5; font-weight: 600; }
tr.hot td { color: #f0883e; }
tr.cold td { color: #8b98a5; }
tr.total td { font-weight: 600; border-top: 1px solid #30363d; }
.empty { color: #6b7681; font-style: italic; }
.scan { margin: 0; font-size: 20px; font-weight: 600; }
"""


def _format_age(seconds: float) -> str:
    """Render an age in seconds as ``{d}d {hh}h {mm}m`` with no branching."""
    total = int(seconds)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    return f"{days}d {hours:02d}h {minutes:02d}m"


def _format_latency(seconds: float) -> str:
    """Render a hop latency as seconds with a minutes companion."""
    return f"{seconds:.1f}s ({seconds / 60:.1f}m)"


def _distribution_panel(distribution: dict[State, int]) -> list[str]:
    """Render the hero funnel plus the broken-out terminal-negative tail."""
    lines = ['<section class="panel hero">', "<h2>State distribution</h2>", '<div class="funnel">']
    for state in PROGRESSIVE:
        lines.append(
            f'<div class="stage"><span class="label">{state.name}</span>'
            f'<span class="count">{distribution[state]}</span></div>'
        )
    lines.append('</div><div class="negatives">')
    for state in _NEGATIVE:
        lines.append(
            f'<div class="stage neg"><span class="label">{state.name}</span>'
            f'<span class="count">{distribution[state]}</span></div>'
        )
    lines.append("</div></section>")
    return lines


def _latency_panel(latencies: list[LatencyStats]) -> list[str]:
    """Render one row per inter-state hop, or an empty-state note when none."""
    lines = ['<section class="panel">', "<h2>Lifecycle latencies</h2>"]
    if latencies:
        lines.append("<table><tr><th>Hop</th><th>Mean</th><th>p95</th></tr>")
        for stat in latencies:
            lines.append(
                f"<tr><td>{stat.hop}</td>"
                f"<td>{_format_latency(stat.mean_seconds)}</td>"
                f"<td>{_format_latency(stat.p95_seconds)}</td></tr>"
            )
        lines.append("</table>")
    else:
        lines.append('<p class="empty">No completed hops yet.</p>')
    lines.append("</section>")
    return lines


def _attention_panel(title: str, states: list[InstructionState], now: datetime) -> list[str]:
    """Render one named attention table: SEME, current state, age; oldest as given."""
    lines = [
        '<section class="panel">',
        f"<h2>{title}</h2>",
        "<table><tr><th>SEME</th><th>State</th><th>Age</th></tr>",
    ]
    if states:
        for state in states:
            age = _format_age((now - state.hops[0].at).total_seconds())
            lines.append(
                f"<tr><td>{html.escape(state.seme)}</td>"
                f"<td>{state.current.name}</td><td>{age}</td></tr>"
            )
    else:
        lines.append('<tr><td colspan="3" class="empty">none</td></tr>')
    lines.append("</table></section>")
    return lines


def _anomaly_panel(by_kind: dict[str, int], total: int) -> list[str]:
    """Render the four anomaly kinds broken out (hot when non-zero) plus the total."""
    lines = [
        '<section class="panel">',
        "<h2>Anomalies</h2>",
        "<table><tr><th>Kind</th><th>Count</th></tr>",
    ]
    for kind, count in by_kind.items():
        css = "hot" if count else "cold"
        lines.append(f'<tr class="{css}"><td>{html.escape(kind)}</td><td>{count}</td></tr>')
    lines.append(f'<tr class="total"><td>total</td><td>{total}</td></tr>')
    lines.append("</table></section>")
    return lines


def _scan_panel(scanned: int) -> list[str]:
    """Render the browse scan-cost line (the scale thread)."""
    return [
        '<section class="panel">',
        "<h2>Scan cost</h2>",
        f'<p class="scan">{scanned} messages scanned</p>',
        "</section>",
    ]


def render_board(metrics: Metrics, now: datetime) -> str:
    """Render a self-contained HTML settlement board from a :class:`Metrics` snapshot.

    Pure function of ``metrics`` and an explicit ``now`` (used for attention-set
    ages) — no clock read, no I/O, no external assets.
    """
    body: list[str] = [
        '<main class="board">',
        "<header><h1>SWIFT settlement lifecycle</h1>",
        f'<p class="ts">as of {now:%Y-%m-%d %H:%M}</p></header>',
    ]
    body.extend(_distribution_panel(metrics.distribution))
    body.extend(_latency_panel(metrics.latencies))
    body.extend(_attention_panel(_HEADER_AGING, metrics.aging, now))
    body.extend(_attention_panel(_HEADER_REJECTED, metrics.rejected, now))
    body.extend(_attention_panel(_HEADER_FAILED, metrics.failed, now))
    body.extend(_attention_panel(_HEADER_AGED_OUT, metrics.aged_out, now))
    body.extend(_anomaly_panel(metrics.anomalies_by_kind, metrics.anomalies))
    body.extend(_scan_panel(metrics.scanned))
    body.append("</main>")
    page = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>SWIFT settlement lifecycle</title>",
        f"<style>{_STYLE}</style></head><body>",
        "\n".join(body),
        "</body></html>",
    ]
    return "\n".join(page)
