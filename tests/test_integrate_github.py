"""Tests for the GitHub VCS backend — REST shape, stubbing the network.

The backend reads over the shared SSRF-pinned client (``self.http``); these tests
replace ``HttpClient.get`` so no DB, settings, or live network is touched.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

import pytest

from angee.integrate.http import HttpClient, HttpResponse
from angee.integrate_github import backend as gh


def _patch_get(monkeypatch: pytest.MonkeyPatch, fake_http_get: Any) -> None:
    """Route the shared client's GET through a ``(url, headers, *, timeout) -> (status, body)`` stub."""

    def get(
        self: Any, url: str, *, headers: Any = None, allow_private: bool = False, timeout: int = 15
    ) -> HttpResponse:
        status, body = fake_http_get(url, headers or {}, timeout=timeout)
        return HttpResponse(status=status, body=body)

    monkeypatch.setattr(HttpClient, "get", get)


def _integration(*, api_base: str = "") -> Any:
    """Return a fake integration carrying a credential and config for the backend."""

    credential = SimpleNamespace(auth_headers=lambda: {"Authorization": "Bearer token"})
    config = {"github_api_base": api_base} if api_base else {}
    return SimpleNamespace(credential=credential, config=config)


def _repo(full_name: str, *, private: bool = False) -> dict[str, Any]:
    """Return a minimal GitHub repository payload."""

    owner = full_name.split("/", 1)[0]
    return {
        "full_name": full_name,
        "owner": {"login": owner},
        "clone_url": f"https://github.com/{full_name}.git",
        "ssh_url": f"git@github.com:{full_name}.git",
        "node_id": f"node-{full_name}",
        "default_branch": "main",
        "private": private,
        "html_url": f"https://github.com/{full_name}",
        "archived": False,
    }


def test_ls_repos_pages_through_every_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """ls_repos follows pages so reconcile never prunes against a truncated first page."""

    page_one = [_repo(f"acme/r{index}") for index in range(gh.PER_PAGE)]
    page_two = [_repo("acme/r100", private=True)]

    def fake_http_get(url: str, headers: dict[str, str], *, timeout: int = 15) -> tuple[int, bytes]:
        body = page_one if url.endswith("page=1") else page_two if url.endswith("page=2") else []
        return 200, json.dumps(body).encode("utf-8")

    _patch_get(monkeypatch, fake_http_get)

    repos = gh.GitHubBackend(_integration()).ls_repos()

    assert len(repos) == gh.PER_PAGE + 1
    assert repos[0].org == "acme"
    assert repos[-1].name == "acme/r100"
    assert repos[-1].visibility == "private"
    assert repos[-1].ssh_remote == "git@github.com:acme/r100.git"


def test_ls_tree_filters_by_path_and_rejects_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """ls_tree keeps only the subtree and raises rather than silently dropping entries."""

    tree = {
        "tree": [
            {"path": "templates/dev/copier.yml", "type": "blob", "sha": "a"},
            {"path": "templates/dev/README.md", "type": "blob", "sha": "b"},
            {"path": "other/thing.txt", "type": "blob", "sha": "c"},
        ]
    }

    def fake_http_get(url: str, headers: dict[str, str], *, timeout: int = 15) -> tuple[int, bytes]:
        if "/commits/" in url:
            return 200, json.dumps({"sha": "deadbeef"}).encode("utf-8")
        return 200, json.dumps(tree).encode("utf-8")

    _patch_get(monkeypatch, fake_http_get)
    repository = SimpleNamespace(name="acme/widgets")
    entries = gh.GitHubBackend(_integration()).ls_tree(repository, ref="main", path="templates", recursive=True)

    assert {entry.path for entry in entries} == {"templates/dev/copier.yml", "templates/dev/README.md"}

    def truncated_http_get(url: str, headers: dict[str, str], *, timeout: int = 15) -> tuple[int, bytes]:
        if "/commits/" in url:
            return 200, json.dumps({"sha": "deadbeef"}).encode("utf-8")
        return 200, json.dumps({"tree": [], "truncated": True}).encode("utf-8")

    _patch_get(monkeypatch, truncated_http_get)
    with pytest.raises(gh.GitHubApiError):
        gh.GitHubBackend(_integration()).ls_tree(repository, ref="main", path="templates", recursive=True)


def test_cat_file_decodes_base64_and_404_is_filenotfound(monkeypatch: pytest.MonkeyPatch) -> None:
    """cat_file decodes the contents API blob; a 404 surfaces as FileNotFoundError."""

    content = base64.b64encode(b"_angee:\n  kind: workspace\n").decode("ascii")

    def fake_http_get(url: str, headers: dict[str, str], *, timeout: int = 15) -> tuple[int, bytes]:
        if "missing" in url:
            return 404, b""
        return 200, json.dumps({"encoding": "base64", "content": content}).encode("utf-8")

    _patch_get(monkeypatch, fake_http_get)
    backend = gh.GitHubBackend(_integration())
    repository = SimpleNamespace(name="acme/widgets")

    assert b"kind: workspace" in backend.cat_file(repository, ref="main", path="templates/dev/copier.yml")
    with pytest.raises(FileNotFoundError):
        backend.cat_file(repository, ref="main", path="missing")


def test_search_repos_projects_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """search_repos reads the search API items into descriptors for the typeahead."""

    def fake_http_get(url: str, headers: dict[str, str], *, timeout: int = 15) -> tuple[int, bytes]:
        assert "/search/repositories" in url
        return 200, json.dumps({"items": [_repo("acme/widgets")]}).encode("utf-8")

    _patch_get(monkeypatch, fake_http_get)
    results = gh.GitHubBackend(_integration()).search_repos("widget", org="acme")

    assert [candidate.name for candidate in results] == ["acme/widgets"]


def test_verify_webhook_checks_hmac(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify_webhook accepts a correct HMAC-SHA256 signature and rejects a wrong one."""

    secret = "s3cr3t"
    body = b'{"ref":"refs/heads/main"}'
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    backend = gh.GitHubBackend(_integration())
    vcs_bridge = SimpleNamespace(webhook_secret=secret)

    good = SimpleNamespace(headers={gh.WEBHOOK_SIGNATURE_HEADER: f"sha256={digest}"}, body=body)
    bad = SimpleNamespace(headers={gh.WEBHOOK_SIGNATURE_HEADER: "sha256=deadbeef"}, body=body)

    assert backend.verify_webhook(vcs_bridge, good) is True
    assert backend.verify_webhook(vcs_bridge, bad) is False
    # No secret configured → never authentic.
    assert backend.verify_webhook(SimpleNamespace(webhook_secret=""), good) is False
