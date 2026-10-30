"""Tests for the GitHub VCS backend — REST shape, stubbing the network.

The backend reads over the shared SSRF-pinned client (``self.http``); these tests
stub transport calls; a database regression pins the credential and OAuth-client join.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.integrate.http import HttpClient
from angee.integrate_github import backend as gh
from tests.conftest import Credential, OAuthClient, VcsBridge


def _patch_get(monkeypatch: pytest.MonkeyPatch, fake_http_get: Any) -> None:
    """Route the shared client's GET through a ``(url, headers, *, timeout) -> (status, body)`` stub."""

    def get(
        self: Any, url: str, *, headers: Any = None, allow_private: bool = False, timeout: int = 15
    ) -> httpx2.Response:
        status, body = fake_http_get(url, headers or {}, timeout=timeout)
        return httpx2.Response(status, content=body)

    monkeypatch.setattr(HttpClient, "get", get)


def _integration(*, api_base: str = "") -> Any:
    """Return a fake integration carrying a credential and config for the backend."""

    credential = SimpleNamespace(auth_headers=lambda: {"Authorization": "Bearer token"})
    config = {"github_api_base": api_base} if api_base else {}
    bridge = VcsBridge(config=config)
    bridge._meta.get_field("credential").set_cached_value(bridge, credential)
    return bridge


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


@pytest.mark.django_db(transaction=True)
def test_headers_fetch_credential_and_oauth_client_in_one_query(
    composed_tables: None, django_assert_num_queries: Any
) -> None:
    """Uncached authentication loads the credential and its provider together."""

    del composed_tables
    with system_context(reason="test github credential query count"):
        user = get_user_model().objects.create_user(username="github-query")
        oauth_client = OAuthClient.objects.create(slug="github-query", client_id="github-client")
        credential = Credential.objects.upsert_for_user(
            user, oauth_client, "oauth", {"access_token": "github-token"}
        )
        bridge = VcsBridge(credential_id=credential.pk, config={})
        backend = gh.GitHubBackend(bridge)

        with django_assert_num_queries(1):
            headers = backend._headers()
            assert bridge.credential.oauth_client.client_id == "github-client"
        assert headers["Authorization"] == "Bearer github-token"
        with django_assert_num_queries(0):
            assert backend._headers() == headers


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


def test_cat_file_decodes_base64_and_404_is_filenotfound(monkeypatch):
    content = b"PK\x00\xff\x80binary-wheel"
    def respond(request):
        if "missing" in request.url.path:
            return httpx2.Response(404)
        return httpx2.Response(200, json={"encoding": "base64", "content": base64.b64encode(content).decode()})
    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(respond)))
    backend = gh.GitHubBackend(_integration())
    repository = SimpleNamespace(name="acme/widgets")
    assert backend.cat_file(repository, ref="a" * 40, path="dist/addon.whl") == content
    with pytest.raises(FileNotFoundError):
        backend.cat_file(repository, ref="a" * 40, path="missing")


def test_large_binary_file_uses_bounded_raw_bytes_at_the_selected_revision(monkeypatch):
    content = b"PK\x00\xff\x80already-built-wheel"
    calls = []
    def respond(request):
        assert request.url.params["ref"] == "a" * 40
        assert request.headers["Authorization"] == "Bearer token"
        calls.append(request.headers["Accept"])
        if request.headers["Accept"] == "application/vnd.github.raw+json":
            return httpx2.Response(200, content=content)
        return httpx2.Response(200, json={"encoding": "none", "content": "", "size": len(content)})
    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(respond)))
    backend = gh.GitHubBackend(_integration())
    assert backend.cat_file(SimpleNamespace(name="acme/widgets"), ref="a" * 40, path="dist/addon.whl") == content
    assert calls == ["application/vnd.github+json", "application/vnd.github.raw+json"]


def test_raw_artifact_redirect_cannot_forward_the_selected_credential(monkeypatch):
    from angee.integrate.http import RedirectOriginError
    calls = []
    def respond(request):
        calls.append(str(request.url))
        if request.headers["Accept"] == "application/vnd.github.raw+json":
            return httpx2.Response(302, headers={"Location": "https://outside.example/steal"})
        return httpx2.Response(200, json={"encoding": "none", "content": ""})
    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(respond)))
    backend = gh.GitHubBackend(_integration())
    with pytest.raises(RedirectOriginError):
        backend.cat_file(SimpleNamespace(name="acme/widgets"), ref="a" * 40, path="dist/addon.whl")
    assert len(calls) == 2 and all(url.startswith("https://api.github.com/") for url in calls)


def test_raw_artifact_download_enforces_the_native_decoded_byte_limit(monkeypatch):
    from angee.integrate.http import ResponseTooLargeError
    def respond(request):
        if request.headers["Accept"] == "application/vnd.github.raw+json":
            return httpx2.Response(200, content=b"123456789")
        return httpx2.Response(200, json={"encoding": "none", "content": ""})
    monkeypatch.setattr(HttpClient, "transport_factory", staticmethod(lambda **_: httpx2.MockTransport(respond)))
    backend = gh.GitHubBackend(_integration())
    backend.max_file_bytes = 8
    with pytest.raises(ResponseTooLargeError):
        backend.cat_file(SimpleNamespace(name="acme/widgets"), ref="a" * 40, path="dist/addon.whl")


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


def test_repository_page_exhaustion_refuses_incomplete_inventory(monkeypatch):
    monkeypatch.setattr(gh, "MAX_REPO_PAGES", 1)
    monkeypatch.setattr(gh.GitHubBackend, "_get", lambda self, path: [_repo("acme/r")] * gh.PER_PAGE)
    with pytest.raises(gh.GitHubApiError, match="inventory is incomplete"):
        gh.GitHubBackend(_integration()).ls_repos()
