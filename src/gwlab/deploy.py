"""Reproducible per-QM board deployment.

Discovers which queue managers carry the gateway's queues and, for each, renders
its board and upserts it into the **same Grafana folder as that queue manager's
own lab board**. The gateway board therefore sits with the QM it extends — under
the lab's per-stack folder (#59) — without the gateway knowing or hardcoding the
folder layout: it finds the folder by the QM's existing board and writes only its
own dashboard (parasite / boundary rule).

The orchestration here is pure and takes a ``DeployClient`` seam, so it is fully
unit-tested; the real HTTP client (Prometheus discovery + Grafana folder lookup +
dashboard upsert) lives in ``deploy_http`` and is exercised on the box.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from gwlab.dashboard import build_dashboard

if TYPE_CHECKING:
    from typing import Any


class DeployClient(Protocol):
    """The lab-facing operations board deployment needs, behind a testable seam.

    - ``gateway_qmgrs``: queue managers whose archive queues Prometheus scrapes
      for this counterparty.
    - ``qm_board_folder_uid``: the Grafana folder UID of that QM's own lab board,
      or ``None`` if it has none.
    - ``upsert_board``: create or overwrite ``board`` in the given folder.
    """

    def gateway_qmgrs(self, counterparty: str) -> list[str]: ...
    def qm_board_folder_uid(self, qmgr: str) -> str | None: ...
    def upsert_board(self, board: dict[str, Any], folder_uid: str) -> None: ...


@dataclass(frozen=True)
class DeployResult:
    """What happened for one queue manager's board."""

    qmgr: str
    folder_uid: str
    deployed: bool
    note: str


def deploy_boards(
    client: DeployClient, counterparty: str, ds_uid: str = "prometheus"
) -> list[DeployResult]:
    """Deploy one board per discovered queue manager, co-located with its lab board.

    A queue manager with no lab board is skipped loudly (recorded in the result),
    never guessed at — we place the board next to a board we can actually find.
    """
    results: list[DeployResult] = []
    for qmgr in client.gateway_qmgrs(counterparty):
        folder_uid = client.qm_board_folder_uid(qmgr)
        if folder_uid is None:
            results.append(
                DeployResult(qmgr, "", deployed=False, note="no lab board for this QM; skipped")
            )
            continue
        client.upsert_board(build_dashboard(qmgr, counterparty, ds_uid), folder_uid)
        results.append(DeployResult(qmgr, folder_uid, deployed=True, note="deployed"))
    return results
