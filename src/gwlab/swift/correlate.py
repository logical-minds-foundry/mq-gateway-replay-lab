"""Reference-based correlation: tie each response to its instruction.

The body-level analog of v1's ``MsgId``/``CorrelId`` header echo. Each response
(MT548 status advice, MT544-547 confirmation) carries ``:20C::RELA//`` whose
value is the originating instruction's ``:20C::SEME//`` (documented; see
``docs/swift-settlement-reference.md`` §3). We index instructions by their SEME
and bucket responses by their RELA.

A response whose RELA matches no sent instruction — including a ``NONREF`` or a
missing RELA — is an :class:`Orphan`, the v2 analog of v1's Algorithm-3 orphan
reply. Per the reviewed reference §7, we correlate on ``RELA`` only; ``:20C::PREV//``
cancellation/second-leg chains are recorded in the message model but deliberately
not chased here (amendment tracking is out of scope for this iteration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gwlab.swift.model import SwiftMessage

REASON_ORPHAN = "orphan"


@dataclass(frozen=True)
class Correlated:
    """One instruction with its response stream (RELA == instruction SEME).

    ``responses`` is sorted by MQ put-time, so the lifecycle fold (#79) can replay
    it in order. An instruction with no responses yet has an empty list.
    """

    instruction: SwiftMessage
    responses: list[SwiftMessage] = field(default_factory=list)


@dataclass(frozen=True)
class Orphan:
    """A response that references no sent instruction (unexpected — flag it)."""

    rela: str
    message: SwiftMessage
    reason: str = REASON_ORPHAN


def correlate(
    instructions: list[SwiftMessage],
    responses: list[SwiftMessage],
) -> tuple[list[Correlated], list[Orphan]]:
    """Group responses under their instruction by SEME/RELA.

    Returns ``(correlated, orphans)``. Every instruction with a SEME becomes a
    :class:`Correlated` (empty ``responses`` if none matched); a SEME-less
    instruction cannot be correlated and is skipped (a malformed instruction is
    surfaced by the parser/collector, not here). Responses are put-time sorted.
    """
    index: dict[str, SwiftMessage] = {}
    buckets: dict[str, list[SwiftMessage]] = {}
    for instruction in instructions:
        seme = instruction.seme
        if seme is None:
            continue
        index[seme] = instruction
        buckets.setdefault(seme, [])

    orphans: list[Orphan] = []
    for response in responses:
        rela = response.rela
        if rela is None or rela not in index:
            orphans.append(Orphan(rela=rela or "", message=response))
            continue
        buckets[rela].append(response)

    correlated = [
        Correlated(
            instruction=index[seme],
            responses=sorted(messages, key=lambda message: message.put_time),
        )
        for seme, messages in buckets.items()
    ]
    return correlated, orphans
