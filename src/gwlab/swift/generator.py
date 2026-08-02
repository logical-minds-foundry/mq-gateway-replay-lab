"""Deterministic day-of-instructions generator for the load simulation (§4-§5, §8).

Given a ``count``, a ``target_size_bytes``, and a scenario ``mix``,
:func:`generate` produces a reproducible list of :class:`GeneratedInstruction` —
each a full ``{1:}{2:}{4:}`` MT541/543 settlement instruction (built on the corpus
renderer, :func:`gwlab.swift.corpus.render_instruction`) carrying a unique
``SEME``, a processing directive (:mod:`gwlab.swift.directive`), and free-text
padding. The directive and the padding both live in a ``:70E:`` narrative field
inside a structurally-valid ``GENL`` block.

The collector never reads ``:70E:`` (it is not in the field catalog; correlation
keys on ``SEME``/``RELA``), so the directive and the padding are inert from its
view — but the message must still parse and validate, because the collector folds
it. Hence the generation-time format-validity gate (§4): every generated message,
at the target size, tokenizes clean and has an empty ``validate_tree``. The size
padding rides as ``:70E:`` continuation lines, so it can never break block-4
structure.

Everything is invented, anonymised, and deterministic given ``seed`` (epic #65,
anonymization rule).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift.corpus import render_instruction
from gwlab.swift.directive import Directive, encode_directive

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class Mix:
    """Scenario mix: outcome proportions and the delay shape of each (sim seconds).

    ``clean``/``reject``/``fail``/``slow`` are relative category weights (they need
    not sum to 1 — :func:`generate` normalises them). ``slow`` is not a fourth
    outcome (§5) but a *clean* settlement carrying a large settle delay, so the
    timing heavy tail is exercised through the same three-outcome vocabulary. The
    ``*_delay`` knobs are per-stage sim-time seconds; ``*_reasons`` are the reason
    codes drawn for a rejected/failing settlement; ``mts`` the settlement
    directions drawn from.
    """

    clean: float = 0.90
    reject: float = 0.04
    fail: float = 0.04
    slow: float = 0.02
    ack_delay: int = 0
    match_delay: int = 0
    settle_delay: int = 0
    slow_settle_delay: int = 86_400
    reject_reasons: tuple[str, ...] = ("NARR", "DQUA")
    fail_reasons: tuple[str, ...] = ("LACK", "MONY")
    mts: tuple[str, ...] = ("541", "543")


#: The default scenario mix (~90% clean, ~4% reject, ~4% fail, ~2% slow).
DEFAULT_MIX = Mix()

#: The mechanism (teaching) mix — today's inflated ``DEFAULT_MIX``, preserved so
#: the ``generate``/mechanism mode keeps demonstrating every outcome loudly.
MECHANISM_MIX = DEFAULT_MIX

#: Calibrated asset-class books (Task 1 reference). Weights are clean/reject/fail/slow.
REALISTIC_BOOKS: dict[str, Mix] = {
    "govvie": Mix(clean=0.965, reject=0.003, fail=0.020, slow=0.012, fail_reasons=("LACK", "MONY")),
    "equity": Mix(clean=0.955, reject=0.005, fail=0.028, slow=0.012, fail_reasons=("LACK", "MONY")),
    "etf": Mix(clean=0.800, reject=0.005, fail=0.180, slow=0.015, fail_reasons=("LACK", "MONY")),
}

#: Book share of the aggregate stream (invented, plausible back-office mix).
_BOOK_SHARE: dict[str, float] = {"govvie": 0.45, "equity": 0.45, "etf": 0.10}

#: Category names, aligned with the weight vector built in :func:`generate`.
_CATEGORIES = ("clean", "reject", "fail", "slow")

#: Padding-line width — a newline every ``_PAD_LINE_WIDTH + 1`` bytes keeps the
#: ``:70E:`` continuation lines SWIFT-ish while the run stays byte-exact.
_PAD_LINE_WIDTH = 63


@dataclass(frozen=True)
class GeneratedInstruction:
    """One generated instruction: its ``SEME``, MT, directive, and full envelope text."""

    seme: str
    mt: str
    directive: Directive
    text: str


def _pick(rng: random.Random, options: Sequence[str]) -> str:
    """Uniformly pick one option (simulation data — not cryptographic)."""
    return rng.choice(options)  # noqa: S311


def _draw_directive(rng: random.Random, category: str, mix: Mix) -> Directive:
    """Build the directive for a drawn ``category`` (§5: three outcomes × timing)."""
    if category == "reject":
        return Directive("reject", _pick(rng, mix.reject_reasons), (mix.ack_delay, 0, 0))
    if category == "fail":
        return Directive(
            "fail",
            _pick(rng, mix.fail_reasons),
            (mix.ack_delay, mix.match_delay, mix.settle_delay),
        )
    if category == "slow":
        return Directive("clean", "", (mix.ack_delay, mix.match_delay, mix.slow_settle_delay))
    return Directive("clean", "", (mix.ack_delay, mix.match_delay, mix.settle_delay))


def _padding(size: int) -> str:
    """A ``size``-byte run of ``:70E:`` continuation lines (inert filler text)."""
    return "".join("\n" if index % (_PAD_LINE_WIDTH + 1) == 0 else "A" for index in range(size))


def _build_text(mt: str, seme: str, directive: Directive, target_size_bytes: int) -> str:
    """Render the instruction, then inject the directive + size padding in a :70E: block."""
    suffix = "\n-}"
    head = render_instruction(mt, seme).removesuffix(suffix)
    narrative = "\n:16R:GENL\n:70E::DECL//" + encode_directive(directive)
    close = "\n:16S:GENL"
    unpadded = head + narrative + close + suffix
    deficit = target_size_bytes - len(unpadded.encode("ascii"))
    padding = _padding(deficit) if deficit > 0 else ""
    return head + narrative + padding + close + suffix


def generate(
    count: int,
    *,
    target_size_bytes: int = 1024,
    mix: Mix = DEFAULT_MIX,
    seed: int = 0,
) -> list[GeneratedInstruction]:
    """Generate ``count`` deterministic instructions for one simulated day.

    Each instruction has a unique zero-padded ``SEME``, a settlement-direction MT
    drawn from ``mix.mts``, a directive drawn per the scenario ``mix``, and a
    ``:70E:`` narrative padded to ``target_size_bytes``. Deterministic given
    ``seed``: two calls with the same arguments return equal lists.
    """
    rng = random.Random(seed)  # noqa: S311
    weights = [mix.clean, mix.reject, mix.fail, mix.slow]
    instructions: list[GeneratedInstruction] = []
    for index in range(count):
        seme = f"GWSEC{index:010d}"
        mt = _pick(rng, mix.mts)
        category = rng.choices(_CATEGORIES, weights=weights, k=1)[0]  # noqa: S311
        directive = _draw_directive(rng, category, mix)
        text = _build_text(mt, seme, directive, target_size_bytes)
        instructions.append(GeneratedInstruction(seme, mt, directive, text))
    return instructions


def generate_realistic(
    count: int,
    *,
    seed: int = 0,
    target_size_bytes: int = 1024,
) -> list[GeneratedInstruction]:
    """Blend the calibrated asset-class books into one realistic aggregate day.

    Deterministic given ``seed``. Asset class shapes the aggregate outcome mix only;
    it is not carried on the generated instruction (macro-only invariant).
    """
    rng = random.Random(seed)  # noqa: S311
    books = list(_BOOK_SHARE)
    weights = [_BOOK_SHARE[b] for b in books]
    out: list[GeneratedInstruction] = []
    for index in range(count):
        book = rng.choices(books, weights=weights, k=1)[0]  # noqa: S311
        mix = REALISTIC_BOOKS[book]
        seme = f"GWSEC{index:010d}"
        mt = _pick(rng, mix.mts)
        category_weights = [mix.clean, mix.reject, mix.fail, mix.slow]
        category = rng.choices(_CATEGORIES, weights=category_weights, k=1)[0]  # noqa: S311
        directive = _draw_directive(rng, category, mix)
        text = _build_text(mt, seme, directive, target_size_bytes)
        out.append(GeneratedInstruction(seme, mt, directive, text))
    return out
