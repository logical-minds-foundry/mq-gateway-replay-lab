"""Upsert the SWIFT settlement board into Grafana (the network deploy).

Box-only — it talks to Prometheus/Grafana over HTTP — so it is excluded from
coverage like v1's :mod:`gwlab.deploy_http`, whose :class:`DeployClient` it
reuses. The board it deploys is built by the covered
:func:`gwlab.swift.board.build_settlement_board`.

    python -m gwlab.swift.deploy_http --qmgr NHAUAPP \\
        --grafana-url http://localhost:3000 --grafana-user admin --grafana-password admin
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from gwlab.deploy_http import HttpDeployClient
from gwlab.swift.board import build_settlement_board

if TYPE_CHECKING:
    from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gwlab.swift.deploy_http")
    parser.add_argument("--qmgr", required=True)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--grafana-url", default="http://localhost:3000")
    parser.add_argument("--grafana-user", default="admin")
    parser.add_argument("--grafana-password", default="admin")
    args = parser.parse_args(argv)

    client = HttpDeployClient(
        args.prometheus_url, args.grafana_url, args.grafana_user, args.grafana_password
    )
    folder_uid = client.qm_board_folder_uid(args.qmgr) or ""
    client.upsert_board(build_settlement_board(args.qmgr), folder_uid)
    print(f"deployed swift settlement board into folder {folder_uid or '-'}")


if __name__ == "__main__":
    main()
