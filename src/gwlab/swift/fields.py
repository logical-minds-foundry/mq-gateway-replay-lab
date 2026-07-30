"""Validate parsed block-4 fields against the cited field-spec catalog.

This layer answers one question per field: does its value conform to the format
the reference (:mod:`gwlab.swift.reference`) publishes for that field/qualifier?
It checks the character set, the bounded length, and — for the ``:25D:`` status
fields — the enumerated status domain. It deliberately does **not** treat an
unknown/undocumented field as a violation: surfacing those is the collector's job
(epic #65, task #7b), kept separate so each layer owns one responsibility.

Composite-value fields are validated leniently, because their published char-set
describes only a sub-part of the extracted value and a naive check would
false-flag: the multi-line ``:35B:`` (ISIN plus description) and the ``d``
decimal-amount fields (which carry a currency/unit prefix). We validate what is
cleanly checkable and leave the rest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from gwlab.swift.reference import FIELD_CATALOG, STATUS_DOMAINS

if TYPE_CHECKING:
    from collections.abc import Iterator

    from gwlab.swift.tokenizer import Block, Field, FieldTree


#: SWIFT character-set classes -> a compiled full-match regex for the value part.
#: ``n`` digits, ``a`` upper letters, ``c`` upper alphanumeric, ``d`` digits with a
#: decimal comma, ``x`` the SWIFT ``x`` set (letters, digits, space, and
#: ``/ - ? : ( ) . , ' +``).
_CHARSET_PATTERNS: dict[str, re.Pattern[str]] = {
    "n": re.compile(r"[0-9]+"),
    "a": re.compile(r"[A-Z]+"),
    "c": re.compile(r"[A-Z0-9]+"),
    "d": re.compile(r"[0-9,]+"),
    "x": re.compile(r"[A-Za-z0-9 /?:().,'+-]+"),
}


@dataclass(frozen=True)
class FieldViolation:
    """One field whose value breaks the catalog spec for its key.

    ``key`` is the catalog key (``"20C:SEME"`` or ``"25D:IPRC"``); ``reason`` is
    ``"charset"``, ``"format"`` (length), or ``"domain"`` (status-code domain).
    """

    key: str
    value: str
    reason: str


def _is_composite(value: str, charset: str) -> bool:
    """Whether the value is composite, so validated leniently (no char/length check)."""
    return "\n" in value or charset == "d"


def validate_field(field: Field) -> FieldViolation | None:
    """Validate one field's value against its catalog spec; ``None`` when clean.

    An unknown/undocumented key is not a violation here — it returns ``None``.
    """
    key = f"{field.tag}:{field.qualifier}" if field.qualifier else field.tag
    spec = FIELD_CATALOG.get(key)
    if spec is None:
        return None
    value = field.value
    if not _is_composite(value, spec.charset):
        if _CHARSET_PATTERNS[spec.charset].fullmatch(value) is None:
            return FieldViolation(key, value, "charset")
        if len(value) > spec.max_len:
            return FieldViolation(key, value, "format")
    if spec.tag == "25D" and value not in STATUS_DOMAINS[cast("str", spec.qualifier)]:
        return FieldViolation(key, value, "domain")
    return None


def _iter_fields(blocks: list[Block]) -> Iterator[Field]:
    """Yield every field in ``blocks``, recursing into sub-blocks."""
    for block in blocks:
        yield from block.fields
        yield from _iter_fields(block.subblocks)


def validate_tree(tree: FieldTree) -> list[FieldViolation]:
    """Validate every field in the tree; an empty list means all fields are clean."""
    violations: list[FieldViolation] = []
    for field in _iter_fields(tree.blocks):
        violation = validate_field(field)
        if violation is not None:
            violations.append(violation)
    return violations
