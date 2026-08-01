"""Tests for the box-only v1 Grafana/Prometheus deploy client, boundary-mocked.

``deploy_http`` talks to the live Prometheus and Grafana over stdlib ``urllib``.
Here ``urllib.request.urlopen`` is monkeypatched to a small in-memory router that
serves each endpoint from a JSON payload (or raises ``HTTPError`` where a branch
needs it), so the client's discovery/folder-lookup/upsert logic runs with no
network. ``main`` is exercised against a faked ``HttpDeployClient`` so its wiring
and printing are covered. The board JSON is covered by ``dashboard.py``'s tests.
All hosts, queue managers, and folders are invented/anonymised.
"""

from __future__ import annotations

import base64
import email.message
import json
import urllib.error
from typing import TYPE_CHECKING, Any

from gwlab import deploy_http
from gwlab.deploy_http import HttpDeployClient

if TYPE_CHECKING:
    import pytest

_PROM = "http://prom:9090"
_GRAF = "http://graf:3000"


class _Resp:
    """A minimal ``urlopen`` context manager exposing ``read()``."""

    def __init__(self, payload: Any = None, raw: bytes = b"") -> None:
        self._data = json.dumps(payload).encode() if payload is not None else raw

    def read(self) -> bytes:
        return self._data

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


def _route(
    monkeypatch: pytest.MonkeyPatch,
    routes: dict[str, Any],
    *,
    error_urls: tuple[str, ...] = (),
) -> list[Any]:
    """Patch ``urlopen`` to dispatch on a URL substring; record every request."""
    seen: list[Any] = []

    def _fake_urlopen(request: Any, timeout: float = 0) -> _Resp:
        seen.append(request)
        url = request.full_url
        for substring in error_urls:
            if substring in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", email.message.Message(), None)
        for substring, payload in routes.items():
            if substring in url:
                return _Resp(payload)
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(deploy_http.urllib.request, "urlopen", _fake_urlopen)
    return seen


def _client() -> HttpDeployClient:
    return HttpDeployClient(_PROM, _GRAF, "admin", "secret")


# --- __init__: url normalisation + basic-auth ---------------------------------


def test_init_strips_trailing_slashes_and_builds_basic_auth() -> None:
    client = HttpDeployClient(f"{_PROM}/", f"{_GRAF}/", "admin", "secret")
    assert client._prom == _PROM
    assert client._graf == _GRAF
    token = client._auth["Authorization"].removeprefix("Basic ")
    assert base64.b64decode(token).decode() == "admin:secret"


# --- gateway_qmgrs: discover the QMs scraping the audit queue ------------------


def test_gateway_qmgrs_returns_sorted_unique_qmgrs_with_the_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _route(
        monkeypatch,
        {
            "prom:9090": {
                "data": {
                    "result": [
                        {"metric": {"qmgr": "QMB"}},
                        {"metric": {"qmgr": "QMA"}},
                        {"metric": {"qmgr": "QMB"}},  # duplicate → deduped
                        {"metric": {}},  # no qmgr label → filtered out
                    ]
                }
            }
        },
    )
    assert _client().gateway_qmgrs("CPB") == ["QMA", "QMB"]


# --- _stack_for: find the QM's stack label ------------------------------------


def test_stack_for_returns_the_first_series_carrying_a_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _route(
        monkeypatch,
        {
            "prom:9090": {
                "data": {
                    "result": [
                        {"metric": {}},  # no stack → skipped
                        {"metric": {"stack": "nhau"}},
                    ]
                }
            }
        },
    )
    assert _client()._stack_for("QMA") == "nhau"


def test_stack_for_returns_none_when_no_series_has_a_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _route(monkeypatch, {"prom:9090": {"data": {"result": [{"metric": {}}]}}})
    assert _client()._stack_for("QMA") is None


# --- qm_board_folder_uid: co-locate with the QM's cockpit board ---------------


def test_folder_uid_resolves_via_the_stack_cockpit_board(monkeypatch: pytest.MonkeyPatch) -> None:
    _route(
        monkeypatch,
        {
            "prom:9090": {"data": {"result": [{"metric": {"stack": "nhau"}}]}},
            "graf:3000": {"meta": {"folderUid": "folder-nhau"}},
        },
    )
    assert _client().qm_board_folder_uid("QMA") == "folder-nhau"


def test_folder_uid_is_none_when_the_qm_has_no_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    _route(monkeypatch, {"prom:9090": {"data": {"result": [{"metric": {}}]}}})
    assert _client().qm_board_folder_uid("QMA") is None


def test_folder_uid_is_none_when_the_cockpit_board_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _route(
        monkeypatch,
        {"prom:9090": {"data": {"result": [{"metric": {"stack": "nhau"}}]}}},
        error_urls=("graf:3000",),  # dashboards/uid lookup 404s
    )
    assert _client().qm_board_folder_uid("QMA") is None


def test_folder_uid_is_none_when_the_cockpit_board_sits_at_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _route(
        monkeypatch,
        {
            "prom:9090": {"data": {"result": [{"metric": {"stack": "nhau"}}]}},
            "graf:3000": {"meta": {"folderUid": ""}},  # falsey → None
        },
    )
    assert _client().qm_board_folder_uid("QMA") is None


# --- upsert_board: POST the dashboard -----------------------------------------


def test_upsert_board_posts_the_dashboard_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _route(monkeypatch, {"graf:3000": {}})
    board = {"uid": "gwlab-cpb", "title": "Gateway"}
    _client().upsert_board(board, "folder-nhau")
    (request,) = seen
    assert request.full_url == f"{_GRAF}/api/dashboards/db"
    assert request.method == "POST"
    body = json.loads(request.data)
    assert body == {"dashboard": board, "folderUid": "folder-nhau", "overwrite": True}
    assert request.headers["Content-type"] == "application/json"
    assert "Authorization" in request.headers


# --- main(): parse, discover, deploy, print -----------------------------------


class _FakeClient:
    """A ``DeployClient`` mapping QMA→a folder and QMB→none (the skip path)."""

    def __init__(self, *_args: Any, **_kw: Any) -> None:
        self.upserts: list[tuple[dict[str, Any], str]] = []

    def gateway_qmgrs(self, _counterparty: str) -> list[str]:
        return ["QMA", "QMB"]

    def qm_board_folder_uid(self, qmgr: str) -> str | None:
        return "folder-nhau" if qmgr == "QMA" else None

    def upsert_board(self, board: dict[str, Any], folder_uid: str) -> None:
        self.upserts.append((board, folder_uid))


def test_main_deploys_each_qm_and_prints_both_outcomes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = _FakeClient()
    monkeypatch.setattr(deploy_http, "HttpDeployClient", lambda *a, **k: client)
    deploy_http.main(["--counterparty", "CPB"])
    assert len(client.upserts) == 1  # only the QM with a folder is deployed
    out = capsys.readouterr().out
    assert "QMA: deployed (folder folder-nhau)" in out
    assert "QMB: no lab board for this QM; skipped (folder -)" in out  # `or "-"` fallback


def test_main_runs_with_all_defaults(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, Any] = {}

    class _RecordingClient(_FakeClient):
        def __init__(self, *args: Any, **kw: Any) -> None:
            super().__init__()
            captured["args"] = args

    monkeypatch.setattr(deploy_http, "HttpDeployClient", _RecordingClient)
    deploy_http.main([])
    assert captured["args"] == (
        "http://localhost:9090",
        "http://localhost:3000",
        "admin",
        "admin",
    )
