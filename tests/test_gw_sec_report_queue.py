"""Task 1 (epic #105): the GW.SEC.REPORT terminal COA/COD report ledger.

Renders the ``gw-sec-queues`` Ansible role from its **real** files (defaults +
tasks) and asserts the report ledger is a terminal, CAPEXPRY-capped QLOCAL:
created, horizon-capped to the same window as the SEC audit queues, and *never*
streamed. This keeps the role in lockstep with spec §5 — GW.SEC.REPORT is a
queue we own, kept **out of** the audit queues (which stay pure application
traffic) and out of ``gw_sec_streams`` (no ``STREAMQ``; the collector only
browses it as its third source).

The tiny loaders below parse exactly the constructs these two role files use
(scalars, block lists, flow lists, and the handful of ``{{ ... }}`` references
the tasks resolve). They deliberately depend on nothing outside the standard
library — the validation venv carries no YAML/Jinja engine — and raise loudly on
any construct they do not recognise rather than silently skipping it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ROLE = Path(__file__).resolve().parent.parent / "ansible" / "roles" / "gw-sec-queues"

_TEMPLATE_RE = re.compile(r"\{\{(.*?)\}\}")
_JOIN_RE = re.compile(r"join\('([^']*)'\)")
_MULT_RE = re.compile(r"(\w+)\s*\*\s*(\d+)")
_DEFINE_RE = re.compile(
    r"DEFINE\s+(QLOCAL|QALIAS|QREMOTE)\((.*?)\)(.*?)\bREPLACE\b",
    re.IGNORECASE | re.DOTALL,
)
_ATTR_RE = re.compile(r"([A-Za-z][A-Za-z0-9]*)\(([^)]*)\)")
_PRINTF_RE = re.compile(r'printf\s+"(.*?)"\s+([^\n|]+)', re.DOTALL)


def _subst(text: str, ctx: dict[str, Any]) -> str:
    """Resolve the ``{{ ... }}`` references these role files actually use."""

    def repl(match: re.Match[str]) -> str:
        expr = match.group(1).strip()
        if expr.startswith("item."):
            return match.group(0)  # per-stream loop var, expanded by the caller
        if expr == "qmgr_name":
            return "QMX"  # apply-time value; irrelevant to the object model
        if "|" in expr:
            var, _, filt = (part.strip() for part in expr.partition("|"))
            fm = _JOIN_RE.fullmatch(filt)
            if not fm:
                raise ValueError(f"unsupported filter: {filt!r}")
            return fm.group(1).join(str(x) for x in ctx[var])
        mult = _MULT_RE.fullmatch(expr)
        if mult:
            return str(int(ctx[mult.group(1)]) * int(mult.group(2)))
        if expr in ctx:
            return str(ctx[expr])
        raise ValueError(f"unresolved template reference: {expr!r}")

    return _TEMPLATE_RE.sub(repl, text)


def _scalar(value: str) -> Any:
    value = value.strip()
    if (value[:1], value[-1:]) in {('"', '"'), ("'", "'")}:
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _item(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("["):
        return [_scalar(part) for part in raw[1:-1].split(",")]
    return _scalar(raw)


def _resolve(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _subst(value, ctx)
    if isinstance(value, list):
        return [_resolve(v, ctx) for v in value]
    return value


def load_defaults() -> dict[str, Any]:
    """Parse ``defaults/main.yml`` and resolve its inter-variable references."""
    lines = (_ROLE / "defaults" / "main.yml").read_text().splitlines()
    ctx: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        head = re.match(r"^(\w+):\s*(.*)$", lines[i])
        if not stripped or stripped.startswith("#") or not head:
            i += 1
            continue
        key, inline = head.group(1), head.group(2)
        if inline:
            ctx[key] = _scalar(inline)
            i += 1
            continue
        items: list[Any] = []
        i += 1
        while i < len(lines) and re.match(r"^\s+-\s+", lines[i]):
            items.append(_item(re.sub(r"^\s+-\s+", "", lines[i])))
            i += 1
        ctx[key] = items
    for _ in range(len(ctx) + 2):  # fixed-point resolution of nested {{ ... }}
        rendered = {k: _resolve(v, ctx) for k, v in ctx.items()}
        if rendered == ctx:
            break
        ctx = rendered
    return ctx


def _parse_defines(segment: str, objs: dict[str, dict]) -> None:
    for kind, name, body in _DEFINE_RE.findall(segment):
        attrs = {k.upper(): v for k, v in _ATTR_RE.findall(body)}
        objs[name.strip().strip("'\"")] = {"type": kind.upper(), **attrs}


def render_gw_sec_objects(defaults: dict[str, Any]) -> dict[str, dict]:
    """Render the role's ``tasks/main.yml`` into the MQ objects it provisions."""
    raw = (_ROLE / "tasks" / "main.yml").read_text()
    text = _subst(raw, defaults)
    objs: dict[str, dict] = {}
    # `printf "DEFINE QLOCAL(%s) ... REPLACE" A B` — the attrs apply to each name.
    for fmt, args in _PRINTF_RE.findall(text):
        for name in args.replace("\\", " ").split():
            _parse_defines(fmt.replace("%s", name), objs)
    # The single-object `echo "DEFINE ..."` tasks (printf blocks removed first).
    _parse_defines(_PRINTF_RE.sub("", text), objs)
    # STREAMQ is applied only to the live queues in gw_sec_streams — never to the
    # terminal report ledger, which is deliberately absent from that list.
    qos = re.search(r"STRMQOS\(([^)]*)\)", raw)
    for live, target in defaults["gw_sec_streams"]:
        obj = objs.setdefault(live, {"type": "QLOCAL"})
        obj["STREAMQ"] = target
        if qos:
            obj["STRMQOS"] = qos.group(1)
    return objs


def test_report_queue_is_terminal_capexpry_ledger() -> None:
    defaults = load_defaults()
    objs = render_gw_sec_objects(defaults)

    rep = objs["GW.SEC.REPORT"]
    assert rep["type"] == "QLOCAL"
    assert rep["CAPEXPRY"] == defaults["gw_sec_capexpry"]
    assert rep["MAXDEPTH"] == str(defaults["gw_sec_max_depth"])
    # Terminal: nothing streams out of it — the collector only browses it.
    assert "STREAMQ" not in rep
    # Kept out of the audit-traffic stream list (spec §5).
    assert "GW.SEC.REPORT" not in [live for live, _ in defaults["gw_sec_streams"]]
    # And out of the pure-application audit queues.
    assert "GW.SEC.REPORT" not in defaults["gw_sec_audit_queues"]


def test_report_ledger_named_by_q_report_var() -> None:
    defaults = load_defaults()
    assert defaults["q_report"] == "GW.SEC.REPORT"
