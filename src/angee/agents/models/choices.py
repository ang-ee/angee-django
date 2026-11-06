"""Agent choices owners and their native model contracts."""

from __future__ import annotations

from django.db import models


class InferenceModelUse(models.TextChoices):
    """What an inference model is used for (mirrors the LLM catalogue's model use)."""

    CHAT = "chat", "Chat"
    COMPLETION = "completion", "Completion"
    EMBEDDING = "embedding", "Embedding"
    MULTIMODAL = "multimodal", "Multimodal"
    GENERATION = "generation", "Generation"
    IMAGE = "image", "Image"


TEXT_INFERENCE_MODEL_USES = frozenset((InferenceModelUse.CHAT, InferenceModelUse.MULTIMODAL))


IMAGE_INFERENCE_MODEL_USES = frozenset((InferenceModelUse.MULTIMODAL, InferenceModelUse.IMAGE))


class InferenceModelStatus(models.TextChoices):
    """Lifecycle of a model in a provider's catalogue."""

    AVAILABLE = "available", "Available"
    PREVIEW = "preview", "Preview"
    DEPRECATED = "deprecated", "Deprecated"
    RETIRED = "retired", "Retired"


class MCPPlacement(models.TextChoices):
    """Where an MCP server runs relative to the platform."""

    INTERNAL = "internal", "Internal"
    EXTERNAL = "external", "External"


class MCPTransport(models.TextChoices):
    """How an agent reaches an MCP server."""

    STDIO = "stdio", "stdio"
    HTTP = "http", "HTTP"
    SSE = "sse", "SSE"


class AgentLifecycle(models.TextChoices):
    """Where an agent sits in the operator provision pipeline.

    The lifecycle axis: a forward journey the render pipeline drives, distinct from
    the agent's observed run state (:class:`RuntimeStatus`). A provision moves it
    ``DRAFT → PROVISIONING → READY``; a teardown moves it ``→ DEPROVISIONING →
    DEPROVISIONED``. Whether the rendered agent is actually up — and whether the last
    operation failed — is the orthogonal :attr:`Agent.runtime_status`, never folded
    in here.
    """

    DRAFT = "draft", "Draft"
    PROVISIONING = "provisioning", "Provisioning"
    READY = "ready", "Ready"
    DEPROVISIONING = "deprovisioning", "Deprovisioning"
    DEPROVISIONED = "deprovisioned", "Deprovisioned"


class RuntimeStatus(models.TextChoices):
    """The observed run state of a provisioned thing — the colored-dot axis.

    Orthogonal to a provision lifecycle (:class:`AgentLifecycle`): stopped/running/
    error/warning is "is it up right now, and is anything wrong", the grey/green/red/
    amber dot selected by the field's ``status_display="dot"`` metadata. Reused for
    any model that has a run state; the operator daemon reports the same vocabulary
    for its services (see ``docs/frontend/guidelines.md`` for the shared tone mapping).
    """

    STOPPED = "stopped", "Stopped"
    RUNNING = "running", "Running"
    ERROR = "error", "Error"
    WARNING = "warning", "Warning"


class SessionStatus(models.TextChoices):
    """Conversation state projected from the session's active turn."""

    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    CLOSED = "closed", "Closed"
    ERROR = "error", "Error"


class TurnStatus(models.TextChoices):
    """Execution state of one prompt-to-response turn."""

    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELED = "canceled", "Canceled"


ACTIVE_TURN_STATUSES = (TurnStatus.RUNNING,)


ACTIVE_TURN_DELETE_MESSAGE = "An agent turn is still running; stop it first."


OPEN_TURN_STATUSES = (TurnStatus.PENDING, *ACTIVE_TURN_STATUSES)
