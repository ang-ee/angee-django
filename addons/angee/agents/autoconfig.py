"""Settings fragments required by the agents addon."""

from __future__ import annotations

SETTINGS = {
    # The ``InferenceProvider.backend_class`` registry: each key a provider row may
    # name → the dotted path of the ``InferenceBackend`` it resolves to. agents ships
    # only the built-in ``manual`` backend (``ImplClassField`` requires a non-empty
    # registry); a vendor backend addon adds its own impl with a yamlconf dotted key
    # (``"ANGEE_INFERENCE_BACKEND_CLASSES.anthropic": "…"``). See ``ImplClassField``.
    "ANGEE_INFERENCE_BACKEND_CLASSES": {"manual": "angee.agents.backends.ManualInferenceBackend"},
    # The agents addon owns the MCP catalogue, so it supplies the bearer→actor
    # verifier the base ``angee.mcp`` runtime calls: it matches an inbound bearer to an
    # ``agents.MCPServer.credential`` and resolves the agent actor (see ``mcp_verifier``).
    "ANGEE_MCP_ACTOR_VERIFIER": "angee.agents.mcp_verifier.resolve_actor",
    # TTL of the per-actor chat route token minted by ``agentChatEndpoint`` (the
    # daemon caps it at 24h). The TTL policy lives here, not as a literal in the
    # resolver; mirrors ``ANGEE_OPERATOR_TOKEN_TTL`` for the GraphQL token.
    "ANGEE_AGENT_CHAT_TOKEN_TTL": "2h",
}
"""Django settings contributed when the agents addon is installed."""
