"""Non-secret inference deployment identity used by model-owned approval policy."""

from __future__ import annotations

from typing import TypedDict


class InferenceDeploymentIdentity(TypedDict):
    """Non-secret exact identity of one callable inference deployment."""

    model: str
    provider: str
    backend: str
    native_model: str
    endpoint: str
