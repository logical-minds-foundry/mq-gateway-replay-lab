"""The seven named failure episodes — a deterministic scenario-injection harness.

Each :class:`Scenario` renders one reproducible *episode*: a realistic settlement
day (the calibrated baseline from :func:`gwlab.swift.generator.generate_realistic`)
with a single **concentrated perturbation** layered on top — a burst of one
failure signature biasing exactly one macro dimension, while the rest of the flow
stays at baseline (spec §7, Unit 2). An episode is a flat, ordered list of
``(queue_alias, message_text)`` pairs: instructions land on the outbound
instruction-audit alias, their MT548 status advices and MT544-547 confirmations on
the inbound status-audit alias — the two audit copies the collector browses
(:class:`gwlab.swift.swiftconfig.SwiftQueueSet`).

The macro reframe: incidents are *concentrations*. Every episode isolates on a
bounded macro dimension (a reason family, a message type, a time window), so the
collector detects the *class* of problem — the drill-down to *who*/*which* lives in
the full-detail application system. The baseline itself carries routine settlement
and cash fails (the noise); the perturbation is the signal that must separate from
it by reason family (and, for #7, only via the EWMA slow-drift residual).

The concentrated reason (``LACK`` vs ``CMON``) is what splits an otherwise
identical ``SETT//PENF`` into the *settlement* vs *cash* family — carried as a
``:24B:`` reason in a ``REAS`` sub-block of the status' STAT sequence, exactly
where :mod:`gwlab.swift.model` pairs it. ``render_status`` emits no reason, so a
reason-carrying status is composed from it here (:func:`_reason_status`).

Everything is deterministic given ``seed`` and fully invented/anonymised
(``GWSEC…`` references, ``Counterparty`` direction only) — no wall clock, no
unseeded random, no client-identifiable content (epic #65, anonymization rule).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift import corpus
from gwlab.swift.generator import generate_realistic
from gwlab.swift.reasons import ReasonFamily
from gwlab.swift.swiftconfig import SwiftQueueSet

if TYPE_CHECKING:
    from collections.abc import Callable

    from gwlab.swift.generator import GeneratedInstruction

#: The two audit aliases every episode targets (the collector browses these).
_QUEUES = SwiftQueueSet()

#: Instructions to settle, in DvP directions — the message types a funding
#: squeeze bites on (spec §7 #6). The generator draws the same pair.
_DVP_MTS: tuple[str, ...] = ("541", "543")

#: Instruction MT -> its settlement-confirmation MT (540↔544 … 543↔547).
_CONFIRM_MT: dict[str, str] = {"540": "544", "541": "545", "542": "546", "543": "547"}

#: ``:24B:`` reason qualifiers (inert to the collector's field catalog, so they
#: never false-flag; present for message realism). The *value* is what
#: :func:`gwlab.swift.reasons.classify` reads.
_PENDING_QUAL = "PENR"  # settlement/cash pending reason
_REJECT_QUAL = "REPR"  # reason for rejection
_MATCH_QUAL = "NMAT"  # matching-disagreement reason

#: Instructions in one realistic baseline day (small enough for fast, deterministic
#: episodes; large enough that a burst of ``_CLUSTER`` reads as a concentration).
_BASELINE_COUNT = 240

#: Size of a concentrated burst — the extra failing instructions a spike-shaped
#: scenario injects. A burst of this size clears the routine baseline noise.
_CLUSTER = 20

#: Number of ramp slices for the slow-degradation episode; slice ``k`` injects
#: ``k`` matching fails, so the rate creeps up rather than spiking (spec §7 #7).
_RAMP_SLICES = 6

#: Reference-number base for injected (perturbation) instructions, offset well
#: clear of the baseline's ``GWSEC{0..count}`` block so no ``SEME`` collides.
_PERTURB_BASE = 1_000_000

#: Offset applied to a scenario seed for its perturbation RNG, so the burst's
#: draws do not shadow the baseline generator's stream. Deterministic per seed.
_PERTURB_SEED_OFFSET = 90_001


def _instruct_queue() -> str:
    """The outbound instruction-audit alias episodes put instructions on."""
    return _QUEUES.instruct_audit


def _status_queue() -> str:
    """The inbound status-audit alias episodes put advices/confirmations on."""
    return _QUEUES.status_audit


def _perturb_seme(index: int) -> str:
    """A collision-free ``SEME`` for the ``index``-th injected instruction."""
    return f"GWSEC{_PERTURB_BASE + index:010d}"


def _reason_status(rela: str, qualifier: str, code: str, reason: str, reason_qual: str) -> str:
    """A one-status MT548 carrying a ``:24B:`` reason in a ``REAS`` sub-block.

    ``render_status`` emits only ``:25D:`` lines; the reason is what separates a
    settlement fail from a cash fail (both ``SETT//PENF``), so it is composed in by
    nesting a ``REAS`` block inside the single STAT sequence — where
    :func:`gwlab.swift.model._reason_of` reads it back and pairs it to the status.
    """
    text = corpus.render_status(rela=rela, statuses=[(qualifier, code)])
    reas = "\n:16R:REAS\n:24B::" + reason_qual + "//" + reason + "\n:16S:REAS"
    return text.replace("\n:16S:STAT", reas + "\n:16S:STAT")


def _status_pair(text: str) -> tuple[str, str]:
    """Tag a rendered response with the status-audit alias."""
    return (_status_queue(), text)


def _instruction_pair(seme: str, mt: str) -> tuple[str, str]:
    """Render an injected instruction and tag it with the instruction-audit alias."""
    return (_instruct_queue(), corpus.render_instruction(mt, seme))


def _baseline_responses(gi: GeneratedInstruction) -> list[tuple[str, str]]:
    """The counterparty's realistic response stream for one baseline instruction.

    Mirrors the responder recipes: a rejection stops at input; a fail accepts,
    matches, then pends-fails carrying its reason (``LACK``/``MONY`` → settlement /
    cash noise); a clean settlement accepts, matches and confirms.
    """
    rela = gi.seme
    if gi.directive.outcome == "reject":
        return [
            _status_pair(_reason_status(rela, "IPRC", "REJT", gi.directive.reason, _REJECT_QUAL))
        ]
    if gi.directive.outcome == "fail":
        return [
            _status_pair(corpus.render_status(rela=rela, statuses=[("IPRC", "PACK")])),
            _status_pair(corpus.render_status(rela=rela, statuses=[("MTCH", "MACH")])),
            _status_pair(_reason_status(rela, "SETT", "PENF", gi.directive.reason, _PENDING_QUAL)),
        ]
    return [
        _status_pair(corpus.render_status(rela=rela, statuses=[("IPRC", "PACK")])),
        _status_pair(corpus.render_status(rela=rela, statuses=[("MTCH", "MACH")])),
        _status_pair(corpus.render_confirmation(mt=_CONFIRM_MT[gi.mt], rela=rela)),
    ]


def _burst(
    rng: random.Random,
    count: int,
    respond: Callable[[str], list[str]],
) -> list[tuple[str, str]]:
    """Inject ``count`` failing instructions, each with a fixed response signature."""
    pairs: list[tuple[str, str]] = []
    for index in range(count):
        seme = _perturb_seme(index)
        mt = rng.choice(_DVP_MTS)  # noqa: S311
        pairs.append(_instruction_pair(seme, mt))
        pairs.extend(_status_pair(text) for text in respond(seme))
    return pairs


def _outage_signature(seme: str) -> list[str]:
    """Accepted, then never matches — a counterparty gone dark (``MTCH//NMAT``)."""
    return [
        corpus.render_status(rela=seme, statuses=[("IPRC", "PACK")]),
        corpus.render_status(rela=seme, statuses=[("MTCH", "NMAT")]),
    ]


def _csd_signature(seme: str) -> list[str]:
    """Accepted, matched, then a broad settlement pend-fail (a CSD cycle stall)."""
    return [
        corpus.render_status(rela=seme, statuses=[("IPRC", "PACK")]),
        corpus.render_status(rela=seme, statuses=[("MTCH", "MACH")]),
        corpus.render_status(rela=seme, statuses=[("SETT", "PENF")]),
    ]


def _lack_signature(seme: str) -> list[str]:
    """Matched, then pend-fails on ``LACK`` — a lack-of-securities fail chain."""
    return [
        corpus.render_status(rela=seme, statuses=[("IPRC", "PACK")]),
        corpus.render_status(rela=seme, statuses=[("MTCH", "MACH")]),
        _reason_status(seme, "SETT", "PENF", "LACK", _PENDING_QUAL),
    ]


def _stale_ssi_signature(seme: str) -> list[str]:
    """Accepted, then a matching disagreement on a security-detail (``DSEC``)."""
    return [
        corpus.render_status(rela=seme, statuses=[("IPRC", "PACK")]),
        _reason_status(seme, "MTCH", "NMAT", "DSEC", _MATCH_QUAL),
    ]


def _reject_signature(seme: str) -> list[str]:
    """Rejected at input validation on ``NARR`` — a bad-deploy / ref-data glitch."""
    return [_reason_status(seme, "IPRC", "REJT", "NARR", _REJECT_QUAL)]


def _funding_signature(seme: str) -> list[str]:
    """Matched, then pend-fails on ``CMON`` — lack of cash on the DvP leg."""
    return [
        corpus.render_status(rela=seme, statuses=[("IPRC", "PACK")]),
        corpus.render_status(rela=seme, statuses=[("MTCH", "MACH")]),
        _reason_status(seme, "SETT", "PENF", "CMON", _PENDING_QUAL),
    ]


def _degradation_ramp(rng: random.Random) -> list[tuple[str, str]]:
    """A slow ramp of matching fails: slice ``k`` injects ``k`` of them.

    No single slice spikes; the sustained rise only surfaces via the collector's
    EWMA residual (spec §7 #7) — the hard case the other six do not exercise.
    """
    pairs: list[tuple[str, str]] = []
    index = 0
    for slice_size in range(1, _RAMP_SLICES + 1):
        for _ in range(slice_size):
            seme = _perturb_seme(index)
            index += 1
            mt = rng.choice(_DVP_MTS)  # noqa: S311
            pairs.append(_instruction_pair(seme, mt))
            pairs.append(_status_pair(corpus.render_status(rela=seme, statuses=[("IPRC", "PACK")])))
            pairs.append(_status_pair(corpus.render_status(rela=seme, statuses=[("MTCH", "NMAT")])))
    return pairs


@dataclass(frozen=True)
class Scenario:
    """One named episode: its isolating macro dimension, signature, and injector.

    ``episode(seed)`` renders a realistic baseline day and layers this scenario's
    concentrated perturbation on top, returning the ordered ``(queue_alias,
    message_text)`` pairs the collector would browse. Deterministic given ``seed``.
    """

    name: str
    isolating_dimension: str
    expected_family: ReasonFamily
    perturbation: Callable[[random.Random], list[tuple[str, str]]]
    baseline_count: int = _BASELINE_COUNT

    def episode(self, seed: int) -> list[tuple[str, str]]:
        """The realistic baseline plus this scenario's concentrated perturbation."""
        pairs: list[tuple[str, str]] = []
        for gi in generate_realistic(self.baseline_count, seed=seed):
            pairs.append((_instruct_queue(), gi.text))
            pairs.extend(_baseline_responses(gi))
        pairs.extend(self.perturbation(random.Random(seed + _PERTURB_SEED_OFFSET)))  # noqa: S311
        return pairs


def _spike(
    count: int, respond: Callable[[str], list[str]]
) -> Callable[[random.Random], list[tuple[str, str]]]:
    """A perturbation that injects a fixed-size burst of one response signature."""
    return lambda rng: _burst(rng, count, respond)


#: The seven episodes (spec §7). Each isolates on the macro dimension named, and
#: declares the reason family its concentrated signature drives.
SCENARIOS: dict[str, Scenario] = {
    "counterparty_outage": Scenario(
        name="counterparty_outage",
        isolating_dimension="reason_family",
        expected_family=ReasonFamily.MATCHING,
        perturbation=_spike(_CLUSTER, _outage_signature),
    ),
    "csd_incident": Scenario(
        name="csd_incident",
        isolating_dimension="time_and_state",
        expected_family=ReasonFamily.SETTLEMENT,
        perturbation=_spike(_CLUSTER, _csd_signature),
    ),
    "lack_of_securities": Scenario(
        name="lack_of_securities",
        isolating_dimension="reason_family",
        expected_family=ReasonFamily.SETTLEMENT,
        perturbation=_spike(_CLUSTER, _lack_signature),
    ),
    "stale_ssi": Scenario(
        name="stale_ssi",
        isolating_dimension="reason_family",
        expected_family=ReasonFamily.MATCHING,
        perturbation=_spike(_CLUSTER, _stale_ssi_signature),
    ),
    "rejection_burst": Scenario(
        name="rejection_burst",
        isolating_dimension="reason_family",
        expected_family=ReasonFamily.REJECTION,
        perturbation=_spike(_CLUSTER, _reject_signature),
    ),
    "funding_squeeze": Scenario(
        name="funding_squeeze",
        isolating_dimension="reason_and_msg_type",
        expected_family=ReasonFamily.CASH,
        perturbation=_spike(_CLUSTER, _funding_signature),
    ),
    "silent_degradation": Scenario(
        name="silent_degradation",
        isolating_dimension="reason_family_trend",
        expected_family=ReasonFamily.MATCHING,
        perturbation=_degradation_ramp,
    ),
}
