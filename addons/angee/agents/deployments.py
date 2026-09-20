"""Project policy for approving inference deployments by caller-defined role."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.conf import settings


def validate_approved_deployment(model: Any | None, *, role: str) -> None:
    """Fail closed when the configured role policy excludes ``model``.

    Roles are consumer vocabulary (for example ``mapping`` or ``recognition``);
    agents owns the common endpoint identity and exact allowlist comparison.
    ``None`` means that the caller has elected not to invoke a model.
    """

    if model is None:
        return
    policy = getattr(settings, "ANGEE_INFERENCE_APPROVED_DEPLOYMENTS", None)
    if policy is None:
        return
    if not isinstance(policy, Mapping):
        raise ValueError("The inference deployment approval policy is invalid.")
    approved = policy.get(role)
    if not isinstance(approved, (list, tuple)) or not all(isinstance(item, Mapping) for item in approved):
        raise ValueError(f"The inference {role} deployment approval policy is invalid.")
    identity = model.deployment_identity()
    if not any(dict(item) == identity for item in approved):
        raise ValueError(f"The configured {role} model deployment is not approved.")
