"""The local operator daemon as seen from Django: endpoint + token minting."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from django.conf import settings
from graphql import build_client_schema, get_introspection_query, print_schema

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "/operator"
"""Same-origin reverse-proxy base — keeps default deployments CORS-free."""

_DEFAULT_TTL = "1h"
"""Lifetime requested for a minted connection token (the daemon caps at 24h)."""

_MINT_TIMEOUT = 5
"""Seconds to wait on the server-side mint before hiding the connection."""

_PROVISION_TIMEOUT = 60
"""Seconds to wait on a server-side render call (workspace/service create)."""


class OperatorDaemonError(RuntimeError):
    """An operator daemon REST call failed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OperatorDaemonNotFound(OperatorDaemonError):
    """The daemon reported that the requested resource is already absent."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    """One workspace file read through the operator file tools.

    ``etag`` is the daemon's content hash for optimistic concurrency — read it,
    edit, then write it back so a concurrent edit fails the write rather than
    silently clobbering it.
    """

    content: str
    etag: str


@dataclass(frozen=True, slots=True)
class OperatorDaemon:
    """The operator daemon bridge resolved from settings.

    ``endpoint`` is the browser-visible GraphQL URL handed to an authorized
    actor. ``server_base`` and ``admin_bearer`` are server-side only — the admin
    bearer never reaches the browser; it is the credential used to mint a
    short-lived, scoped per-actor token via :meth:`mint_token`, and to drive the
    daemon's lifecycle server-side over its REST API (:meth:`set_secret`,
    :meth:`create_workspace`, :meth:`create_service`, :meth:`destroy_workspace`)
    when Django provisions on a user's behalf.
    """

    endpoint: str
    server_base: str | None
    admin_bearer: str | None
    scope: tuple[str, ...]
    ttl: str

    @classmethod
    def from_settings(cls) -> OperatorDaemon:
        """Resolve the daemon bridge from Django settings and the environment."""

        endpoint_url = cls._setting("ANGEE_OPERATOR_GRAPHQL_ENDPOINT")
        base_url = cls._setting("ANGEE_OPERATOR_URL")
        return cls(
            endpoint=cls._with_graphql_path(endpoint_url or base_url or _DEFAULT_BASE),
            server_base=cls._server_base(endpoint_url, base_url),
            admin_bearer=cls._setting("ANGEE_OPERATOR_TOKEN"),
            scope=tuple(str(item) for item in getattr(settings, "ANGEE_OPERATOR_TOKEN_SCOPE", ())),
            ttl=str(getattr(settings, "ANGEE_OPERATOR_TOKEN_TTL", _DEFAULT_TTL)),
        )

    def mint_token(self, actor: str) -> str | None:
        """Mint a short-lived, scoped connection token for ``actor``, or ``None``.

        Calls the daemon's ``POST /tokens/mint`` with the admin bearer (server-side
        only) and returns the minted ``aud=operator`` token the browser presents —
        so a leaked browser token expires and never carries root access. Returns
        ``None`` (hiding the connection) when the daemon URL or bearer is unset, or
        the call fails. An empty ``scope`` is full access until the daemon enforces
        a capability map.
        """

        if self.admin_bearer is None or self.server_base is None:
            logger.debug("operator: daemon URL or bearer not configured; hiding connection")
            return None
        payload = {"actor": actor, "scope": list(self.scope), "ttl": self.ttl}
        try:
            data = self._post_json(f"{self.server_base}/tokens/mint", payload)
        except (OSError, ValueError) as error:
            logger.warning("operator: connection token mint failed: %s", error)
            return None
        token = data.get("token")
        return token if isinstance(token, str) and token else None

    def introspect_sdl(self) -> str | None:
        """Return the daemon's GraphQL SDL by introspecting it, or ``None``.

        The daemon owns its schema; the console derives its types from it instead
        of hand-maintaining them. This reuses the addon's authenticated connection
        (the admin bearer over the absolute GraphQL URL) to fetch a fresh contract
        — ``manage.py operator_schema`` writes it where frontend codegen reads it.
        Returns ``None`` when the daemon is unset or unreachable.
        """

        if self.admin_bearer is None or self.server_base is None:
            return None
        try:
            data = self._post_json(
                self._with_graphql_path(self.server_base),
                {"query": get_introspection_query()},
            )
        except (OSError, ValueError) as error:
            logger.warning("operator: schema introspection failed: %s", error)
            return None
        result = data.get("data")
        if not isinstance(result, dict):
            return None
        return print_schema(build_client_schema(result))  # type: ignore[arg-type]

    # --- Server-side provisioning over the daemon REST API (admin bearer) -------
    # The daemon owns lifecycle; these let Django drive a render on a user's behalf
    # (the secret value stays server-side, never reaching the browser). Each raises
    # on an unconfigured/unreachable daemon so the caller surfaces a clean failure.

    def set_secret(self, name: str, value: str) -> None:
        """Set a secret value in the operator store (``POST /secrets/{name}``)."""

        self._request("POST", f"{self._base()}/secrets/{quote(name, safe='')}", {"value": value})

    def resolve_template_ref(self, *, name: str, kind: str) -> str | None:
        """Return the daemon's template ref for a template ``name`` + ``kind``.

        The daemon owns the ref format and emits it in its own ``GET /templates``
        listing (its ``path`` there is an absolute filesystem path, not the template
        ref), so match the manifest ``name`` (and ``kind``) — both sides parse it from
        the template's ``_angee`` block — and return the daemon's ``ref``.
        """

        descriptors = self._request("GET", f"{self._base()}/templates")
        for descriptor in _collection_items(descriptors):
            if not isinstance(descriptor, dict):
                continue
            if descriptor.get("name") == name and (not kind or descriptor.get("kind") == kind):
                ref = descriptor.get("ref")
                return str(ref) if ref else None
        return None

    def create_workspace(self, *, template: str, inputs: dict[str, str]) -> str:
        """Render a workspace from a daemon template ref; return the instance name."""

        payload = {"template": template, "inputs": inputs}
        data = self._request("POST", f"{self._base()}/workspaces", payload)
        return str((data or {}).get("name") or "")

    def create_service(self, *, template: str, workspace: str, inputs: dict[str, str], start: bool = True) -> str:
        """Render a service into the stack mounting ``workspace``; return the instance name."""

        payload = {"template": template, "workspace": workspace, "inputs": inputs, "start": start}
        data = self._request("POST", f"{self._base()}/services/create", payload)
        return str((data or {}).get("name") or "")

    def destroy_workspace(self, name: str) -> None:
        """Destroy a workspace and its files (``POST /workspaces/{name}/destroy``)."""

        self._request("POST", f"{self._base()}/workspaces/{quote(name, safe='')}/destroy?purge=true", {})

    def destroy_service(self, name: str) -> None:
        """Destroy (stop + remove) a stack service (``POST /services/{name}/destroy``).

        A service is a stack entry distinct from the workspace it mounts, so it has
        its own lifecycle: tearing down the workspace leaves the service behind (a
        later ``create_service`` then 409s), and a secret change needs the service
        *recreated* — ``destroy_service`` then ``create_service`` over the same
        workspace — to re-resolve its ``${secret.<name>}`` env, not just restarted.
        """

        self._request("POST", f"{self._base()}/services/{quote(name, safe='')}/destroy", {})

    def service_endpoint(self, name: str) -> dict[str, Any]:
        """Return a routed service's reachable endpoint (``GET /services/{name}/endpoint``).

        The daemon owns routing: for a service fronted by the central Caddy it
        returns ``{"routed": true, "url": "wss://<service>.<domain>/", …}`` — a
        browser-reachable WebSocket URL carrying no token. The browser appends an
        operator-minted route token (:meth:`mint_route_token`) as a query parameter,
        which the central Caddy forward-auths against the daemon.
        """

        return cast(dict[str, Any], self._request("GET", f"{self._base()}/services/{quote(name, safe='')}/endpoint"))

    def mint_route_token(self, actor: str, service: str, ttl: str = _DEFAULT_TTL) -> dict[str, Any]:
        """Mint a route token scoping ``actor`` to a routed ``service`` (``POST /tokens/route``).

        Distinct from :meth:`mint_token` (the ``aud=operator`` GraphQL token): this is
        the ``aud=svc:<service>`` token the central Caddy forward-auths on a routed
        service's upgrade, so a browser that holds it can open the service WebSocket
        and nothing else. Short-lived and per-actor; the daemon caps the TTL at 24h.
        """

        payload = {"actor": actor, "service": service, "ttl": ttl}
        return cast(dict[str, Any], self._request("POST", f"{self._base()}/tokens/route", payload))

    # --- Workspace file tools (the AddonInstaller's operator transport) ---------
    # The operator owns the deployment's files + rebuild lifecycle. These read/edit
    # one file under a stack source (e.g. ``app/settings.yaml``) over its file API
    # and trigger a rebuild. The ``etag`` carries optimistic concurrency: read it,
    # echo it on write, and the daemon 409s a stale write rather than clobbering.

    def read_file(self, source: str, path: str) -> RemoteFile:
        """Read ``path`` under a stack ``source`` (``GET /files?source=&path=``)."""

        query = urlencode({"source": source, "path": path})
        data = self._request("GET", f"{self._base()}/files?{query}") or {}
        return RemoteFile(content=str(data.get("content", "")), etag=str(data.get("etag", "")))

    def write_file(self, source: str, path: str, content: str, etag: str = "") -> str:
        """Write ``path`` under a stack ``source`` (``PUT /files?source=&path=``); return the new etag."""

        query = urlencode({"source": source, "path": path})
        data = self._request("PUT", f"{self._base()}/files?{query}", {"content": content, "etag": etag}) or {}
        return str(data.get("etag", ""))

    def stack_build(self) -> str:
        """Trigger a stack rebuild + restart (``POST /stack/build``); return a status marker."""

        data = self._request("POST", f"{self._base()}/stack/build", {})
        status = (data or {}).get("status")
        return str(status) if status else "rebuilding"

    def _base(self) -> str:
        """Return the absolute daemon base, or raise when the daemon is unconfigured."""

        if self.admin_bearer is None or self.server_base is None:
            raise RuntimeError(
                "operator daemon is not configured (ANGEE_OPERATOR_URL / ANGEE_OPERATOR_TOKEN unset).",
            )
        return self.server_base

    def _request(
        self,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int = _PROVISION_TIMEOUT,
    ) -> Any:
        """Issue an authenticated daemon REST call; return the decoded JSON body (or ``None``)."""

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode() if payload is not None else None,
            method=method,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.admin_bearer}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode()
        except urllib.error.HTTPError as error:
            # Surface the daemon's own error message instead of a bare "HTTP 500":
            # the body is JSON like ``{"error": "…"}`` (or text); the caller records it.
            message = f"operator {method} {url.rsplit('/', 1)[-1]}: {_daemon_error(error)}"
            error_class = OperatorDaemonNotFound if error.code == 404 else OperatorDaemonError
            raise error_class(message, status_code=error.code) from error
        return json.loads(body) if body else None

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST with the admin bearer on the short token/introspection budget."""

        return cast(dict[str, Any], self._request("POST", url, payload, timeout=_MINT_TIMEOUT))

    @staticmethod
    def _setting(name: str) -> str | None:
        """Return the named Django setting as a non-empty string, or ``None``."""

        raw = getattr(settings, name, None)
        if raw is not None and (text := str(raw).strip()):
            return text
        return None

    @staticmethod
    def _server_base(*candidates: str | None) -> str | None:
        """Return the absolute daemon base (``scheme://host`` + mount path).

        Server-side calls (mint, introspection) target paths *under* the daemon
        base, so a mount prefix must survive: ``https://host/operator`` yields
        ``…/operator/tokens/mint``, not ``https://host/tokens/mint`` (which could
        hit a different service on the same origin). Only a trailing ``/graphql``
        — the browser GraphQL path — is stripped, leaving the daemon root. The
        browser endpoint may be a same-origin path (``/operator``) with no host,
        so such candidates are skipped in favour of an absolute one.
        """

        for value in candidates:
            if not value:
                continue
            parts = urlsplit(value)
            if not (parts.scheme and parts.netloc):
                continue
            path = parts.path.rstrip("/")
            if path.endswith("/graphql"):
                path = path[: -len("/graphql")]
            return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        return None

    @staticmethod
    def _with_graphql_path(base: str) -> str:
        """Return ``base`` with its path ending in a single ``/graphql``."""

        parts = urlsplit(base)
        path = parts.path.rstrip("/")
        if not path.endswith("/graphql"):
            path = f"{path}/graphql" if path else "/graphql"
        return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _collection_items(value: Any) -> tuple[Any, ...] | list[Any]:
    """Return daemon collection nodes from the REST collection envelope."""

    if isinstance(value, dict) and isinstance(value.get("nodes"), list):
        return value["nodes"]
    return ()


def _daemon_error(error: urllib.error.HTTPError) -> str:
    """Return a human message from a daemon HTTP error: its JSON ``error`` field, or text."""

    raw = error.read().decode(errors="replace").strip()
    detail = raw
    try:
        decoded = json.loads(raw)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        detail = str(decoded.get("error") or decoded.get("reason") or raw)
    return f"HTTP {error.code}: {detail}" if detail else f"HTTP {error.code}"
