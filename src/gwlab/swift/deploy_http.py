"""Upsert the SWIFT settlement board into Grafana (the network deploy).

Box-only — it talks to Prometheus/Grafana over HTTP — so it is excluded from
coverage like v1's :mod:`gwlab.deploy_http`, whose :class:`HttpDeployClient` it
reuses. The board it deploys is built by the covered
:func:`gwlab.swift.board.build_settlement_board`.

    python -m gwlab.swift.deploy_http --qmgr NHAUAPP \\
        --grafana-url http://localhost:3000 --grafana-user admin --grafana-password admin
"""

from __future__ import annotations

import argparse
import base64
import json
import urllib.request
from typing import TYPE_CHECKING

from gwlab.deploy_http import HttpDeployClient
from gwlab.swift.board import build_settlement_board

if TYPE_CHECKING:
    from collections.abc import Sequence


def _prometheus_ds_uid(grafana_url: str, user: str, password: str) -> str:
    """Discover the Prometheus datasource uid so the board's panels resolve.

    Prefers the default Prometheus datasource; falls back to the first one, or the
    conventional ``"prometheus"`` uid if none is enumerable.
    """
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(  # noqa: S310 — trusted lab Grafana URL
        f"{grafana_url.rstrip('/')}/api/datasources",
        headers={"Authorization": f"Basic {token}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        sources = json.load(resp)
    prom = [d for d in sources if d.get("type") == "prometheus"]
    chosen = next((d for d in prom if d.get("isDefault")), None) or (prom[0] if prom else None)
    return str(chosen["uid"]) if chosen else "prometheus"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gwlab.swift.deploy_http")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    parser.add_argument("--grafana-user", default="admin")
    parser.add_argument("--grafana-password", default="admin")
    args = parser.parse_args(argv)

    ds_uid = _prometheus_ds_uid(args.grafana_url, args.grafana_user, args.grafana_password)
    client = HttpDeployClient(
        args.prometheus_url, args.grafana_url, args.grafana_user, args.grafana_password
    )
    folder_uid = client.qm_board_folder_uid(args.qmgr) or ""
    client.upsert_board(build_settlement_board(args.qmgr, ds_uid=ds_uid), folder_uid)
    print(f"deployed swift settlement board into folder {folder_uid or '-'} (datasource {ds_uid})")


if __name__ == "__main__":
    main()
