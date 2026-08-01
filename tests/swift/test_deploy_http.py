"""Tests for the SWIFT settlement board Grafana deploy, boundary-mocked.

``deploy_http`` talks to Grafana over HTTP. Here ``urllib.request.urlopen`` is
monkeypatched so the datasource-discovery GET is served from an in-memory JSON
payload, and ``HttpDeployClient`` is replaced so ``main`` exercises its wiring
(datasource-uid resolution, folder resolution, upsert) without any network. The
board JSON itself is covered by ``board.py``'s own tests.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from gwlab.swift import deploy_http


class _Resp:
    """A minimal urlopen context manager yielding a JSON body for ``json.load``."""

    def __init__(self, payload: Any) -> None:
        self._data = json.dumps(payload).encode()

    def __enter__(self) -> io.BytesIO:
        return io.BytesIO(self._data)

    def __exit__(self, *_exc: object) -> bool:
        return False


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, payload: Any) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def _fake_urlopen(req: Any, timeout: float = 0) -> _Resp:
        seen["url"] = req.full_url
        seen["headers"] = req.headers
        return _Resp(payload)

    monkeypatch.setattr(deploy_http.urllib.request, "urlopen", _fake_urlopen)
    return seen


# --- _prometheus_ds_uid resolution -------------------------------------------


def test_prometheus_ds_uid_prefers_the_default_datasource(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch_urlopen(
        monkeypatch,
        [
            {"type": "loki", "uid": "loki-1"},
            {"type": "prometheus", "uid": "prom-a", "isDefault": False},
            {"type": "prometheus", "uid": "prom-default", "isDefault": True},
        ],
    )
    uid = deploy_http._prometheus_ds_uid("http://graf:3000/", "admin", "admin")
    assert uid == "prom-default"
    assert seen["url"] == "http://graf:3000/api/datasources"  # trailing slash stripped
    assert "Authorization" in seen["headers"]


def test_prometheus_ds_uid_falls_back_to_first_prometheus(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(
        monkeypatch,
        [{"type": "prometheus", "uid": "prom-only"}, {"type": "loki", "uid": "loki-1"}],
    )
    assert deploy_http._prometheus_ds_uid("http://graf:3000", "u", "p") == "prom-only"


def test_prometheus_ds_uid_defaults_when_none_enumerable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, [{"type": "loki", "uid": "loki-1"}])
    assert deploy_http._prometheus_ds_uid("http://graf:3000", "u", "p") == "prometheus"


# --- main() wiring ------------------------------------------------------------


class _FakeClient:
    """Records the board upsert; maps the QM to a folder uid (or None)."""

    def __init__(self, *_args: Any, folder: str | None = None) -> None:
        self._folder = folder
        self.upserts: list[tuple[dict[str, Any], str]] = []

    def qm_board_folder_uid(self, _qmgr: str) -> str | None:
        return self._folder

    def upsert_board(self, board: dict[str, Any], folder_uid: str) -> None:
        self.upserts.append((board, folder_uid))


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, folder: str | None) -> _FakeClient:
    client = _FakeClient(folder=folder)
    monkeypatch.setattr(deploy_http, "HttpDeployClient", lambda *a, **k: client)
    monkeypatch.setattr(deploy_http, "_prometheus_ds_uid", lambda *a: "prom-uid")
    return client


def test_main_upserts_the_board_into_the_qm_folder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _install_fake_client(monkeypatch, folder="folder-nhau")
    deploy_http.main(["--qmgr", "NHAUAPP"])
    assert len(client.upserts) == 1
    board, folder_uid = client.upserts[0]
    assert folder_uid == "folder-nhau"
    assert board["uid"]  # a real board was built via build_settlement_board
    out = capsys.readouterr().out
    assert "folder-nhau" in out
    assert "prom-uid" in out


def test_main_handles_a_qm_with_no_folder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _install_fake_client(monkeypatch, folder=None)
    deploy_http.main(["--qmgr", "NHAUAPP"])
    assert client.upserts[0][1] == ""  # the `or ""` fallback
    assert "folder -" in capsys.readouterr().out  # printed as '-'


def test_main_requires_qmgr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deploy_http, "_prometheus_ds_uid", lambda *a: "prom-uid")
    with pytest.raises(SystemExit):
        deploy_http.main([])
