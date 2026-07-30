"""Turn a tokenized SWIFT message into a typed :class:`SwiftMessage`.

The tokenizer (:mod:`gwlab.swift.tokenizer`) yields a structure-only
:class:`~gwlab.swift.tokenizer.FieldTree`; this module applies MT540-548
meaning to it: the message's own reference (``:20C::SEME//``), its linkage back
to the instruction it reports on (``:20C::RELA//``), and every ``:25D:`` status
with the ``:24B:`` reason paired from the *same* STAT sequence.

Reason pairing is by block, not by a flat ``tree.find``. The tokenizer nests
``REAS`` inside ``STAT`` inside ``GENL``; a flat search would lose which status a
reason belongs to. So we walk each STAT sequence and read the ``:24B:`` from
within that STAT's own subtree, preserving the status<->reason association.

``parse_swift`` is the full entry point: it hands the raw envelope to
:func:`~gwlab.swift.tokenizer.parse_envelope` (the single owner of "which MT is
this"), tokenizes the block-4 payload, and models it. ``parse_message`` is the
lower-level form used when the MT is already known (and by tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gwlab.swift import reference, tokenizer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from gwlab.swift.tokenizer import Block, Field, FieldTree


@dataclass(frozen=True)
class StatusItem:
    """One ``:25D:`` status from a STAT sequence, with its paired reason.

    ``qualifier`` is the status kind (``IPRC``, ``MTCH``, ``SETT``, ...);
    ``value`` its code (``PACK``, ``MACH``, ``REJT``, ...); ``reason`` the
    ``:24B:`` reason code from the same STAT's REAS sub-block, or ``None``.
    """

    qualifier: str
    value: str
    reason: str | None


@dataclass(frozen=True)
class SwiftMessage:
    """A parsed MT540-548 message reduced to the fields the lifecycle needs."""

    mt: str
    seme: str | None
    rela: str | None
    statuses: tuple[StatusItem, ...]
    put_time: datetime
    is_instruction: bool
    is_confirmation: bool


def _first(fields: list[Field]) -> str | None:
    """The value of the first matching field, or ``None`` when there are none."""
    return fields[0].value if fields else None


def _fields_deep(block: Block) -> Iterator[Field]:
    """Yield ``block``'s own fields, then those of its sub-blocks, recursively."""
    yield from block.fields
    for sub in block.subblocks:
        yield from _fields_deep(sub)


def _iter_stat_blocks(blocks: list[Block]) -> Iterator[Block]:
    """Yield every STAT sequence at any depth (STAT nests inside GENL)."""
    for block in blocks:
        if block.name == "STAT":
            yield block
        yield from _iter_stat_blocks(block.subblocks)


def _reason_of(stat: Block) -> str | None:
    """The ``:24B:`` reason code paired to this STAT (from its REAS sub-block)."""
    for field in _fields_deep(stat):
        if field.tag == "24B":
            return field.value
    return None


def _statuses(tree: FieldTree) -> tuple[StatusItem, ...]:
    """Every ``:25D:`` status across all STAT sequences, each with its reason."""
    items: list[StatusItem] = []
    for stat in _iter_stat_blocks(tree.blocks):
        reason = _reason_of(stat)
        for field in _fields_deep(stat):
            if field.tag != "25D":
                continue
            qualifier = field.qualifier
            if qualifier is None:
                message = f":25D: status in a STAT sequence has no qualifier: {field.value!r}"
                raise ValueError(message)
            items.append(StatusItem(qualifier, field.value, reason))
    return tuple(items)


def parse_message(mt: str, tree: FieldTree, put_time: datetime) -> SwiftMessage:
    """Model an already-typed ``mt`` and its parsed ``tree`` into a message."""
    return SwiftMessage(
        mt=mt,
        seme=_first(tree.find("20C", "SEME")),
        rela=_first(tree.find("20C", "RELA")),
        statuses=_statuses(tree),
        put_time=put_time,
        is_instruction=mt in reference.INSTRUCTION_MTS,
        is_confirmation=mt in reference.CONFIRMATION_MTS,
    )


def parse_swift(text: str, put_time: datetime) -> SwiftMessage:
    """Parse a full ``{1:}{2:}{4:}`` envelope into a :class:`SwiftMessage`."""
    mt, block4 = tokenizer.parse_envelope(text)
    tree = tokenizer.tokenize(block4)
    return parse_message(mt, tree, put_time)
