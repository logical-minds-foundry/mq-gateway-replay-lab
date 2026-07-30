"""A generic ISO 15022 block-4 tokenizer plus SWIFT envelope/MT extraction.

The tokenizer knows nothing about any particular MT — it turns block-4 text into
a uniform :class:`FieldTree` of nested ``:16R:``/``:16S:`` blocks and
``:<tag>::<qualifier>//<value>`` fields. Meaning is applied later, by the field
catalog (:mod:`gwlab.swift.fields`) and the message model
(:mod:`gwlab.swift.model`).

The MT type is **not** in block 4 — it lives in the Block 2 application header of
the SWIFT envelope ``{1:…}{2:…}{4:…-}`` — so :func:`parse_envelope` is the single
owner of "which MT is this": it reads the MT number from Block 2 and returns the
block-4 payload for :func:`tokenize`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class TokenizeError(ValueError):
    """Raised when the envelope or block-4 structure cannot be parsed."""


@dataclass(frozen=True)
class Field:
    """One block-4 field: its tag, optional qualifier, and value.

    For ``:20C::SEME//REF`` the tag is ``20C``, the qualifier ``SEME``, and the
    value ``REF``. For an unqualified field like ``:23G:NEWM`` the qualifier is
    ``None`` and the value is ``NEWM``. A multi-line value (e.g. an ISIN with a
    description line) is joined with ``\\n``.
    """

    tag: str
    qualifier: str | None
    value: str


@dataclass(frozen=True)
class Block:
    """A ``:16R:<name>`` … ``:16S:<name>`` sequence, with fields and sub-blocks."""

    name: str
    fields: list[Field]
    subblocks: list[Block]


@dataclass(frozen=True)
class FieldTree:
    """The parsed block 4: the top-level sequences."""

    blocks: list[Block]

    def find(self, tag: str, qualifier: str | None = None) -> list[Field]:
        """Return every field matching ``tag`` (and ``qualifier`` if given), any depth."""
        out: list[Field] = []
        _collect(self.blocks, tag, qualifier, out)
        return out


def _collect(blocks: list[Block], tag: str, qualifier: str | None, out: list[Field]) -> None:
    for block in blocks:
        for field in block.fields:
            if field.tag == tag and (qualifier is None or field.qualifier == qualifier):
                out.append(field)
        _collect(block.subblocks, tag, qualifier, out)


_FIELD_RE = re.compile(r"^:(\d{2}[A-Z]?):(.*)$")
_QUAL_RE = re.compile(r"^:([A-Z0-9]{4})//(.*)$")
_QUAL_DSS_RE = re.compile(r"^:([A-Z0-9]{4})/(.*)$")
_BLOCK2_RE = re.compile(r"\{2:[IO](\d{3})")


def _split_qualifier(rest: str) -> tuple[str | None, str]:
    """Split a field's content into (qualifier, value).

    ``:SEME//REF`` -> ``("SEME", "REF")``; ``:DEAG/ECLR/24943`` (Data Source
    Scheme form) -> ``("DEAG", "ECLR/24943")``; ``ISIN FI0001006462`` (no
    qualifier) -> ``(None, "ISIN FI0001006462")``.
    """
    match = _QUAL_RE.match(rest)
    if match is not None:
        return match.group(1), match.group(2)
    match = _QUAL_DSS_RE.match(rest)
    if match is not None:
        return match.group(1), match.group(2)
    return None, rest


def tokenize(text: str) -> FieldTree:
    """Parse block-4 text into a :class:`FieldTree`.

    Raises :class:`TokenizeError` on unbalanced blocks, a field outside any
    block, or a stray line that is neither a field nor a value continuation.
    """
    tree_blocks: list[Block] = []
    stack: list[Block] = []
    pending: tuple[str, str | None, list[str]] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        tag, qualifier, lines = pending
        if not stack:
            message = f"field :{tag}: appears outside any block"
            raise TokenizeError(message)
        stack[-1].fields.append(Field(tag, qualifier, "\n".join(lines)))
        pending = None

    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if line in ("", "-"):
            continue
        match = _FIELD_RE.match(line)
        if match is None:
            if pending is None:
                message = f"stray line outside a field: {line!r}"
                raise TokenizeError(message)
            pending[2].append(line)
            continue
        tag, rest = match.group(1), match.group(2)
        if tag == "16R":
            flush()
            block = Block(rest, [], [])
            (stack[-1].subblocks if stack else tree_blocks).append(block)
            stack.append(block)
        elif tag == "16S":
            flush()
            if not stack or stack[-1].name != rest:
                message = f"unbalanced block close :16S:{rest}"
                raise TokenizeError(message)
            stack.pop()
        else:
            flush()
            qualifier, value0 = _split_qualifier(rest)
            pending = (tag, qualifier, [value0])

    flush()
    if stack:
        message = f"unclosed block(s): {[b.name for b in stack]}"
        raise TokenizeError(message)
    return FieldTree(tree_blocks)


def parse_envelope(text: str) -> tuple[str, str]:
    """Read the MT number from Block 2 and return ``(mt, block4_text)``.

    Handles both input ``{2:I541…}`` and output ``{2:O548…}`` headers. Raises
    :class:`TokenizeError` if Block 2 (with an MT) or a ``{4:…-}`` block is
    missing.
    """
    match = _BLOCK2_RE.search(text)
    if match is None:
        message = "no Block 2 application header / MT type in envelope"
        raise TokenizeError(message)
    mt = match.group(1)

    start = text.find("{4:")
    if start == -1:
        message = "no Block 4 in envelope"
        raise TokenizeError(message)
    body_start = start + len("{4:")
    end = text.find("-}", body_start)
    if end == -1:
        message = "Block 4 not terminated with '-}'"
        raise TokenizeError(message)
    return mt, text[body_start:end]
