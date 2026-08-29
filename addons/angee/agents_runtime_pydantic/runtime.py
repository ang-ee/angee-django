"""Agent runtime declaration for the in-process pydantic-ai loop."""

from __future__ import annotations

from angee.agents.runtimes import AgentRuntime
from angee.integrate.credentials import CredentialKind


class PydanticAIRuntime(AgentRuntime):
    """Run persisted chat turns inside the workflow worker with pydantic-ai."""

    key = "pydantic"
    label = "In-process (pydantic-ai)"
    icon = "robot"
    service_template_name = ""
    session_runner_class = "angee.agents_runtime_pydantic.runner.PydanticAISessionRunner"
    supported_credential_kinds = frozenset({CredentialKind.STATIC_TOKEN, CredentialKind.OAUTH})
