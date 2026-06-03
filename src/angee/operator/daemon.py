"""The local operator daemon as seen from Django: endpoint + token minting."""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

logger = logging.getLogger(__name__)

_DEFAULT_BASE = "/operator"
"""Same-origin reverse-proxy base — keeps default deployments CORS-free."""

_DEFAULT_TTL = "1h"
"""Lifetime requested for a minted connection token (the daemon caps at 24h)."""

_MINT_TIMEOUT = 5
"""Seconds to wait on the server-side mint before hiding the connection."""


@dataclass(frozen=True, slots=True)
class OperatorDaemon:
    """The operator daemon bridge resolved from settings.

    ``endpoint`` is the browser-visible GraphQL URL handed to an authorized
    actor. ``server_base`` and ``admin_bearer`` are server-side only — the admin
    bearer never reaches the browser; it is the credential used to mint a
    short-lived, scoped per-actor token via :meth:`mint_token`.
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
            admin_bearer=cls._setting("ANGEE_OPERATOR_TOKEN", "ANGEE_SECRET_OPERATOR_TOKEN"),
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
        from graphql import build_client_schema, get_introspection_query, print_schema

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

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` as JSON with the admin bearer; return the decoded body."""

        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.admin_bearer}",
            },
        )
        with urllib.request.urlopen(request, timeout=_MINT_TIMEOUT) as response:
            return json.loads(response.read().decode())

    @staticmethod
    def _setting(name: str, *fallback_env: str) -> str | None:
        """Return the first non-empty value from the setting then the env keys."""

        candidates = (
            getattr(settings, name, None),
            *(os.environ.get(key) for key in (name, *fallback_env)),
        )
        for raw in candidates:
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
