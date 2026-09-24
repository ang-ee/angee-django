"""Authorize, invoke and account for one workflow inference call."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from django.apps import apps
from django.core.exceptions import PermissionDenied
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import BinaryContent, ModelMessage, ModelResponse
from rebac import system_context

from angee.agents.models import (
    IMAGE_INFERENCE_MODEL_USES,
    TEXT_INFERENCE_MODEL_USES,
    InferenceModelUse,
    InferenceOutputError,
    InferenceOutputSchema,
    InferenceResult,
)
from angee.base.db import get_write_alias, related_on
from angee.workflows.steps import TransientStepError


class InferRequest(BaseModel):
    """Native request arguments; the selected backend validates settings."""

    model_config = ConfigDict(extra="forbid")
    messages: list[ModelMessage]
    images: list[BinaryContent] = Field(default_factory=list)
    output_schema: InferenceOutputSchema | None = None
    # ModelSettings includes httpx.Timeout, which has no Pydantic JSON schema.
    settings: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"widget": "json"})


class InferenceCallError(RuntimeError):
    """A terminal provider outcome, distinct from invocation authorization errors."""

    def __init__(
        self,
        error: Exception,
        *,
        response: ModelResponse | None,
        usage: dict[str, int],
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.response = response
        self.usage = usage


def call_inference(
    step_run: Any,
    model: Any,
    request: InferRequest,
    *,
    role: str,
    uses: Collection[InferenceModelUse] | None = None,
    using: str | None = None,
) -> InferenceResult:
    """Resolve and authorize once, then classify failures and debit exactly once.

    Admission failures never invoke a provider or debit a budget. Returned usage
    survives structured-output decoding failures. A debit failure propagates to
    the attempt owner rather than becoming an ordinary provider outcome.
    """

    alias = get_write_alias(type(step_run), using=using, instance=step_run)
    with system_context(reason="workflows_agents.inference.resolve"):
        if isinstance(model, str):
            model = apps.get_model("agents", "InferenceModel").objects.db_manager(alias).get(sqid=model)
        run: Any = related_on(step_run, "run", using=alias)
        actor = run.admission_actor(using=alias)
        if actor is None:
            raise PermissionDenied("Inference requires the workflow admission actor.")
        if uses is None:
            uses = IMAGE_INFERENCE_MODEL_USES if request.images else TEXT_INFERENCE_MODEL_USES
        model.require_usable(actor, role, uses=uses, using=alias)
        provider: Any = related_on(model, "provider", using=alias)
        backend = provider.backend
    usage: dict[str, int] = {}
    try:
        result = model.infer(
            request.messages,
            images=request.images,
            output_schema=request.output_schema,
            settings=request.settings,
            using=alias,
        )
        usage = result.usage
        return result
    except Exception as error:  # noqa: BLE001 - provider SDKs have unrelated exception trees.
        if isinstance(error, TransientStepError):
            raise
        if backend.is_transient_error(error):
            raise TransientStepError(str(error)) from error
        response = None
        if isinstance(error, InferenceOutputError):
            response, usage = error.response, error.usage
        raise InferenceCallError(error, response=response, usage=usage) from error
    finally:
        run.debit_budget(usage, using=alias)
