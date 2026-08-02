"""The scenario catalog runner: reproducible metric dumps + per-scenario pages.

Task 12 (spec §6, Unit 6). Each of the seven :data:`gwlab.swift.scenarios.SCENARIOS`
episodes is driven **offline** through an in-memory
:class:`~gwlab.swift.stream.state.SettlementState` — every ``(queue_alias, text)``
pair parsed with :func:`gwlab.swift.model.parse_swift` and folded via
:meth:`~gwlab.swift.stream.state.SettlementState.ingest` — then its macro picture is
captured: the ``(state, reason_family, msg_type)`` :meth:`segment_counts` and the
per-reason-family :meth:`deviations` z-scores against the calibrated
:data:`~gwlab.swift.stream.state.REASON_FAMILY_BASELINES`.

:func:`metric_dump` is the **reproducible source of truth**: deterministic given a
scenario name and seed (no wall clock — a fixed :data:`_PUT_TIME` is passed into the
parser, per the determinism rule). :func:`render_catalog_page` turns one dump into a
per-scenario markdown page (what broke, the SWIFT/MQ signature, the isolating macro
dimension, and which board register reveals it), referencing the screenshot path the
**live** validation bookend captures (the PNG is not produced here — the metric dump
is; spec §6, plan Task V). :func:`write_catalog` writes the seven pages plus an
``index.md`` under a catalog directory.

Everything is invented and anonymised — generic vocabulary, ``GWSEC…`` references,
``Counterparty`` direction only (epic #65, anonymization rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, TypedDict

from gwlab.swift.model import parse_swift
from gwlab.swift.scenarios import SCENARIOS
from gwlab.swift.stream.state import REASON_FAMILY_BASELINES, SettlementState

if TYPE_CHECKING:
    from pathlib import Path

    from gwlab.swift.scenarios import Scenario

#: The fixed put-time stamped on every parsed episode message. A constant — never
#: ``datetime.now()`` — so a dump is byte-reproducible from name + seed alone. The
#: catalog never ages the window, so the exact instant is immaterial to the counts;
#: it only has to be stable.
_PUT_TIME = datetime(2026, 7, 29)  # noqa: DTZ001 — deterministic stamp, not a clock read

#: The seed the committed catalog pages are generated with (reproducible baseline).
_DEFAULT_SEED = 0

#: Relative directory (under the catalog dir) the live validation bookend drops each
#: scenario's dashboard screenshot into; the page links to it by name.
_IMG_DIR = "img"


class _Segment(TypedDict):
    """One captured macro segment of the retained window."""

    state: str
    reason_family: str
    msg_type: str
    count: int


class _Dump(TypedDict):
    """The reproducible metric dump for one scenario episode (the source of truth)."""

    scenario: str
    seed: int
    expected_family: str
    isolating_dimension: str
    segments: list[_Segment]
    deviations: dict[str, float]


@dataclass(frozen=True)
class _Narrative:
    """The prose the metric dump cannot supply: cause, signature, and where it shows.

    ``what_broke`` is the plain-language incident; ``signature`` the concrete SWIFT
    status chain / MQ shape the audit copies carry; ``register`` the board register
    and panel that raises it above the baseline noise (spec §5, Registers A/B/C).
    """

    what_broke: str
    signature: str
    register: str


#: Per-scenario narrative, keyed by scenario name. Grounded in the scenario
#: signatures (:mod:`gwlab.swift.scenarios`) and the three-register board
#: (Task 7 Register A normal-day, Task 10 Register B exceptional-event, Task 7
#: Register C infra-health).
_NARRATIVE: dict[str, _Narrative] = {
    "counterparty_outage": _Narrative(
        what_broke=(
            "A counterparty stops responding mid-session. Instructions are accepted at "
            "input but never progress to matched, so a cluster of trades hangs unmatched "
            "while the rest of the day settles normally."
        ),
        signature=(
            "`IPRC//PACK` (accepted) followed by `MTCH//NMAT` (never matches) — and then "
            "silence. The instruction audit copies are present; their status audit copies "
            "stall on the matching hop."
        ),
        register=(
            "Register B — the per-reason-family deviation panel for the **matching** "
            "family lifts above its baseline z-band while efficiency (Register A) barely "
            "dips, because the concentration is one family, not the whole book."
        ),
    ),
    "csd_incident": _Narrative(
        what_broke=(
            "A central securities depository settlement cycle stalls. A block of matched "
            "trades all pend-fail on settlement together, concentrated in one time window "
            "rather than spread across the day."
        ),
        signature=(
            "`IPRC//PACK` then `MTCH//MACH` then a broad `SETT//PENF` with no distinguishing "
            "`:24B:` reason — a settlement pend-fail that arrives as a burst, not a trickle."
        ),
        register=(
            "Register B settlement-family deviation, read together with Register C's "
            "expiry-window-stability timeseries: the oldest-age and audit-queue-depth "
            "curves rise as the stalled block ages in the window — the time-and-state tell."
        ),
    ),
    "lack_of_securities": _Narrative(
        what_broke=(
            "Delivering trades cannot settle because the securities are not in the account "
            "— a classic lack-of-securities fail chain concentrated on one book."
        ),
        signature=(
            "`IPRC//PACK` then `MTCH//MACH` then `SETT//PENF` carrying the `LACK` reason "
            "code. The reason is the only thing separating this from a cash squeeze — same "
            "`SETT//PENF` status, different `:24B:` reason."
        ),
        register=(
            "Register B — the **settlement** reason-family deviation panel. The `LACK` "
            "reason routes the pend-fail into the settlement bucket, so the settlement "
            "family's z-score is what crosses the red threshold."
        ),
    ),
    "stale_ssi": _Narrative(
        what_broke=(
            "Stale standing settlement instructions drive a run of matching disagreements: "
            "the counterparty rejects the security detail, so trades match-fail rather than "
            "settle."
        ),
        signature=(
            "`IPRC//PACK` then `MTCH//NMAT` carrying a `DSEC` (security-detail) disagreement "
            "reason — accepted, then rejected at matching on a detail mismatch."
        ),
        register=(
            "Register B — the **matching** reason-family deviation panel. Shares the family "
            "with the counterparty outage, but the disagreement reason and the immediate "
            "(non-stalling) match-fail distinguish the cause on drill-down."
        ),
    ),
    "rejection_burst": _Narrative(
        what_broke=(
            "A bad deploy or reference-data glitch makes a burst of instructions fail input "
            "validation outright — they never enter the settlement lifecycle at all."
        ),
        signature=(
            "`IPRC//REJT` at input with a `NARR` reason, and no further status. Rejection is "
            "vanishingly rare on a normal day, so a 0→N burst is a loud step change."
        ),
        register=(
            "Register B's rare-event amplification panel for **rejection**: because the "
            "baseline is near zero, the deltas/sparkline make the burst unmissable where a "
            "raw count would stay visually flat."
        ),
    ),
    "funding_squeeze": _Narrative(
        what_broke=(
            "A cash funding shortfall bites the delivery-versus-payment leg: matched trades "
            "pend-fail for lack of cash on the settlement directions (MT541/MT543)."
        ),
        signature=(
            "`IPRC//PACK` then `MTCH//MACH` then `SETT//PENF` carrying the `CMON` (cash) "
            "reason on the DvP message types — same pend-fail status as a securities lack, "
            "split into the cash family by the `:24B:` reason."
        ),
        register=(
            "Register B **cash**-family deviation, cross-read with Register A's fails-by-"
            "msg_type panel: the concentration on the DvP directions is the second macro "
            "dimension (reason and msg_type) that pins the funding cause."
        ),
    ),
    "silent_degradation": _Narrative(
        what_broke=(
            "A slow, sustained rise in matching fails — no single spike, just a creeping "
            "deterioration over successive slices of the day. The hard case: any one window "
            "looks unremarkable."
        ),
        signature=(
            "Successive `IPRC//PACK` then `MTCH//NMAT` slices, each a little larger than the "
            "last. No burst clears the z-band on its own; the signal is the trend, not any "
            "one window."
        ),
        register=(
            "Register B's EWMA-trend panel for the **matching** family: the exponentially "
            "weighted residual accumulates the sustained drift across sweeps, surfacing what "
            "the per-window z-score alone washes out."
        ),
    ),
}


def metric_dump(scenario_name: str, seed: int) -> _Dump:
    """Drive one named episode through an in-memory state and capture its metrics.

    Deterministic given ``scenario_name`` and ``seed``: parses every episode message
    with the fixed :data:`_PUT_TIME`, folds it into a fresh
    :class:`~gwlab.swift.stream.state.SettlementState`, then reads the macro
    ``segment_counts`` and per-reason-family ``deviations`` — the reproducible source
    of truth a catalog page renders. Raises ``KeyError`` for an unknown scenario (a
    loud precondition, not a silent empty dump).
    """
    scenario = SCENARIOS[scenario_name]
    state = SettlementState()
    for _alias, text in scenario.episode(seed):
        state.ingest(parse_swift(text, _PUT_TIME))

    segments: list[_Segment] = [
        {"state": segment_state, "reason_family": family, "msg_type": msg_type, "count": count}
        for (segment_state, family, msg_type), count in sorted(state.segment_counts().items())
    ]
    deviations = {str(family): z for family, z in state.deviations(REASON_FAMILY_BASELINES).items()}
    return {
        "scenario": scenario_name,
        "seed": seed,
        "expected_family": str(scenario.expected_family),
        "isolating_dimension": scenario.isolating_dimension,
        "segments": segments,
        "deviations": deviations,
    }


def _deviation_table(deviations: dict[str, float]) -> str:
    """A markdown table of every scored reason family's deviation z-score."""
    rows = [f"| {family} | {z:.1f} |" for family, z in sorted(deviations.items())]
    return "\n".join(["| reason family | deviation z |", "|---|---|", *rows])


def _segment_table(segments: list[_Segment]) -> str:
    """A markdown table of the captured ``(state, reason_family, msg_type)`` picture."""
    rows = [
        f"| {s['state']} | {s['reason_family']} | {s['msg_type']} | {s['count']} |"
        for s in segments
    ]
    return "\n".join(["| state | reason family | msg_type | count |", "|---|---|---|---|", *rows])


def render_catalog_page(scenario: Scenario, dump: _Dump) -> str:
    """Render one scenario's markdown catalog page from its metric dump.

    Names the scenario's isolating macro dimension and expected reason family, states
    what broke and the SWIFT/MQ signature, points to the board register that reveals
    it, references the live-captured screenshot path, and prints the reproducible
    metric evidence (per-family deviation z-scores + the concentrated segments).
    """
    narrative = _NARRATIVE[scenario.name]
    return "\n".join(
        [
            f"# Scenario: {scenario.name}",
            "",
            f"- **Isolating dimension:** `{scenario.isolating_dimension}`",
            f"- **Expected reason family:** `{scenario.expected_family}`",
            f"- **Seed:** `{dump['seed']}` (metric dump is byte-reproducible)",
            "",
            "## What broke",
            "",
            narrative.what_broke,
            "",
            "## SWIFT / MQ signature",
            "",
            narrative.signature,
            "",
            "## Where it surfaces on the board",
            "",
            narrative.register,
            "",
            "## Live snapshot",
            "",
            "Captured against the live audit queues by the validation bookend:",
            "",
            f"![{scenario.name} dashboard]({_IMG_DIR}/{scenario.name}.png)",
            "",
            "## Metric evidence (reproducible source of truth)",
            "",
            "Per-reason-family deviation z-scores over the retained window:",
            "",
            _deviation_table(dump["deviations"]),
            "",
            "Captured macro segments:",
            "",
            _segment_table(dump["segments"]),
            "",
        ]
    )


def render_index() -> str:
    """Render the catalog ``index.md`` — one row per scenario, linking each page."""
    rows = [
        f"| [{name}]({name}.md) | `{scenario.isolating_dimension}` | `{scenario.expected_family}` |"
        for name, scenario in SCENARIOS.items()
    ]
    return "\n".join(
        [
            "# Scenario catalog",
            "",
            "Seven deterministic settlement-failure episodes, each isolating on a bounded "
            "macro dimension so a rare, concentrated incident separates from the diffuse "
            "background of routine fails. The metric dump behind each page is the "
            "reproducible source of truth; the dashboard screenshot is captured live during "
            "validation.",
            "",
            "| scenario | isolating dimension | expected reason family |",
            "|---|---|---|",
            *rows,
            "",
        ]
    )


def write_catalog(out_dir: Path, *, seed: int = _DEFAULT_SEED) -> list[Path]:
    """Write the seven per-scenario pages plus ``index.md`` under ``out_dir``.

    Returns the paths written (pages then index). Byte-reproducible given ``seed``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, scenario in SCENARIOS.items():
        page = render_catalog_page(scenario, metric_dump(name, seed))
        path = out_dir / f"{name}.md"
        path.write_text(page, encoding="utf-8")
        written.append(path)
    index_path = out_dir / "index.md"
    index_path.write_text(render_index(), encoding="utf-8")
    written.append(index_path)
    return written
