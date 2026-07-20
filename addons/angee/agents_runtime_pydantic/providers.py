"""Closed provider dispatch from Angee inference backends to pydantic-ai models."""

from __future__ import annotations

from typing import Any

from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider


def model_for_agent(agent: Any) -> Any:
    """Return the pydantic-ai model bound to ``agent``'s live credential."""

    model = getattr(agent, "model", None)
    if model is None:
        raise ValueError("An in-process agent requires an inference model.")
    backend = model.provider.backend
    credential = agent.inference_credential_for_runtime()
    client = backend.async_client(credential=credential)
    handle = str(model.provider_model_name)
    if backend.key == "anthropic":
        return AnthropicModel(handle, provider=AnthropicProvider(anthropic_client=client))
    if backend.key == "openai":
        return OpenAIChatModel(handle, provider=OpenAIProvider(openai_client=client))
    raise ValueError(
        f"The {backend.key!r} inference backend has no pydantic-ai model adapter; "
        "add its closed dispatch to angee.agents_runtime_pydantic.providers."
    )
