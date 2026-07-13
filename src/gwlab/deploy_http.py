"""The live ``DeployClient`` — Prometheus discovery + Grafana folder lookup + upsert.

Box-only glue over stdlib ``urllib``: excluded from coverage (it talks to the live
Prometheus and Grafana); the orchestration it serves (``deploy.deploy_boards``) is
unit-tested. Run the reproducible per-QM board deploy with::

    python -m gwlab.deploy_http --counterparty CPB \\
        --prometheus-url http://localhost:9090 \\
        --grafana-url http://localhost:3000 --grafana-user admin --grafana-password admin

It discovers every queue manager whose ``GW.<cp>.REQUEST.AUDIT`` Prometheus is
scraping, finds each QM's own lab board (``lab-qm-*``) to learn its folder, and
upserts the gateway board into that same folder.
"""

from __future__ import annotations

import argparse
import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from gwlab.config import QueueSet
from gwlab.deploy import deploy_boards

if TYPE_CHECKING:
    from collections.abc import Sequence

_TIMEOUT = 15


def _get_json(url: str, headers: dict[str, str] | None = None) -> Any:
    request = urllib.request.Request(url, headers=headers or {})  # noqa: S310 — trusted lab URLs
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read())


class HttpDeployClient:
    """A ``DeployClient`` backed by the live Prometheus and Grafana HTTP APIs."""

    def __init__(self, prometheus_url: str, grafana_url: str, user: str, password: str) -> None:
        self._prom = prometheus_url.rstrip("/")
        self._graf = grafana_url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self._auth = {"Authorization": f"Basic {token}"}

    def gateway_qmgrs(self, counterparty: str) -> list[str]:
        audit = QueueSet.for_counterparty(counterparty).request_audit
        query = urllib.parse.quote(f'ibmmq_queue_depth{{queue="{audit}"}}')
        result = _get_json(f"{self._prom}/api/v1/query?query={query}")["data"]["result"]
        return sorted({series["metric"]["qmgr"] for series in result if "qmgr" in series["metric"]})

    def _stack_for(self, qmgr: str) -> str | None:
        query = urllib.parse.quote(f'ibmmq_qmgr_status{{qmgr="{qmgr}"}}')
        result = _get_json(f"{self._prom}/api/v1/query?query={query}")["data"]["result"]
        for series in result:
            stack = series["metric"].get("stack")
            if stack:
                return str(stack)
        return None

    def qm_board_folder_uid(self, qmgr: str) -> str | None:
        # Co-locate with the stack's cockpit board (lab-<stack>-cluster), which is
        # reliably under its per-stack folder — the QM's own board can sit at root.
        # Look the cockpit up by uid directly (Grafana search matches titles, not uids).
        stack = self._stack_for(qmgr)
        if stack is None:
            return None
        cockpit = f"lab-{stack}-cluster"
        try:
            meta = _get_json(f"{self._graf}/api/dashboards/uid/{cockpit}", self._auth)["meta"]
        except urllib.error.HTTPError:
            return None
        folder_uid = meta.get("folderUid")
        return str(folder_uid) if folder_uid else None

    def upsert_board(self, board: dict[str, Any], folder_uid: str) -> None:
        payload = json.dumps(
            {"dashboard": board, "folderUid": folder_uid, "overwrite": True}
        ).encode()
        headers = {**self._auth, "Content-Type": "application/json"}
        request = urllib.request.Request(  # noqa: S310 — trusted lab URL
            f"{self._graf}/api/dashboards/db", data=payload, headers=headers, method="POST"
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            response.read()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gwlab.deploy_http")
    parser.add_argument("--counterparty", default="CPB")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    parser.add_argument("--grafana-user", default="admin")
    parser.add_argument("--grafana-password", default="admin")
    args = parser.parse_args(argv)

    client = HttpDeployClient(
        args.prometheus_url, args.grafana_url, args.grafana_user, args.grafana_password
    )
    for result in deploy_boards(client, args.counterparty):
        where = result.folder_uid or "-"
        print(f"{result.qmgr}: {result.note} (folder {where})")


if __name__ == "__main__":
    main()
