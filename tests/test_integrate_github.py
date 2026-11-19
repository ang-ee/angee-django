"""Tests for the GitHub VCS backend — REST shape, stubbing the network.

The backend reads over the shared SSRF-pinned client (``self.http``); these tests
script the network through ``mock_transport``; a database regression pins the credential and OAuth-client join.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import httpx2
import pytest
from django.contrib.auth import get_user_model
from rebac import system_context

from angee.integrate.errors import is_transient_failure, safe_integration_failure
from angee.integrate.http import JSON_MAX_BYTES
from angee.integrate.testing.http import mock_transport
from angee.integrate_github import backend as gh
from tests.conftest import Credential, OAuthClient, VcsBridge


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


def test_headers_fetch_credential_and_oauth_client_in_one_query(
    db: None, django_assert_num_queries: Any
) -> None:
    """Uncached authentication loads the credential and its provider together."""

    del db
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


def test_ls_repos_pages_through_every_repository() -> None:
    """ls_repos follows pages so reconcile never prunes against a truncated first page."""

    pages = {"1": [_repo(f"acme/r{index}") for index in range(gh.PER_PAGE)], "2": [_repo("acme/r100", private=True)]}

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=pages.get(request.url.params["page"], []))

    with mock_transport(respond):
        repos = gh.GitHubBackend(_integration()).ls_repos()

    assert len(repos) == gh.PER_PAGE + 1
    assert repos[0].org == "acme"
    assert repos[-1].name == "acme/r100"
    assert repos[-1].visibility == "private"
    assert repos[-1].ssh_remote == "git@github.com:acme/r100.git"


def test_ls_tree_filters_by_path_and_rejects_truncation() -> None:
    """ls_tree keeps only the subtree and raises rather than silently dropping entries."""

    tree = {
        "tree": [
            {"path": "templates/dev/copier.yml", "type": "blob", "sha": "a"},
            {"path": "templates/dev/README.md", "type": "blob", "sha": "b"},
            {"path": "other/thing.txt", "type": "blob", "sha": "c"},
        ]
    }

    def respond(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"sha": "deadbeef"} if "/commits/" in request.url.path else tree)

    repository = SimpleNamespace(name="acme/widgets")
    with mock_transport(respond):
        entries = gh.GitHubBackend(_integration()).ls_tree(repository, ref="main", path="templates", recursive=True)
        assert {entry.path for entry in entries} == {"templates/dev/copier.yml", "templates/dev/README.md"}
        tree = {"tree": [], "truncated": True}
        with pytest.raises(gh.GitHubApiError) as refused:
            gh.GitHubBackend(_integration()).ls_tree(repository, ref="main", path="templates", recursive=True)
    # The backend's own refusals reach the operator in its words.
    assert "is truncated" in safe_integration_failure(refused.value).message


@pytest.mark.parametrize(("answer", "transient", "wait", "message"), [
    pytest.param(
        httpx2.Response(403, json={"message": "vendor text"}), False, None, "GitHub refused the request (HTTP 403).",
        id="a-refusal-is-an-integration-error-in-the-backends-words",
    ),
    pytest.param(
        httpx2.Response(503, headers={"Retry-After": "30"}), True, timedelta(seconds=30),
        "GitHub is temporarily unavailable (HTTP 503).", id="an-outage-is-transient-and-waits",
    ),
    pytest.param(
        httpx2.Response(429, headers={"Retry-After": "30"}), True, timedelta(seconds=30),
        "GitHub is rate limiting requests (HTTP 429).", id="a-rate-limit-is-transient-and-waits",
    ),
    pytest.param(httpx2.ReadTimeout("slow"), True, None, "GitHub could not be reached.", id="a-transport-failure"),
    pytest.param(
        httpx2.Response(200, text="not json"), False, None, "GitHub returned an unreadable response.",
        id="an-unreadable-answer-is-permanent",
    ),
    pytest.param(
        httpx2.Response(200, json={"pad": "x" * JSON_MAX_BYTES}), False, None, "GitHub returned an oversized response.",
        id="json-answers-are-capped",
    ),
])
def test_github_refusals_are_integration_errors_classified_by_the_shared_rule(
    answer: httpx2.Response | Exception, transient: bool, wait: timedelta | None, message: str,
) -> None:
    def respond(request: httpx2.Request) -> httpx2.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    with mock_transport(respond) as requests, pytest.raises(gh.GitHubApiError) as refused:
        gh.GitHubBackend(_integration()).get_repo("acme/widgets")

    error = refused.value
    assert (is_transient_failure(error), getattr(error, "retry_after", None)) == (transient, wait)
    assert safe_integration_failure(error).message == message
    # A capped answer is asked for unencoded.
    assert requests[0].headers["accept-encoding"] == "identity"


def test_cat_file_decodes_base64_and_404_is_filenotfound():
    content = b"PK\x00\xff\x80binary-wheel"
    def respond(request):
        if "missing" in request.url.path:
            return httpx2.Response(404)
        return httpx2.Response(200, json={"encoding": "base64", "content": base64.b64encode(content).decode()})
    backend = gh.GitHubBackend(_integration())
    repository = SimpleNamespace(name="acme/widgets")
    with mock_transport(respond):
        assert backend.cat_file(repository, ref="a" * 40, path="dist/addon.whl") == content
        with pytest.raises(FileNotFoundError):
            backend.cat_file(repository, ref="a" * 40, path="missing")


def test_large_binary_file_uses_bounded_raw_bytes_at_the_selected_revision():
    content = b"PK\x00\xff\x80already-built-wheel"
    def respond(request):
        if request.headers["Accept"] == "application/vnd.github.raw+json":
            return httpx2.Response(200, content=content)
        return httpx2.Response(200, json={"encoding": "none", "content": "", "size": len(content)})
    backend = gh.GitHubBackend(_integration())
    with mock_transport(respond) as requests:
        assert backend.cat_file(SimpleNamespace(name="acme/widgets"), ref="a" * 40, path="dist/addon.whl") == content
    assert [request.headers["Accept"] for request in requests] == [
        "application/vnd.github+json", "application/vnd.github.raw+json",
    ]
    assert {(request.url.params["ref"], request.headers["Authorization"]) for request in requests} == {
        ("a" * 40, "Bearer token"),
    }


def test_raw_artifact_redirect_cannot_forward_the_selected_credential():
    from angee.integrate.http import RedirectOriginError
    def respond(request):
        if request.headers["Accept"] == "application/vnd.github.raw+json":
            return httpx2.Response(302, headers={"Location": "https://outside.example/steal"})
        return httpx2.Response(200, json={"encoding": "none", "content": ""})
    backend = gh.GitHubBackend(_integration())
    with mock_transport(respond) as requests, pytest.raises(RedirectOriginError):
        backend.cat_file(SimpleNamespace(name="acme/widgets"), ref="a" * 40, path="dist/addon.whl")
    assert len(requests) == 2 and all(request.url.host == "api.github.com" for request in requests)


def test_raw_artifact_download_enforces_the_native_decoded_byte_limit():
    from angee.integrate.http import ResponseTooLargeError
    def respond(request):
        if request.headers["Accept"] == "application/vnd.github.raw+json":
            return httpx2.Response(200, content=b"123456789")
        return httpx2.Response(200, json={"encoding": "none", "content": ""})
    backend = gh.GitHubBackend(_integration())
    backend.max_file_bytes = 8
    with mock_transport(respond), pytest.raises(gh.GitHubApiError, match="oversized") as refused:
        backend.cat_file(SimpleNamespace(name="acme/widgets"), ref="a" * 40, path="dist/addon.whl")
    assert isinstance(refused.value.__cause__, ResponseTooLargeError) and not refused.value.transient


def test_search_repos_projects_candidates() -> None:
    """search_repos reads the search API items into descriptors for the typeahead."""

    with mock_transport(lambda request: httpx2.Response(200, json={"items": [_repo("acme/widgets")]})) as requests:
        results = gh.GitHubBackend(_integration()).search_repos("widget", org="acme")

    assert [request.url.path for request in requests] == ["/search/repositories"]
    assert [candidate.name for candidate in results] == ["acme/widgets"]


def test_verify_webhook_checks_hmac() -> None:
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
    full_page = [_repo("acme/r")] * gh.PER_PAGE
    with (
        mock_transport(lambda request: httpx2.Response(200, json=full_page)),
        pytest.raises(gh.GitHubApiError, match="inventory is incomplete"),
    ):
        gh.GitHubBackend(_integration()).ls_repos()
