"""GitHub REST implementation of the host-agnostic :class:`VCSBackend`.

Reads a GitHub remote over the REST API to populate the ``integrate`` inventory —
listing repositories (by org), walking trees, reading blobs, resolving refs — and
verifies inbound push webhooks. It never clones: git transport is the operator's
job. The outbound HTTP lives in a model-free helper (mirroring
``webhooks.PinnedWebhookClient``) that reuses ``integrate.net`` for the SSRF gate;
the API base is admin-configured (``api.github.com`` by default, a GHE host via
``Integration.config``), so the host is trusted rather than user-supplied per
request.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
from typing import Any
from urllib.parse import quote, urlunsplit

from django.core.exceptions import ValidationError

from angee.integrate.net import is_unsafe_address, parse_http_url, resolved_addresses
from angee.integrate.vcs.backend import RepoDescriptor, TreeEntry, VCSBackend

HTTP_TIMEOUT_SECONDS = 15
DEFAULT_API_BASE = "https://api.github.com"
WEBHOOK_SIGNATURE_HEADER = "X-Hub-Signature-256"
PER_PAGE = 100
MAX_REPO_PAGES = 100
"""Hard cap on repository pages (~10k repos) so a pathological listing can't loop forever."""
SEARCH_LIMIT = 20
"""Repository search results returned for a typeahead query."""


class GitHubApiError(Exception):
    """Raised when the GitHub REST API returns a non-success, non-404 status."""

    def __init__(self, message: str, *, status: int) -> None:
        """Record the HTTP status for the failed request."""

        super().__init__(message)
        self.status = status


class GitHubBackend(VCSBackend):
    """A :class:`VCSBackend` that reads a GitHub remote over the REST API."""

    key = "github"
    label = "GitHub"
    icon = "github"
    defaults = {
        "vendor": "github",
        "config": {
            "github_api_base": DEFAULT_API_BASE,
        },
    }

    @property
    def api_base(self) -> str:
        """Return the REST API base URL (``api.github.com`` or a configured GHE host)."""

        base = str(self.integration.config.get("github_api_base") or DEFAULT_API_BASE)
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
                break
        return descriptors

    def ls_tree(self, repository: Any, *, ref: str, path: str, recursive: bool = False) -> list[TreeEntry]:
        """List the tree under ``path`` at ``ref`` via the git-trees API."""

        tree_oid = self.rev_parse(repository, ref)
        suffix = "?recursive=1" if recursive else ""
        payload = self._get(f"/repos/{repository.name}/git/trees/{quote(tree_oid)}{suffix}") or {}
        if payload.get("truncated"):
            raise GitHubApiError(
                f"git tree for {repository.name}@{ref} is truncated; enumeration would silently drop entries",
                status=0,
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
        """Return the bytes of one blob via the contents API; raise if absent or a tree."""

        payload = self._get(f"/repos/{repository.name}/contents/{quote(path, safe='/')}?ref={quote(ref, safe='')}")
        if isinstance(payload, list):
            raise IsADirectoryError(path)
        encoding = str(payload.get("encoding", "base64"))
        content = str(payload.get("content", ""))
        return base64.b64decode(content) if encoding == "base64" else content.encode("utf-8")

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

    def verify_webhook(self, vcs_integration: Any, request: Any) -> bool:
        """Verify an inbound push webhook's HMAC-SHA256 signature against the secret."""

        secret = str(getattr(vcs_integration, "webhook_secret", "") or "")
        signature = _request_header(request, WEBHOOK_SIGNATURE_HEADER)
        if not secret or not signature:
            return False
        digest = hmac.new(secret.encode("utf-8"), _request_body(request), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, f"sha256={digest}")

    # Internals ---------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        """Return REST headers including the integration credential's authorization."""

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "angee-integrate-github",
        }
        headers.update(self.integration.credential.auth_headers())
        return headers

    def _get(self, path: str) -> Any:
        """GET one REST path and return parsed JSON; raise ``FileNotFoundError`` on 404."""

        status, body = http_get(f"{self.api_base}{path}", self._headers())
        if status == 404:
            raise FileNotFoundError(path)
        if status < 200 or status >= 300:
            raise GitHubApiError(f"GitHub API GET {path} returned HTTP {status}", status=status)
        return json.loads(body or b"null")

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


def http_get(url: str, headers: dict[str, str], *, timeout: int = HTTP_TIMEOUT_SECONDS) -> tuple[int, bytes]:
    """GET ``url`` after gating its host, returning ``(status, body)``.

    Module-level so tests can stub the network. Gates scheme + host through
    ``integrate.net`` and rejects a host resolving to a non-public address.
    """

    parsed = parse_http_url(url)
    host = str(parsed.hostname)
    port = parsed.port or (http.client.HTTPS_PORT if parsed.scheme == "https" else http.client.HTTP_PORT)
    for address in resolved_addresses(host, port):
        if is_unsafe_address(address):
            raise ValidationError("GitHub API host must resolve only to public IP addresses.")
    target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
    connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(host, port=port, timeout=timeout)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        return int(response.status), response.read()
    finally:
        connection.close()


def _request_header(request: Any, name: str) -> str:
    """Return one header from a Django request or a test double."""

    headers = getattr(request, "headers", None)
    if headers is not None:
        return str(headers.get(name, "") or "")
    return str(getattr(request, name, "") or "")


def _request_body(request: Any) -> bytes:
    """Return the raw request body from a Django request or a test double."""

    body = getattr(request, "body", b"")
    return body if isinstance(body, bytes) else str(body).encode("utf-8")
