"""GitHub REST implementation of the host-agnostic :class:`VCSBackend`.

Reads a GitHub remote over the REST API to populate the ``integrate`` inventory —
listing repositories (by org), walking trees, reading blobs, resolving refs — and
verifies inbound push webhooks. It never clones: git transport is the operator's
job. Outbound calls go through the shared SSRF-pinned client (``self.http``, from
:class:`~angee.integrate.http.HttpClientMixin` on :class:`VCSBackend`); the API
base is admin-configured (``api.github.com`` by default, a GHE host via
``VcsBridge.config``), so the host is trusted rather than user-supplied per
request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from django.http import HttpRequest
from pydantic import BaseModel, ConfigDict, Field

from angee.integrate.errors import IntegrationError
from angee.integrate.http import JSON_MAX_BYTES, HttpRefusal
from angee.integrate_vcs.backend import RepoDescriptor, TreeEntry, VCSBackend

if TYPE_CHECKING:
    from angee.integrate_vcs.models import VcsBridge

HTTP_TIMEOUT_SECONDS = 15
DEFAULT_API_BASE = "https://api.github.com"
WEBHOOK_SIGNATURE_HEADER = "X-Hub-Signature-256"
PER_PAGE = 100
MAX_REPO_PAGES = 100
"""Hard cap on repository pages (~10k repos) so a pathological listing can't loop forever."""
SEARCH_LIMIT = 20
"""Repository search results returned for a typeahead query."""


class GitHubApiError(IntegrationError):
    """A GitHub refusal whose message the backend composed from facts, never from GitHub's answer."""


def _error_for(path: str) -> Callable[[HttpRefusal], Exception]:
    """Return the error a refusal about ``path`` becomes: a 404 is ``FileNotFoundError``, else a GitHubApiError."""

    def error(refusal: HttpRefusal) -> Exception:
        return FileNotFoundError(path) if refusal.status == 404 else refusal.as_error(GitHubApiError, "GitHub")

    return error


class GitHubBackend(VCSBackend):
    """A :class:`VCSBackend` that reads a GitHub remote over the REST API."""

    key = "github"
    label = "GitHub"
    icon = "github"
    repository_search_scope_config_key = "github_org"
    defaults = {"vendor": "github"}

    class Config(BaseModel):
        """Supported non-secret GitHub API configuration."""

        model_config = ConfigDict(extra="forbid")

        github_api_base: str = Field(default=DEFAULT_API_BASE, description="GitHub API base URL.")
        github_org: str = Field(default="", description="Organization used to scope repository search.")

    config_model = Config

    @property
    def api_base(self) -> str:
        """Return the REST API base URL (``api.github.com`` or a configured GHE host)."""

        base = str(self.bridge.config.get("github_api_base") or DEFAULT_API_BASE)
        return base.rstrip("/")

    def ls_repos(self, *, org: str = "") -> list[RepoDescriptor]:
        """List every repository for ``org`` (or the authenticated account when blank).

        Pages through the full listing: ``reconcile`` prunes repositories absent
        from this result, so a truncated first page would delete every repo past
        it. A short page (fewer than ``PER_PAGE``) is the last; a hard cap bounds a
        pathological loop.
        """

        base = f"/orgs/{quote(org)}/repos" if org else "/user/repos"
        descriptors: list[RepoDescriptor] = []
        for page in range(1, MAX_REPO_PAGES + 1):
            items = self._get(f"{base}?per_page={PER_PAGE}&page={page}") or []
            descriptors.extend(self._descriptor(item) for item in items)
            if len(items) < PER_PAGE:
                return descriptors
        raise GitHubApiError("Repository listing exceeded its page limit; inventory is incomplete.")

    def ls_tree(self, repository: Any, *, ref: str, path: str, recursive: bool = False) -> list[TreeEntry]:
        """List the tree under ``path`` at ``ref`` via the git-trees API."""

        tree_oid = self.rev_parse(repository, ref)
        suffix = "?recursive=1" if recursive else ""
        payload = self._get(f"/repos/{repository.name}/git/trees/{quote(tree_oid)}{suffix}") or {}
        if payload.get("truncated"):
            raise GitHubApiError(
                f"git tree for {repository.name}@{ref} is truncated; enumeration would silently drop entries"
            )
        prefix = path.strip("/")
        entries: list[TreeEntry] = []
        for item in payload.get("tree", []):
            full = str(item.get("path", ""))
            if prefix and full != prefix and not full.startswith(f"{prefix}/"):
                continue
            entries.append(TreeEntry(path=full, type=str(item.get("type", "")), oid=str(item.get("sha", ""))))
        return entries

    def cat_file(self, repository: Any, *, ref: str, path: str) -> bytes:
        """Read bounded binary contents; large blobs use GitHub's native raw media type."""

        endpoint = f"/repos/{repository.name}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}"
        payload = self._get(endpoint, max_bytes=2 * 1024 * 1024)
        if isinstance(payload, list):
            raise IsADirectoryError(path)
        if payload.get("size", 0) > self.max_file_bytes:
            raise GitHubApiError("GitHub file exceeds its byte limit")
        if payload.get("encoding", "base64") == "base64":
            content = base64.b64decode(str(payload.get("content", "")))
            if len(content) > self.max_file_bytes:
                raise GitHubApiError("GitHub file exceeds its byte limit")
            return content
        if payload.get("encoding") != "none":
            raise GitHubApiError("GitHub returned an unsupported file encoding")
        headers = {**self._headers(), "Accept": "application/vnd.github.raw+json"}
        error = _error_for(path)
        response = self.http.request(
            "GET", f"{self.api_base}{endpoint}", headers=headers,
            allow_private=self.api_base != DEFAULT_API_BASE.rstrip("/"),
            same_origin_redirects=3, max_bytes=self.max_file_bytes, timeout=HTTP_TIMEOUT_SECONDS, error=error,
        )
        if not response.is_success:
            raise error(HttpRefusal.of(response))
        return response.content

    def rev_parse(self, repository: Any, ref: str) -> str:
        """Resolve ``ref`` to a commit oid via the commits API."""

        payload = self._get(f"/repos/{repository.name}/commits/{quote(ref, safe='')}")
        return str((payload or {}).get("sha", ""))

    def search_repos(self, query: str, *, org: str = "") -> list[RepoDescriptor]:
        """Return repositories whose name matches ``query`` via the search API (typeahead)."""

        terms = f"{query} in:name"
        if org:
            terms += f" user:{org}"
        payload = self._get(f"/search/repositories?q={quote(terms)}&per_page={SEARCH_LIMIT}")
        return [self._descriptor(item) for item in (payload or {}).get("items", [])]

    def get_repo(self, name: str) -> RepoDescriptor:
        """Return one repository by ``owner/repo`` name; raise ``FileNotFoundError`` if absent."""

        return self._descriptor(self._get(f"/repos/{name}"))

    def verify_webhook(self, vcs_bridge: VcsBridge, request: HttpRequest) -> bool:
        """Verify an inbound push webhook's HMAC-SHA256 signature against the secret."""

        secret = str(vcs_bridge.webhook_secret or "")
        signature = str(request.headers.get(WEBHOOK_SIGNATURE_HEADER, "") or "")
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), request.body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, f"sha256={digest}")

    # Internals ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Return REST headers including the integration credential's authorization."""

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "angee-integrate-github",
        }
        credential = self.bridge.load_connection_credential()
        if credential is None:
            raise GitHubApiError("GitHub requests require an attached credential")
        headers.update(credential.auth_headers())
        return headers

    def _get(self, path: str, *, max_bytes: int = JSON_MAX_BYTES) -> Any:
        """GET one REST path and return parsed JSON; raise ``FileNotFoundError`` on 404.

        A configured (non-default) ``github_api_base`` is a self-hosted GitHub
        Enterprise host, which may sit on a private network, so private targets are
        permitted only then — public ``api.github.com`` stays public-only.
        """

        return self.http.request_model(
            "GET", f"{self.api_base}{path}", headers=self._headers(),
            allow_private=self.api_base != DEFAULT_API_BASE.rstrip("/"),
            max_bytes=max_bytes, timeout=HTTP_TIMEOUT_SECONDS, error=_error_for(path),
        )

    @staticmethod
    def _descriptor(item: dict[str, Any]) -> RepoDescriptor:
        """Project one GitHub repository payload into a :class:`RepoDescriptor`."""

        visibility = item.get("visibility") or ("private" if item.get("private") else "public")
        owner = item.get("owner") or {}
        return RepoDescriptor(
            name=str(item.get("full_name", "")),
            org=str(owner.get("login", "")),
            remote=str(item.get("clone_url", "")),
            ssh_remote=str(item.get("ssh_url", "")),
            remote_id=str(item.get("node_id", "")),
            default_branch=str(item.get("default_branch") or "main"),
            visibility=str(visibility),
            web_url=str(item.get("html_url", "")),
            archived=bool(item.get("archived")),
        )


