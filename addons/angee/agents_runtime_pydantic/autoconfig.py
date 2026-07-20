"""Settings fragments required by the pydantic-ai runtime addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_AGENT_RUNTIME_CLASSES.pydantic": "angee.agents_runtime_pydantic.runtime.PydanticAIRuntime",
}
"""Django settings contributed when the pydantic-ai runtime is installed."""
