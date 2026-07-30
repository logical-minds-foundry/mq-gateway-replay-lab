"""Configuration for the v2 SWIFT settlement flow — queue names and horizon.

Parallel to v1's :class:`gwlab.config.QueueSet`, but for the securities flow:
the two ``GW.SEC.*`` streaming-audit queues this repo owns (its outbound
instruction audit and its inbound response audit), and the settlement horizon
that scopes retention and reporting (spec §4.4, §7). All names are generic and
invented, per the anonymization rule.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwiftQueueSet:
    """The two audit queues the collector browses (read-only).

    - ``instruct_audit``: retained MT540-543 instructions we sent.
    - ``status_audit``: streamed copy of every inbound MT548 / MT544-547.
    """

    instruct_audit: str = "GW.SEC.INSTRUCT.AUDIT"
    status_audit: str = "GW.SEC.STATUS.AUDIT"


@dataclass(frozen=True)
class Horizon:
    """The open settlement window, in business days (≈ T+2/T+3 + fails buffer).

    Because a settlement lifecycle spans days, retention (``CAPEXPRY``) and
    reporting follow this horizon rather than the calendar day (spec §4.4). The
    plan (task #88) sizes the concrete value against real volumes.
    """

    days: int = 3
