"""FastMCP token verifier: authenticate the inbound bearer to a REBAC actor.

Authentication is the transport's job (rebac proposal 0004), so it lives here, not
in rebac. The bearer→actor map belongs to whichever addon owns the MCP catalogue;
it is named by ``ANGEE_MCP_ACTOR_VERIFIER`` and this wraps it as a FastMCP
:class:`~mcp.server.auth.provider.TokenVerifier`. FastMCP then gates every call
(``401`` on a bad bearer) and carries the resolved actor on the request, where
:func:`angee.mcp.actors.actor_from_request` reads it back as the canonical subject
string on the token's ``subject`` field.
"""

from __future__ import annotations

from collections.abc import Callable

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils.module_loading import import_string
from rebac import SubjectRef

from mcp.server.auth.provider import AccessToken, TokenVerifier

MCPActorVerifier = Callable[[str], SubjectRef | None]
"""A ``verify(bearer) -> SubjectRef | None`` callable named by ``ANGEE_MCP_ACTOR_VERIFIER``."""


class RebacTokenVerifier(TokenVerifier):
    """Resolve a bearer to its REBAC actor, carried as the token ``subject``.

    Declines (``None`` → ``401``) for an empty bearer, an unconfigured catalogue
    verifier, or a bearer no credential matches — the fail-closed posture: an
    unauthenticated MCP request reaches no tool.
    """

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an :class:`AccessToken` carrying the resolved actor, or ``None``."""

        verifier = _verifier()
        if verifier is None or not token:
            return None
        actor = await sync_to_async(verifier)(token)
        if actor is None:
            return None
        subject = str(actor)
        # ``subject`` carries the actor (read back by ``actor_from_request``); ``client_id``
        # mirrors it because this bearer model has no separate OAuth client identity, and
        # FastMCP requires the field to be non-empty.
        return AccessToken(token=token, client_id=subject, scopes=[], subject=subject)


def _verifier() -> MCPActorVerifier | None:
    """Return the configured catalogue bearer→actor verifier, or ``None`` when unset.

    ``None`` keeps the base addon importable without a catalogue owner (it ships no
    verifier of its own); every bearer then declines and REBAC denies the request.
    """

    dotted = getattr(settings, "ANGEE_MCP_ACTOR_VERIFIER", "")
    return import_string(dotted) if dotted else None
