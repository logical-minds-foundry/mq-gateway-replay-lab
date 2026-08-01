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
    """The audit queues the collector browses plus the outbound request context.

    - ``instruct_audit``: retained MT540-543 instructions we sent.
    - ``status_audit``: streamed copy of every inbound MT548 / MT544-547.
    - ``instruct``: the app-facing request QALIAS the requester puts to (its
      stream retains the audit copy; its remote leg carries the instruction to the
      counterparty). This is where the COA/COD report options are attached.
    - ``report_ledger``: the terminal ``GW.SEC.REPORT`` ledger the returned COA/COD
      reports land on and the collector browses as its third source (spec §5).
    - ``request_reports``: whether the requester attaches the COA/COD report bits
      to the outbound put (spec §3). Default **on** in the lab; toggling it off
      gives the before/after transparency proof (spec §11).
    """

    instruct_audit: str = "GW.SEC.INSTRUCT.AUDIT"
    status_audit: str = "GW.SEC.STATUS.AUDIT"
    instruct: str = "GW.SEC.INSTRUCT"
    report_ledger: str = "GW.SEC.REPORT"
    request_reports: bool = True


@dataclass(frozen=True)
class Horizon:
    """The open settlement window, in business days (≈ T+2/T+3 + fails buffer).

    Because a settlement lifecycle spans days, retention (``CAPEXPRY``) and
    reporting follow this horizon rather than the calendar day (spec §4.4). The
    plan (task #88) sizes the concrete value against real volumes.

    ``compression`` is the single time-compression factor ``C`` (load-sim §7): a
    whole multi-day sim runs in real ``days/C`` so the window can fire without
    waiting days. The window works in **sim-time** — it scales the horizon by
    ``1/C`` in real terms (``in_window`` / ``is_aged_out``), so aged-out fires
    correctly under compression. ``C = 1`` (the default, production) collapses to
    real-time behaviour: the horizon is exactly ``days`` real days.
    """

    days: int = 3
    compression: float = 1.0
