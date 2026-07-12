"""Settings fragments required by the agents addon."""

from __future__ import annotations

SETTINGS = {
    # Inference providers select their backend with a provider-owned
    # ``backend_class`` field. ``manual`` lists no models; its catalogue is
    # hand-curated.
    "ANGEE_INFERENCE_BACKEND_CLASSES": {
        "manual": "angee.agents.backends.ManualInferenceBackend",
    },
    # An agent selects its runtime (the program it is rendered into) with a
    # ``runtime_class`` field. The runtime owns how it consumes an inference
    # credential as container env and which operator service template renders it
    # (see ``angee.agents.runtimes``). ``none`` renders no service (workspace-only).
    "ANGEE_AGENT_RUNTIME_CLASSES": {
        "none": "angee.agents.runtimes.NoRuntime",
        "claude_code": "angee.agents.runtimes.ClaudeCodeRuntime",
        "opencode": "angee.agents.runtimes.OpenCodeRuntime",
    },
    # OpenCode + Anthropic Personal-Plans OAuth is off by default: it needs a community
    # auth plugin in the opencode image (see its Dockerfile) and using a Pro/Max token in
    # OpenCode is against Anthropic's terms. Enable only on an image built with the plugin.
    "ANGEE_OPENCODE_OAUTH_ENABLED": False,
    # The agents addon owns the MCP catalogue, so it supplies the bearer→actor
    # verifier the base ``angee.mcp`` runtime calls: it matches an inbound bearer to an
    # ``agents.MCPServer.credential`` and resolves the agent actor (see ``mcp_verifier``).
    "ANGEE_MCP_ACTOR_VERIFIER": "angee.agents.mcp_verifier.resolve_actor",
    # The base audit/revision attribution helper resolves non-user actor subjects through
    # this settings-keyed registry. Agents contribute only their own subject type.
    "ANGEE_ACTOR_USER_RESOLVERS": {
        "agents/agent": "angee.agents.actor_resolvers.agent_user_id",
    },
    # TTL of the per-actor chat route token minted by ``agentChatEndpoint`` (the
    # daemon caps it at 24h). The TTL policy lives here, not as a literal in the
    # resolver; mirrors ``ANGEE_OPERATOR_TOKEN_TTL`` for the GraphQL token.
    "ANGEE_AGENT_CHAT_TOKEN_TTL": "2h",
}
"""Django settings contributed when the agents addon is installed."""
