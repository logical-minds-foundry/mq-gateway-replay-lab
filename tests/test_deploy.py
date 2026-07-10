"""Tests for the reproducible per-QM board deployment orchestration."""

from typing import Any

from gwlab.deploy import deploy_boards


class _FakeClient:
    """Records upserts; maps each QM to a folder (or None for 'no lab board')."""

    def __init__(self, qmgrs: list[str], folders: dict[str, str | None]) -> None:
        self._qmgrs = qmgrs
        self._folders = folders
        self.upserts: list[tuple[str, str]] = []  # (board uid, folder uid)

    def gateway_qmgrs(self, counterparty: str) -> list[str]:
        assert counterparty  # exercised with a real tag
        return self._qmgrs

    def qm_board_folder_uid(self, qmgr: str) -> str | None:
        return self._folders.get(qmgr)

    def upsert_board(self, board: dict[str, Any], folder_uid: str) -> None:
        self.upserts.append((board["uid"], folder_uid))


def test_each_discovered_qm_board_lands_in_that_qm_folder() -> None:
    client = _FakeClient(
        ["NHAUAPP", "RDQMAPP"],
        {"NHAUAPP": "folder-nhau", "RDQMAPP": "folder-rdqm"},
    )
    results = deploy_boards(client, "CPB")
    assert [(r.qmgr, r.folder_uid, r.deployed) for r in results] == [
        ("NHAUAPP", "folder-nhau", True),
        ("RDQMAPP", "folder-rdqm", True),
    ]
    # board uid is per-(QM x counterparty); it lands in the QM's own folder
    assert client.upserts == [
        ("gw-nhauapp-cpb", "folder-nhau"),
        ("gw-rdqmapp-cpb", "folder-rdqm"),
    ]


def test_a_qm_without_a_lab_board_is_skipped_loudly() -> None:
    client = _FakeClient(["NHAUAPP"], {"NHAUAPP": None})
    results = deploy_boards(client, "CPB")
    assert results[0].deployed is False
    assert "no lab board" in results[0].note
    assert client.upserts == []  # never guess a folder
