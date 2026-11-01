"""Agent definitions, admission policy and native lifecycle transitions."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, ClassVar, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from pydantic_ai.models import Model
from rebac import SubjectRef, current_actor, system_context, to_subject_ref

from angee.agents.models.choices import AgentLifecycle, RuntimeStatus
from angee.agents.models.sessions import AgentSession
from angee.agents.models.tools import MCPServer
from angee.agents.reader_role import sync_resource_reader_role
from angee.agents.resources import AgentResource
from angee.agents.runtimes import AgentRuntime, operator_secret_ref
from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.fields.text import DiagnosticTextField
from angee.base.locks import LockKey, record_lock_key
from angee.base.models.transitions import StateTransitions, save_state, transition
from angee.iam.models import ServiceUserMixin
from angee.iam.models.base import ProtectedDataModel
from angee.iam.models.ownership import OwnerMixin
from angee.iam.permissions import actor_user_id
from angee.operator.daemon import OperatorInstanceKind, WorkspaceStatus
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.resources.mixins import ResourceLoadMixin

_READER_POLICY_FIELDS = ("resource_reader", "lifecycle", "runtime_class", "user_id")


def _update_field_names(update_fields: Any) -> set[str]:
    """Normalize Django's ``update_fields`` save argument to field names."""

    if isinstance(update_fields, str):
        return {update_fields}
    return {str(field) for field in update_fields}


class AgentManager[AgentT: Agent](AngeeHistoryManager[AgentT]):
    """Own the current actor's chat-agent selection."""

    def chat_for_actor(self) -> AgentT | None:
        """Return the user's most recently updated callable agent, if any."""

        user_id = actor_user_id(current_actor())
        if user_id is None:
            return None
        with system_context(reason="agents.chat_for_actor"):
            candidates = (
                self.filter(owner_id=user_id, is_template=False, runtime_status=RuntimeStatus.RUNNING)
                .select_related("model")
                .order_by("-updated_at", "-pk")
            )
            return next((agent for agent in candidates if agent.can_chat), None)


class Agent(ServiceUserMixin, ResourceLoadMixin, OwnerMixin, HistoryMixin, ProtectedDataModel, concrete=True):
    """An agent definition (or, when ``is_template``, an agent template).

    The operator renders an agent into a workspace from ``workspace_template`` and a
    service from the template its ``runtime_class`` declares, writing the
    ``instructions`` into AGENTS.md/CLAUDE.md, the selected skills and MCP servers/tools
    into the workspace, and the model's API credential into the service. ``service`` and
    ``workspace`` hold the operator instance names once rendered.

    Ownership is transferable (:class:`~angee.iam.models.ownership.OwnerMixin`). An agent
    installed before any user exists loads without an owner until an
    administrator transfers it, and deleting the owner clears it, leaving the
    agent to platform administrators.

    Deletion requires teardown and stopped turns on every Django collector path.
    Sessions and turns derive delete authority from this agent in the Zed schema.
    """

    rebac_grantable = {"reader": "share", "editor": "share", "caller": "share_call"}
    resource_class = AgentResource

    sqid_prefix = "agt_"
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    is_template = models.BooleanField(default=False, db_index=True)
    service_user_prefix = "agent"
    instructions = models.TextField(blank=True)
    """The agent's system instructions, rendered into AGENTS.md/CLAUDE.md."""
    model = models.ForeignKey(
        "agents.InferenceModel",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="agents",
    )
    inference_credential = models.ForeignKey(
        "integrate.Credential",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    """Per-agent inference credential override. When set, the agent authenticates inference
    with this credential (e.g. a connected Anthropic OAuth account) instead of the one its
    model's provider integration carries; unset falls back to that catalogue chain."""
    skills = models.ManyToManyField("agents.Skill", blank=True, related_name="agents")
    mcp_servers = models.ManyToManyField("agents.MCPServer", blank=True, related_name="agents")
    mcp_tools = models.ManyToManyField("agents.MCPTool", blank=True, related_name="agents")
    resource_reader = models.BooleanField(default=True)
    """Enable the provisioned in-process reader bundle; false revokes membership on write."""
    runtime_class = ImplClassField(
        AgentRuntime,
        default="none",
    )
    """Registry key for the agent runtime — the program this agent renders into. The
    runtime owns its operator service template, how it consumes an inference credential
    as container env, and the model-handle convention; ``none`` renders no service
    (a workspace-only agent)."""
    workspace_template = models.ForeignKey(
        "integrate_vcs.Template",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    service_inputs = models.JSONField(default=dict, blank=True)
    workspace_inputs = models.JSONField(default=dict, blank=True)
    service = models.CharField(max_length=128, blank=True)
    """Operator service instance this agent records as its own; unique among agents when set."""
    workspace = models.CharField(max_length=128, blank=True)
    """Operator workspace instance this agent records as its own; unique among agents when set."""
    lifecycle = StateField(choices_enum=AgentLifecycle, default=AgentLifecycle.DRAFT)
    """Provision-pipeline position (:class:`AgentLifecycle`), set by the render flow."""
    runtime_status = StateField(
        choices_enum=RuntimeStatus,
        default=RuntimeStatus.STOPPED,
        status_display="dot",
    )
    """Observed run state (:class:`RuntimeStatus`) — the colored dot; ``ERROR`` pairs
    with ``last_error``. Set by the render flow; the daemon owns the live truth."""
    last_error = DiagnosticTextField(blank=True)
    """The reason ``runtime_status`` is ``ERROR`` — the last failed operation."""
    conflict_kind = StateField(
        choices_enum=OperatorInstanceKind, null=True, blank=True, db_index=False
    )
    """Kind of the conflicting instance: the one the daemon refused to create because it exists.

    Recorded with :attr:`conflict_name` from the daemon's own 409 report. Adopt,
    Replace and Deprovision act on it; it is cleared only where it is resolved —
    when this agent records the instance as its own (adopt, or teardown before a
    destroy), when a teardown completes, or when a rename makes a conflicting
    workspace's derived name stale."""
    conflict_name = models.CharField(max_length=128, blank=True)
    """The daemon-reported name of the conflicting instance; the operator owns naming."""

    _INSTANCE_FIELDS: ClassVar[Mapping[OperatorInstanceKind, str]] = {
        OperatorInstanceKind.WORKSPACE: "workspace",
        OperatorInstanceKind.SERVICE: "service",
    }
    """The field that records this agent's operator instance of each kind."""

    lifecycle_transitions = StateTransitions(
        lifecycle,
        {
            AgentLifecycle.DRAFT: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.DEPROVISIONING,
                AgentLifecycle.DEPROVISIONED,
            ],
            AgentLifecycle.PROVISIONING: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.READY,
                AgentLifecycle.DEPROVISIONING,
            ],
            AgentLifecycle.READY: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.DEPROVISIONING,
            ],
            AgentLifecycle.DEPROVISIONING: [
                AgentLifecycle.DEPROVISIONING,
                AgentLifecycle.DEPROVISIONED,
            ],
            AgentLifecycle.DEPROVISIONED: [
                AgentLifecycle.PROVISIONING,
                AgentLifecycle.DEPROVISIONING,
                AgentLifecycle.DEPROVISIONED,
            ],
        },
    )

    objects = AgentManager()

    class Meta:
        """Django model options for agents."""

        abstract = True
        ordering = ("-updated_at",)
        rebac_resource_type = "agents/agent"
        # One operator instance is recorded by at most one agent, so no verb can
        # adopt or destroy an instance another agent records.
        constraints = (
            models.UniqueConstraint(
                fields=("workspace",), condition=~models.Q(workspace=""), name="uniq_agents_agent_workspace"
            ),
            models.UniqueConstraint(
                fields=("service",), condition=~models.Q(service=""), name="uniq_agents_agent_service"
            ),
        )

    def __str__(self) -> str:
        """Return the agent's name."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the agent, service-user label and resource-reader membership.

        Mirrors ``iam.User.save()``: the row save owns a small derived sync, and
        the manager performs the system-owned dependent write. A rename also clears
        a conflicting workspace: the daemon derived its name from the old agent name,
        so the next provision derives a fresh one.
        """

        creating = self._state.adding
        update_fields = kwargs.get("update_fields")
        names = None if update_fields is None else _update_field_names(update_fields)
        should_check_name = creating or names is None or "name" in names
        with transaction.atomic():
            persisted = None
            if not creating and should_check_name:
                persisted = type(self)._base_manager.filter(pk=self.pk).lock_if_supported(no_key=True).values(
                    "name", *_READER_POLICY_FIELDS,
                ).first()
            renamed = not creating and should_check_name and (persisted or {}).get("name") != self.name
            if renamed and self.conflict_kind == OperatorInstanceKind.WORKSPACE:
                self.conflict_kind = None
                self.conflict_name = ""
                if update_fields is not None:
                    kwargs["update_fields"] = {*_update_field_names(update_fields), "conflict_kind", "conflict_name"}
            super().save(*args, **kwargs)
            if creating or renamed:
                self.sync_service_user()
            # Explicit policy writes always reconcile; a full save only when it changed the policy.
            explicit = names is not None and bool({*_READER_POLICY_FIELDS, "user"} & names)
            if not (creating or explicit) and names is None and persisted is not None:
                after = type(self)._base_manager.filter(pk=self.pk).values(*_READER_POLICY_FIELDS).first()
                explicit = after != {field: persisted[field] for field in _READER_POLICY_FIELDS}
            if creating or explicit:
                # Read the persisted policy: a partial save can omit edited fields.
                sync_resource_reader_role(type(self)._base_manager.get(pk=self.pk))

    def principal_subject(self) -> SubjectRef:
        """Return the service user's REBAC subject for actions this agent performs.

        This is distinct from :attr:`owner`: the owner manages the agent definition,
        while the linked non-login user represents the running agent as an actor.
        """

        if self.user_id is None:
            raise ValueError("Agent has no service user and cannot act.")
        user: Any = self.user
        return to_subject_ref(user)

    def is_transient_inference_error(self, error: Exception) -> bool:
        """Classify with the bound inference provider, if this agent has a model."""

        with system_context(reason="agents.agent.inference_error"):
            model: Any = self.model
            if model is None:
                return False
            return bool(model.is_transient_error(error))

    @property
    def runtime_backend(self) -> AgentRuntime:
        """Return the :class:`~angee.agents.runtimes.AgentRuntime` this agent renders into.

        Resolved fresh from ``runtime_class`` per access (the runtime is stateless); it
        owns the service template, the credential→env mapping, and the model handle.
        """

        runtime_class = self.get_runtime_class_impl(default="none")
        return runtime_class()

    @property
    def expects_service(self) -> bool:
        """Whether this agent's runtime renders an operator service."""

        return self.runtime_backend.renders_service

    @property
    def runs_in_process(self) -> bool:
        """Whether this agent's runtime executes turns inside the worker."""

        return self.runtime_backend.runs_in_process

    @property
    def can_chat(self) -> bool:
        """Whether the running agent has an in-process runtime or a rendered service."""

        return self.runtime_status == RuntimeStatus.RUNNING and (self.runs_in_process or bool(self.service))

    def chat_blocker(self) -> str | None:
        """Return the single refusal for in-process chat availability."""

        if not self.runs_in_process:
            return "This agent runtime uses container ACP sessions."
        if not self.can_chat:
            return "Agent is not running — provision it first."
        return None

    # --- Lifecycle verb eligibility -------------------------------------------
    # Each lifecycle verb owns one ``<verb>_blocker``. ``can_<verb>`` is its visibility
    # projection: the same blocker answered from this row's own columns
    # (``prerequisites=False``), so a list can select it without per-row queries. The
    # verb itself enforces the whole blocker, including the configuration it needs.

    @property
    def can_provision(self) -> bool:
        """Visibility projection of :meth:`provision_blocker`."""

        return self.provision_blocker(prerequisites=False) is None

    @property
    def can_adopt(self) -> bool:
        """Visibility projection of :meth:`adopt_blocker`."""

        return self.adopt_blocker(prerequisites=False) is None

    @property
    def can_replace(self) -> bool:
        """Visibility projection of :meth:`replace_blocker`."""

        return self.replace_blocker(prerequisites=False) is None

    @property
    def can_reprovision(self) -> bool:
        """Visibility projection of :meth:`reprovision_blocker`."""

        return self.reprovision_blocker(prerequisites=False) is None

    @property
    def can_deprovision(self) -> bool:
        """Visibility projection of :meth:`deprovision_blocker`."""

        return self.deprovision_blocker() is None

    @property
    def can_delete(self) -> bool:
        """Visibility projection of :meth:`delete_blocker`."""

        return self.delete_blocker() is None

    @classmethod
    def has_active_turns_expression(cls) -> models.Exists:
        """Project active execution across all sessions without exposing transcripts."""

        session_model = cls._meta.get_field("sessions").related_model
        if session_model is None or not issubclass(session_model, AgentSession):
            raise ImproperlyConfigured(f"{cls._meta.label}.sessions must target an AgentSession model.")
        return models.Exists(
            session_model.turn_model()
            .objects.system_context(reason="agents.agent.active_turns")
            .active()
            .filter(session__agent_id=models.OuterRef("pk"))
        )

    def delete_blocker(self) -> str | None:
        """Require teardown and stopped turns before deleting the agent and its sessions."""

        if self.deprovision_blocker() is None:
            return "Deprovision this agent before deleting it."
        active = getattr(self, "_has_active_turns", None)
        if active is None:
            active = type(self).system_queryset().filter(self.has_active_turns_expression(), pk=self.pk).exists()
        if active:
            return "Stop all active turns before deleting this agent."
        return None

    def provision_blocker(self, *, prerequisites: bool = True) -> str | None:
        """Return why Provision may not start now, or ``None``."""

        if blocker := self._in_progress_blocker() or self._conflict_blocker():
            return blocker
        if self.workspace or (
            str(self.lifecycle) == str(AgentLifecycle.READY) and str(self.runtime_status) != str(RuntimeStatus.ERROR)
        ):
            return "Agent is already provisioned — deprovision it first."
        return self._render_blocker() if prerequisites else None

    def adopt_blocker(self, *, prerequisites: bool = True) -> str | None:
        """Return why Adopt may not start now, or ``None``.

        Adopt needs a recorded conflicting instance, and the workspace template the
        daemon's report of it is verified against.
        """

        if blocker := self._in_progress_blocker():
            return blocker
        if self.conflict_kind is None:
            return "This agent records no conflicting operator instance to adopt."
        if prerequisites and self.workspace_template_id is None:
            return "Set a workspace template on this agent first."
        return None

    def replace_blocker(self, *, prerequisites: bool = True) -> str | None:
        """Return why Replace may not start now, or ``None``.

        Replace provisions afresh after its teardown, so it needs everything
        Provision needs; refusing up front means it never destroys an instance it
        cannot rebuild.
        """

        if blocker := self._in_progress_blocker():
            return blocker
        if self.conflict_kind is None:
            return "This agent records no conflicting operator instance to replace."
        return self._render_blocker() if prerequisites else None

    def reprovision_blocker(self, *, prerequisites: bool = True) -> str | None:
        """Return why Reprovision may not start now, or ``None``."""

        if blocker := self._in_progress_blocker():
            return blocker
        if not self.workspace:
            return "Agent isn't provisioned — provision it first."
        if blocker := self._conflict_blocker():
            return blocker
        if not self.runtime_backend.renders_service:
            return "This agent's runtime renders no service to reprovision."
        return self._render_blocker(needs_template=False) if prerequisites else None

    def deprovision_blocker(self) -> str | None:
        """Return why Deprovision has nothing to do, or ``None``.

        Teardown stays available from ``PROVISIONING`` and ``DEPROVISIONING``: a verb
        that stalled there leaves through Deprovision.
        """

        idle = str(self.lifecycle) in {str(AgentLifecycle.DRAFT), str(AgentLifecycle.DEPROVISIONED)}
        if idle and not (self.workspace or self.service or self.conflict_kind is not None):
            return "This agent has no operator instance to tear down."
        return None

    def _in_progress_blocker(self) -> str | None:
        """Refuse a verb while the lifecycle records another one under way."""

        if str(self.lifecycle) == str(AgentLifecycle.PROVISIONING):
            return "This agent is being provisioned; if that stalled, deprovision it to recover."
        if str(self.lifecycle) == str(AgentLifecycle.DEPROVISIONING):
            return "This agent is being torn down; deprovision it again to finish."
        return None

    def _conflict_blocker(self) -> str | None:
        """Refuse a render while a conflicting instance is recorded: the daemon would refuse it again."""

        if self.conflict_kind is None:
            return None
        label = OperatorInstanceKind(self.conflict_kind).label.lower()
        return (
            f"The operator already has {label} “{self.conflict_name}” for this agent: "
            "adopt it, replace it, or deprovision to clear the record."
        )

    def _render_blocker(self, *, needs_template: bool = True) -> str | None:
        """Return what a render of this agent still lacks, or ``None``."""

        if needs_template and not self.runs_in_process and self.workspace_template_id is None:
            return "Set a workspace template on this agent first."
        if not self.inference_credential_ready():
            return "Connect a usable inference credential to this agent's provider first."
        return None

    # --- Operator instances ----------------------------------------------------

    def provisioning_lock_key(self) -> LockKey:
        """Return the advisory lock key every lifecycle verb on this agent shares.

        It serialises the verbs' daemon work on one agent. It is advisory: the
        lifecycle transitions and the unique instance constraints stay authoritative.
        """

        return record_lock_key(self._meta.label_lower, self.pk, "provisioning")

    def instance_recorded_by(self, kind: OperatorInstanceKind, name: str) -> Agent | None:
        """Return another agent that records the operator ``kind`` instance ``name``, or ``None``.

        Rows are read unscoped: which agent records an instance is a fact about every
        agent, not about the rows the current actor can see.
        """

        field = self._INSTANCE_FIELDS[kind]
        return type(self)._base_manager.filter(**{field: name}).exclude(pk=self.pk).first()

    def records_instance(self, kind: OperatorInstanceKind, name: str) -> bool:
        """Whether this agent records the operator ``kind`` instance ``name`` as its own."""

        return bool(name) and getattr(self, self._INSTANCE_FIELDS[kind]) == name

    def workspace_identity_inputs(self) -> dict[str, str]:
        """Return the workspace template inputs that identify this agent's workspace.

        The daemon derives the workspace name from them and records them on the
        workspace, so its report of an existing workspace can be checked against them.
        """

        return {"agent_name": self.name}

    def conflicting_instance_blocker(self, status: WorkspaceStatus, *, template_ref: str | None) -> str | None:
        """Return why the conflicting instance is not this agent's to adopt or destroy, or ``None``.

        ``status`` is the daemon's report of the workspace involved: the conflicting
        workspace itself, or — for a conflicting service — the workspace this agent
        records. ``template_ref`` is the daemon's ref for this agent's workspace
        template. A conflicting workspace must have been rendered from that template
        with this agent's identity inputs. Every service mounting the workspace counts
        toward the instance: no more may mount it than this agent's runtime renders,
        and a conflicting service must be one of them. No workspace or mounting
        service may be recorded by another agent, and none may differ from one this
        agent already records, which recording it would overwrite.

        Limit: the daemon reports no provenance for a service (no template, no inputs),
        so a service counts as this agent's only by mounting the workspace verified here.
        """

        if self.conflict_kind == OperatorInstanceKind.WORKSPACE:
            if template_ref is None or status.template != template_ref:
                return (
                    f"Workspace “{status.name}” was rendered from template “{status.template}”, "
                    f"not this agent's “{template_ref or 'none'}”."
                )
            for key, value in self.workspace_identity_inputs().items():
                recorded = status.inputs.get(key, "")
                if recorded != value:
                    return f"Workspace “{status.name}” was rendered with {key} “{recorded}”, not “{value}”."
        elif self.conflict_name not in status.services:
            return f"Service “{self.conflict_name}” does not mount this agent's workspace “{status.name}”."
        renders = 1 if self.runtime_backend.renders_service else 0
        if len(status.services) > renders:
            return (
                f"Workspace “{status.name}” is mounted by {', '.join(status.services)}, "
                "more services than this agent's runtime renders."
            )
        instances = [(OperatorInstanceKind.SERVICE, service) for service in status.services]
        if self.conflict_kind == OperatorInstanceKind.WORKSPACE:
            instances.insert(0, (OperatorInstanceKind.WORKSPACE, status.name))
        for kind, name in instances:
            label = OperatorInstanceKind(kind).label.lower()
            # Recording it would overwrite — and so forget — this agent's own instance.
            own = getattr(self, self._INSTANCE_FIELDS[kind])
            if own and own != name:
                return f"This agent already records {label} “{own}”, so it cannot also take over “{name}”."
            if other := self.instance_recorded_by(kind, name):
                return f"The operator {label} “{name}” is recorded by agent “{other.name}”."
        return None

    # --- Lifecycle transitions -------------------------------------------------

    @transition(
        lifecycle,
        source=[AgentLifecycle.DRAFT, AgentLifecycle.READY, AgentLifecycle.DEPROVISIONED],
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_provisioning(self) -> None:
        """Enter the provision flow: lifecycle provisioning, run state reset to stopped.

        Not from ``PROVISIONING``: a second provision never runs over one under way.
        """

        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self._transition_fields = {"runtime_status", "last_error"}

    @transition(
        lifecycle,
        source=[AgentLifecycle.DRAFT, AgentLifecycle.READY, AgentLifecycle.DEPROVISIONED],
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_adopting(self, *, workspace: str, service: str) -> None:
        """Record the verified conflicting instance as this agent's own and enter provisioning.

        One write records the workspace and service and clears the conflict, so no
        failure between them can forget the instance. The unique instance constraints
        refuse it when another agent records either.
        """

        self.workspace = workspace
        self.service = service
        self.conflict_kind = None
        self.conflict_name = ""
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self._transition_fields = {
            "workspace",
            "service",
            "conflict_kind",
            "conflict_name",
            "runtime_status",
            "last_error",
        }

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_workspace_provisioned(self, *, workspace: str) -> None:
        """Record the workspace as soon as the operator creates it."""

        self.workspace = workspace
        self.last_error = ""
        self._transition_fields = {"workspace", "last_error"}

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_service_provisioned(self, *, service: str) -> None:
        """Record the service as soon as the operator creates it."""

        self.service = service
        self.last_error = ""
        self._transition_fields = {"service", "last_error"}

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.PROVISIONING,
        on_success=save_state,
    )
    def mark_service_destroyed(self) -> None:
        """Forget the service as soon as the operator confirms it destroyed.

        A service recorded afterwards — usually under the same name — is then never
        mistaken for the destroyed one.
        """

        self.service = ""
        self._transition_fields = {"service"}

    @transition(
        lifecycle,
        source=AgentLifecycle.PROVISIONING,
        target=AgentLifecycle.READY,
        on_success=save_state,
    )
    def mark_provisioned(self, *, workspace: str, service: str = "") -> None:
        """Record the operator instance the provision flow rendered for this agent.

        The daemon owns the workspace/service lifecycle; the server-side provision
        flow renders them and calls this to persist the resulting instance names, mark
        the lifecycle ``READY`` and the run state ``RUNNING``. Clears any prior error.
        ``service`` is optional — a workspace-only agent renders no service.
        """

        self.workspace = workspace
        self.service = service
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.RUNNING)
        self.last_error = ""
        self._transition_fields = {"workspace", "service", "runtime_status", "last_error"}

    @transition(
        lifecycle,
        source=[AgentLifecycle.DRAFT, AgentLifecycle.DEPROVISIONING, AgentLifecycle.DEPROVISIONED],
        target=AgentLifecycle.DEPROVISIONED,
        on_success=save_state,
    )
    def mark_deprovisioned(self) -> None:
        """Clear the operator instance after teardown: lifecycle deprovisioned, run state stopped.

        A completed teardown also resolves the recorded conflicting instance: it was
        either destroyed as this agent's own or left in place as not this agent's.
        """

        self.workspace = ""
        self.service = ""
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.STOPPED)
        self.last_error = ""
        self.conflict_kind = None
        self.conflict_name = ""
        self._transition_fields = {
            "workspace",
            "service",
            "runtime_status",
            "last_error",
            "conflict_kind",
            "conflict_name",
        }

    @transition(
        lifecycle,
        source=[
            AgentLifecycle.DRAFT,
            AgentLifecycle.PROVISIONING,
            AgentLifecycle.READY,
            AgentLifecycle.DEPROVISIONING,
            AgentLifecycle.DEPROVISIONED,
        ],
        target=AgentLifecycle.DEPROVISIONING,
        on_success=save_state,
    )
    def mark_deprovisioning(self, *, workspace: str = "", service: str = "") -> None:
        """Mark the agent as tearing down through the operator teardown flow.

        ``workspace``/``service`` record a verified conflicting instance as this agent's
        own in this same write — clearing the conflict, under the unique instance
        constraints — so the teardown only ever destroys instances this agent records.
        """

        self.last_error = ""
        transition_fields = {"last_error"}
        if workspace or service:
            self.workspace = workspace or self.workspace
            self.service = service or self.service
            self.conflict_kind = None
            self.conflict_name = ""
            transition_fields.update({"workspace", "service", "conflict_kind", "conflict_name"})
        self._transition_fields = transition_fields

    def mark_provision_failed(
        self,
        message: str,
        *,
        destroyed: Mapping[OperatorInstanceKind, str] | None = None,
        conflict_kind: OperatorInstanceKind | None = None,
        conflict_name: str = "",
    ) -> None:
        """Record a failed operation: run state ``ERROR`` (the red dot), reason kept.

        The failure lands on the run-state axis: ``last_error`` holds the reason and the
        dot turns red. ``destroyed`` maps each instance kind to the name the failed
        operation confirmed gone; a recorded name is blanked only while it is still
        that name, so the row never points at a torn-down instance and never forgets
        one recorded later or whose destroy did not succeed.
        ``conflict_kind``/``conflict_name`` record the conflicting instance the daemon
        reported; without them a recorded conflict is kept. Only the fields this
        changes are written. The lifecycle then follows the workspace, never stranding
        mid-flow: an agent left holding a workspace is still provisioned (``READY``),
        one holding none is a clean ``DRAFT`` retry. This deliberately bypasses the
        declared lifecycle graph because the target is data-dependent recovery state,
        not a user-visible lifecycle action.
        """

        transition_fields = {"runtime_status", "last_error"}
        for kind, name in (destroyed or {}).items():
            if name and self.records_instance(kind, name):
                field = self._INSTANCE_FIELDS[kind]
                setattr(self, field, "")
                transition_fields.add(field)
        if conflict_kind is not None:
            self.conflict_kind = conflict_kind
            self.conflict_name = conflict_name
            transition_fields.update({"conflict_kind", "conflict_name"})
        self.runtime_status = cast(RuntimeStatus, RuntimeStatus.ERROR)
        self.last_error = message[:2000]
        self._transition_fields = transition_fields
        self.lifecycle_transitions.force_state(
            self,
            cast(AgentLifecycle, AgentLifecycle.READY if self.workspace else AgentLifecycle.DRAFT),
            reason="agent provision failure reconciles lifecycle from persisted operator instance names",
        )

    def provision_workspace_inputs(self) -> dict[str, str]:
        """Resolve the ``agent-default`` workspace template inputs from this agent.

        The structured fields (name, instructions, MCP servers) are the source of
        truth, so they win over any same-named key in ``workspace_inputs`` (which
        carries template-specific extras only). All values are stringified — Copier
        and the daemon take string answers.
        """

        structured = {
            **self.workspace_identity_inputs(),
            "instructions": self.instructions,
            "mcp_json": json.dumps(self.mcp_config(), separators=(",", ":")),
        }
        merged = {**(self.workspace_inputs or {}), **structured}
        return {key: str(value) for key, value in merged.items()}

    _SERVICE_ENV_INDENT = "      "
    """Indent for spliced ``env:`` lines — must match the service templates' ``env:`` block."""

    def provision_service_inputs(self) -> dict[str, str]:
        """Resolve the structured service-template inputs from this agent.

        Carries the runtime model handle plus the runtime-owned auth env block. The
        secret *value* never appears here — only operator ``${secret.…}`` placeholders;
        the value is synced server-side. The runtime (not the provider) owns how the
        credential becomes env, because the same token feeds different env vars in
        different runtimes. ``service_inputs`` supplies template-specific extras
        (``permission_mode`` etc.) and loses to the structured keys.
        """

        structured: dict[str, str] = {}
        runtime = self.runtime_backend
        model: Any = self.model
        if model is not None:
            structured["model"] = runtime.model_handle(model)
        # Advertise auth only when the runtime renders a service and there is a usable secret
        # to sync — otherwise the rendered service would reference a ${secret.<name>} the
        # operator never gets, and a workspace-only runtime has no service container to read
        # it. This must agree with the readiness gate and the secret sync in the provision
        # flow (both also keyed on ``renders_service``).
        if runtime.renders_service and model is not None and self.inference_secret():
            credential = self.inference_credential_for_runtime()
            backend = getattr(getattr(model, "provider", None), "backend", None)
            if credential is None or backend is None:
                raise ValueError("Inference auth requires a model provider backend.")
            structured["auth_env"] = self._service_env_lines(
                runtime.auth_env(backend=backend, credential=credential, secret_name=self.inference_secret_name())
            )
        # The MCP bearers ride the container env too: one ``${secret.<name>}`` line per
        # credentialed server (the operator resolves it), which the service template renders
        # under the service env and ``.mcp.json`` reads via ``${ANGEE_MCP_BEARER_<…>}``.
        mcp_env = {
            self.mcp_bearer_env(server): operator_secret_ref(secret_name)
            for server, secret_name in self._addressable_mcp_servers()
            if secret_name
        }
        if mcp_env:
            structured["mcp_env"] = self._service_env_lines(mcp_env)
        merged = {**(self.service_inputs or {}), **structured}
        return {key: str(value) for key, value in merged.items()}

    @classmethod
    def _service_env_lines(cls, env: Mapping[str, str]) -> str:
        """Return YAML ``env:`` lines a service template splices verbatim with ``| safe``.

        The provision answer channel is string-only, so the env block is rendered to
        text here (not a structured list the template loops). ``json.dumps`` double-quotes
        each value, and the shared indent must match the template's ``env:`` nesting.
        """

        return "\n".join(f"{cls._SERVICE_ENV_INDENT}{name}: {json.dumps(value)}" for name, value in env.items())

    def service_model_handle(self) -> str:
        """Return the selected model handle in this agent's runtime convention."""

        model = getattr(self, "model", None)
        return self.runtime_backend.model_handle(model) if model is not None else ""

    def mcp_config(self) -> dict[str, Any]:
        """Return the ``.mcp.json`` document for this agent's reachable MCP servers.

        Each server renders its own entry (:meth:`MCPServer.config_entry`); this supplies
        the bearer env var (:meth:`mcp_bearer_env`) for a credentialed server and skips
        servers that aren't addressable. The header expands that env var, which the service
        env sets from the operator secret (:meth:`provision_service_inputs`) — the value
        rides the container env, never the file or the browser. See :meth:`mcp_secrets`.
        """

        servers = {
            server.name: server.config_entry(self.mcp_bearer_env(server) if secret_name else None)
            for server, secret_name in self._addressable_mcp_servers()
        }
        return {"mcpServers": servers}

    def _addressable_mcp_servers(self) -> Iterator[tuple[MCPServer, str]]:
        """Yield ``(server, secret_name)`` for each addressable MCP server, in row order.

        The single owner of "which servers this agent exposes, and the operator secret
        name each credentialed one uses" — so the rendered ``.mcp.json`` header
        (:meth:`mcp_config`) and the value synced under it (:meth:`mcp_secrets`) can't
        drift. ``secret_name`` is ``""`` for an uncredentialed server.
        """

        for server in self.mcp_servers.select_related("credential"):
            if not server.is_addressable:
                continue
            yield server, (self.mcp_secret_name(server) if server.credential_id else "")

    def mcp_secret_name(self, server: MCPServer) -> str:
        """Return the operator secret name holding one MCP server's bearer for this agent.

        Stable and scoped to the agent + server credential: the service env references it
        (``${secret.<name>}`` → the bearer env var) and the provision flow syncs the agent's
        bearer (:meth:`mcp_secrets`) under it (the value never appears in the file or the browser).
        """

        return f"agent-{self.sqid}-mcp-{server.require_credential().sqid}"

    def mcp_bearer_env(self, server: MCPServer) -> str:
        """Return the container env var carrying one MCP server's bearer for this agent.

        The rendered ``.mcp.json`` header reads it via the agent runtime's ``${VAR}``
        expansion; the service env sets it from the operator secret (:meth:`mcp_secret_name`),
        which the operator resolves into the container env. Keyed by the server credential so
        the env name and the ``.mcp.json`` reference can't drift, and unique per server in the
        agent's container. The sqid segment is upper-cased so the env name is portable across
        container runtimes/shells that reject or fold lowercase env names.
        """

        return f"ANGEE_MCP_BEARER_{server.require_credential().sqid.upper()}"

    def mcp_secrets(self) -> dict[str, str]:
        """Return ``{secret_name: bearer_value}`` for every credentialed MCP server.

        The bearer is whatever this agent presents to the server (:meth:`MCPServer.bearer_for`):
        a per-agent derived token for an internal, platform-verified server, or the raw
        credential secret for an external one. Server-side only — the provision flow pushes
        these to the operator secret store so each server's ``${secret.<name>}`` header
        resolves in the container; the raw internal-server secret never reaches a container.
        """

        secrets: dict[str, str] = {}
        for server, secret_name in self._addressable_mcp_servers():
            if not secret_name:
                continue
            server.require_credential().ensure_fresh()
            secrets[secret_name] = server.bearer_for(self)
        return secrets

    def inference_secret_name(self) -> str:
        """Return the operator secret name holding this agent's inference token.

        Stable and agent-scoped — the provision inputs reference it and the
        (server-side) secret sync writes the credential value under it.
        """

        return f"agent-{self.sqid}-inference"

    def inference_secret(self) -> str:
        """Return the inference credential's secret value (API key or OAuth token), or ``""``.

        Server-side only — the value is pushed to the operator secret store under
        ``inference_secret_name()`` and never returned to the browser. An OAuth token
        near expiry is renewed first (:meth:`integrate.Credential.ensure_fresh`) so the value
        frozen into the provisioned service has its full lifetime ahead of it.
        """

        credential = self.inference_credential_for_runtime()
        if credential is None:
            return ""
        credential.ensure_fresh()
        return str(credential.secret_value())

    def provision_inference_secret(self) -> str:
        """Return the runtime-shaped inference secret payload synced to the operator store.

        The value the provision flow stores under :meth:`inference_secret_name`, that the
        service's ``${secret.<name>}`` auth placeholder resolves to in the container: the
        raw credential secret for most runtimes, or a runtime-built payload (OpenCode's
        base64 ``auth.json`` for an OAuth credential — see
        :meth:`~angee.agents.runtimes.AgentRuntime.auth_secret_value`). ``""`` when there is
        no credential to sync. Readiness still gates on :meth:`inference_secret` (the raw
        token), so an empty credential is refused before this richer payload is built.
        A runtime that renders no service has no container to consume the secret, so nothing
        is synced (kept in step with :meth:`provision_service_inputs`' auth-env block).
        """

        runtime = self.runtime_backend
        if not runtime.renders_service:
            return ""
        credential = self.inference_credential_for_runtime()
        if credential is None:
            return ""
        credential.ensure_fresh()
        return runtime.auth_secret_value(credential)

    def inference_credential_ready(self) -> bool:
        """Whether this agent can be provisioned with working inference auth.

        A model-less agent needs no inference credential, so it is always ready. A
        credential-less backend is ready only for an in-process runtime, where its default
        endpoint reaches the host directly. Every attached credential must yield a usable
        secret, and a service-rendering runtime must be able to consume its kind, so an
        unworkable pairing is refused here rather than degrading silently at run time.
        """

        if self.model_id is None:
            return True
        credential = self.inference_credential_for_runtime()
        runtime = self.runtime_backend
        if credential is None:
            model: Any = self.model
            return not model.provider.backend.requires_credential and not runtime.renders_service
        if not self.inference_secret():
            return False
        return not runtime.renders_service or runtime.supports_credential(credential)

    def inference_model(self) -> AbstractAsyncContextManager[Model]:
        """Bind the native model using this agent's credential override."""

        if self.model_id is None:
            raise ValueError("An in-process agent requires an inference model.")
        model: Any = self.model
        return model.bind(credential=self.inference_credential_for_runtime())

    def inference_credential_for_runtime(self) -> Any:
        """Return the ``integrate.Credential`` backing this agent's inference, or ``None``.

        A per-agent ``inference_credential`` override wins (e.g. a connected Anthropic OAuth
        account the user pointed this agent at); otherwise the model's catalogue credential
        (the model→provider→credential chain the catalogue owns), asked of the model rather
        than walked here.
        """

        if self.inference_credential_id is not None:
            return self.inference_credential
        if self.model_id is None:
            return None
        model: Any = self.model
        return model.credential
