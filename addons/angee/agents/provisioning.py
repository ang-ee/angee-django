"""Operator provisioning workflows for agents.

The GraphQL schema owns the public mutation names; this module owns the daemon
orchestration behind those mutations: status transitions, render plans, secret
sync, workspace/service creation, reprovision, teardown, and the two ways out of
a provision the daemon refused because its instance already exists — adopt the
conflicting instance, or replace it.

Every instance name is recorded on the agent the moment the daemon creates it,
and blanked only once its destroy is confirmed, so the row never forgets an
instance the daemon still holds. A conflicting instance is destroyed only after
the agent records it as its own, so the unique instance constraints guarantee no
verb destroys an instance another agent records.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.apps import apps
from django.db import IntegrityError, transaction
from rebac import system_context

from angee.agents.grants import grant_resource_reader_role
from angee.base.transitions import TransitionNotAllowed
from angee.graphql.actions import ActionResult, resolve_action_target
from angee.graphql.ids import PublicID
from angee.iam.service_users import sync_service_user
from angee.jobs.locks import task_lock
from angee.operator.daemon import OperatorDaemon, OperatorDaemonConflict, OperatorInstanceKind, WorkspaceStatus

logger = logging.getLogger(__name__)

_LOCKED = "Another provisioning action is running for this agent; try again when it finishes."

# The inference-credential chains ``_render_plan`` walks: the per-agent override
# (``inference_credential``, with its ``oauth_client`` for an OAuth refresh) and the
# model->provider->credential fallback, joined up front so provisioning reads the
# credential in one query instead of lazy FK fetches.
_PROVISION_CHAIN = (
    "model__provider__credential__oauth_client",
    "model__provider__vendor",
    "workspace_template",
    "user",
    "inference_credential__oauth_client",
)


@dataclass(frozen=True)
class _RenderPlan:
    """Everything the daemon render needs for one agent, gathered under elevation.

    ``*_template`` are the agent template's ``(name, kind)``; the daemon resolves its
    own ref from them. ``secret_value`` is the credential token pushed before render.
    """

    workspace_inputs: dict[str, str]
    service_inputs: dict[str, str]
    secret_name: str
    secret_value: str
    mcp_secrets: dict[str, str]
    workspace_template: tuple[str, str]
    service_template: tuple[str, str] | None


@dataclass
class _CreatedInstances:
    """The instances one operation created, each recorded on the agent as it appears.

    ``reason`` prefixes the elevation reasons of the record writes. :meth:`roll_back`
    undoes them when a later step fails.
    """

    agent: Any
    reason: str
    names: dict[OperatorInstanceKind, str] = field(default_factory=dict)

    def workspace(self, name: str) -> None:
        """Record a workspace the daemon just created."""

        self.names[OperatorInstanceKind.WORKSPACE] = name
        with system_context(reason=f"{self.reason}.workspace_recorded"):
            self.agent.mark_workspace_provisioned(workspace=name)

    def service(self, name: str) -> None:
        """Record a service the daemon just created."""

        self.names[OperatorInstanceKind.SERVICE] = name
        with system_context(reason=f"{self.reason}.service_recorded"):
            self.agent.mark_service_provisioned(service=name)

    def roll_back(self, daemon: OperatorDaemon) -> dict[OperatorInstanceKind, str]:
        """Destroy what was created, service before workspace; return the instances now gone.

        Best effort: a destroy failure is logged and stops the rollback, because the
        caller surfaces the original failure — and the agent keeps every name whose
        destroy did not succeed.
        """

        gone: dict[OperatorInstanceKind, str] = {}
        try:
            _destroy(
                daemon,
                service=self.names.get(OperatorInstanceKind.SERVICE, ""),
                workspace=self.names.get(OperatorInstanceKind.WORKSPACE, ""),
                gone=gone,
            )
        except Exception:  # noqa: BLE001 - a rollback failure never hides the original one
            logger.warning("agents: operator rollback failed; the agent keeps the instances it holds", exc_info=True)
        return gone


@contextmanager
def _locked_agent(id: PublicID, *, reason: str) -> Iterator[Any | None]:
    """Hold the agent's provisioning lock for one verb; yield the agent, or ``None`` when held.

    Every lifecycle verb runs inside this one owner, so two verbs never drive the
    daemon for the same agent at once. The row is read after the lock is taken. The
    lock is advisory — the lifecycle transitions and unique instance constraints stay
    authoritative — and no row lock is held across daemon calls.
    """

    agent_model = _agent_model()
    target = resolve_action_target(agent_model, id, reason=reason)
    with task_lock(target.provisioning_lock_key()) as acquired:
        if not acquired:
            yield None
        else:
            yield resolve_action_target(agent_model, id, reason=reason, select_related=_PROVISION_CHAIN)


def provision_agent(id: PublicID) -> ActionResult:
    """Render an agent into an operator workspace + service and record the instance."""

    with _locked_agent(id, reason="agents.graphql.provision_agent") as agent:
        if agent is None:
            return ActionResult(ok=False, message=_LOCKED)
        return _provision(agent, OperatorDaemon.from_settings())


def adopt_agent(id: PublicID) -> ActionResult:
    """Record the conflicting instance as the agent's own and keep its container as it is."""

    with _locked_agent(id, reason="agents.graphql.adopt_agent") as agent:
        if agent is None:
            return ActionResult(ok=False, message=_LOCKED)
        return _adopt(agent, OperatorDaemon.from_settings())


def replace_agent(id: PublicID) -> ActionResult:
    """Destroy the conflicting instance, then provision the agent afresh."""

    with _locked_agent(id, reason="agents.graphql.replace_agent") as agent:
        if agent is None:
            return ActionResult(ok=False, message=_LOCKED)
        return _replace(agent, OperatorDaemon.from_settings())


def reprovision_agent(id: PublicID) -> ActionResult:
    """Recreate an agent's service over its existing workspace, re-syncing secrets."""

    with _locked_agent(id, reason="agents.graphql.reprovision_agent") as agent:
        if agent is None:
            return ActionResult(ok=False, message=_LOCKED)
        return _reprovision(agent, OperatorDaemon.from_settings())


def deprovision_agent(id: PublicID) -> ActionResult:
    """Tear down an agent's operator workspace and service, then clear the record."""

    with _locked_agent(id, reason="agents.graphql.deprovision_agent") as agent:
        if agent is None:
            return ActionResult(ok=False, message=_LOCKED)
        return _deprovision(agent, OperatorDaemon.from_settings())


def _provision(agent: Any, daemon: OperatorDaemon) -> ActionResult:
    """Render the agent's workspace and service, recording each the moment it exists."""

    with system_context(reason="agents.graphql.provision_agent"):
        if agent.user_id is None:
            sync_service_user(agent, prefix="agent")
        if blocker := agent.provision_blocker():
            return ActionResult(ok=False, message=blocker)
        in_process = agent.runs_in_process
        try:
            agent.mark_provisioning()
            if in_process:
                agent.mark_provisioned(workspace="", service="")
                grant_resource_reader_role(agent)
        except TransitionNotAllowed as error:
            return ActionResult(ok=False, message=f"Provisioning failed: {error}")
    if in_process:
        return ActionResult(ok=True, message="Provisioned in process.")
    created = _CreatedInstances(agent, reason="agents.graphql.provision_agent")
    try:
        with system_context(reason="agents.graphql.provision_agent.plan"):
            plan = _render_plan(agent)
        result = _render_agent(
            daemon,
            plan,
            on_workspace_created=created.workspace,
            on_service_created=created.service,
        )
    except Exception as error:  # noqa: BLE001 - a render/plan failure is the result, not a 500
        return _failed(agent, error, verb="Provisioning", daemon=daemon, created=created)
    with system_context(reason="agents.graphql.provision_agent.recorded"):
        try:
            agent.mark_provisioned(workspace=result["workspace"], service=result["service"])
        except TransitionNotAllowed as error:
            _record_provision_failure(agent, message=str(error))
            return ActionResult(ok=False, message=f"Provisioning failed: {error}")
    return ActionResult(ok=True, message=f"Provisioned “{result['service'] or result['workspace']}”.")


def _adopt(agent: Any, daemon: OperatorDaemon) -> ActionResult:
    """Record the verified conflicting instance as the agent's own; start its service if stopped.

    Adopt keeps the container exactly as it is, with the configuration and credentials
    it was created with: nothing is rendered, recreated or re-synced, and a running
    service is not touched. Reprovision rebuilds the service from the agent's current
    settings afterwards. The workspace must verify as this agent's
    (:meth:`Agent.conflicting_instance_blocker`); the daemon reports no service
    provenance, so the service counts as this agent's only by mounting it. A refused
    adopt changes nothing.
    """

    with system_context(reason="agents.graphql.adopt_agent"):
        if blocker := agent.adopt_blocker():
            return ActionResult(ok=False, message=blocker)
    try:
        status, refusal = _inspect_conflict(agent, daemon)
    except Exception as error:  # noqa: BLE001 - a daemon failure is the result, not a 500
        return ActionResult(ok=False, message=f"Adopt failed: {error}")
    if refusal:
        return ActionResult(ok=False, message=f"Adopt refused: {refusal}")
    if status is None:
        return ActionResult(
            ok=False, message=f"The operator no longer has workspace “{_inspected_workspace(agent)}”; replace instead."
        )
    service = status.services[0] if status.services else ""
    with system_context(reason="agents.graphql.adopt_agent.recorded"):
        try:
            agent.mark_adopting(workspace=status.name, service=service)
        except TransitionNotAllowed as error:
            return ActionResult(ok=False, message=f"Adopt failed: {error}")
        except IntegrityError:
            refused = _recorded_elsewhere(agent, workspace=status.name, service=service)
            return ActionResult(ok=False, message=f"Adopt refused: {refused}")
    try:
        if service and daemon.service_status(service) != "running":
            daemon.start_service(service)
    except Exception as error:  # noqa: BLE001 - a daemon failure is the result, not a 500
        return _failed(agent, error, verb="Adopt", daemon=daemon)
    unserved = not service and agent.runtime_backend.renders_service
    with system_context(reason="agents.graphql.adopt_agent.provisioned"):
        try:
            if unserved:
                agent.mark_provision_failed(f"No service mounts workspace “{status.name}”; reprovision to render one.")
            else:
                agent.mark_provisioned(workspace=status.name, service=service)
        except TransitionNotAllowed as error:
            return ActionResult(ok=False, message=f"Adopt failed: {error}")
    if unserved:
        return ActionResult(
            ok=True, message=f"Adopted “{status.name}”; no service mounts it — reprovision to render one."
        )
    return ActionResult(ok=True, message=f"Adopted “{service or status.name}”.")


def _replace(agent: Any, daemon: OperatorDaemon) -> ActionResult:
    """Destroy the verified conflicting instance, then provision afresh.

    Composes the two verbs that own each half. Refused up front — destroying
    nothing — when the agent could not provision afterwards or when the conflicting
    instance does not verify as this agent's.
    """

    with system_context(reason="agents.graphql.replace_agent"):
        if blocker := agent.replace_blocker():
            return ActionResult(ok=False, message=blocker)
    try:
        status, refusal = _inspect_conflict(agent, daemon)
    except Exception as error:  # noqa: BLE001 - a daemon failure is the result, not a 500
        return ActionResult(ok=False, message=f"Replace failed: {error}")
    if refusal:
        return ActionResult(
            ok=False,
            message=f"Replace refused: {refusal} Deprovision to clear the record without destroying it.",
        )
    teardown = _deprovision(agent, daemon)
    if not teardown.ok:
        return teardown
    return _provision(agent, daemon)


def _reprovision(agent: Any, daemon: OperatorDaemon) -> ActionResult:
    """Destroy the agent's service and render it again over the recorded workspace."""

    with system_context(reason="agents.graphql.reprovision_agent"):
        if agent.user_id is None:
            sync_service_user(agent, prefix="agent")
        if blocker := agent.reprovision_blocker():
            return ActionResult(ok=False, message=blocker)
        try:
            agent.mark_provisioning()
        except TransitionNotAllowed as error:
            return ActionResult(ok=False, message=f"Reprovisioning failed: {error}")
    workspace, service = agent.workspace, agent.service
    created = _CreatedInstances(agent, reason="agents.graphql.reprovision_agent")
    try:
        with system_context(reason="agents.graphql.reprovision_agent.plan"):
            plan = _render_plan(agent)
        _sync_secrets(daemon, plan)
        if service:
            daemon.destroy_service(service)
            # Forget the old service now: the new one usually takes the same name.
            with system_context(reason="agents.graphql.reprovision_agent.service_destroyed"):
                agent.mark_service_destroyed()
        new_service = _render_service(daemon, plan, workspace, on_service_created=created.service)
    except Exception as error:  # noqa: BLE001 - a render/plan failure is the result, not a 500
        return _failed(agent, error, verb="Reprovisioning", daemon=daemon, created=created)
    with system_context(reason="agents.graphql.reprovision_agent.recorded"):
        try:
            agent.mark_provisioned(workspace=workspace, service=new_service)
        except TransitionNotAllowed as error:
            _record_provision_failure(agent, message=str(error))
            return ActionResult(ok=False, message=f"Reprovisioning failed: {error}")
    return ActionResult(ok=True, message=f"Recreated service “{new_service}”.")


def _deprovision(agent: Any, daemon: OperatorDaemon) -> ActionResult:
    """Destroy the agent's instances, then clear the record.

    A recorded conflicting instance that verifies as this agent's is recorded as its
    own first and destroyed with the rest. One that does not verify — or cannot be
    verified — is left in place and the record cleared: a non-destructive way out of
    every conflict. An in-process agent has no operator instance; its teardown closes
    its sessions instead (:func:`_deprovision_in_process`).
    """

    with system_context(reason="agents.graphql.deprovision_agent"):
        if blocker := agent.deprovision_blocker():
            return ActionResult(ok=False, message=blocker)
    if agent.runs_in_process:
        return _deprovision_in_process(agent)
    note = ""
    recorded: dict[str, str] = {}
    if agent.conflict_kind is not None:
        try:
            status, refusal = _inspect_conflict(agent, daemon)
        except Exception as error:  # noqa: BLE001 - a daemon failure is the result, not a 500
            return ActionResult(ok=False, message=f"Teardown failed: {error}")
        if refusal:
            note = f" Left “{agent.conflict_name}” in place: {refusal}"
        elif status is not None:
            if agent.conflict_kind == OperatorInstanceKind.WORKSPACE:
                recorded["workspace"] = status.name
            if status.services:
                recorded["service"] = status.services[0]
    with system_context(reason="agents.graphql.deprovision_agent.started"):
        try:
            agent.mark_deprovisioning(**recorded)
        except TransitionNotAllowed as error:
            return ActionResult(ok=False, message=f"Teardown failed: {error}")
        except IntegrityError:
            return ActionResult(ok=False, message=f"Teardown refused: {_recorded_elsewhere(agent, **recorded)}")
    gone: dict[OperatorInstanceKind, str] = {}
    try:
        _destroy(daemon, service=agent.service, workspace=agent.workspace, gone=gone)
    except Exception as error:  # noqa: BLE001 - teardown failure is the result, not a 500
        with system_context(reason="agents.graphql.deprovision_agent.failed"):
            _record_provision_failure(agent, message=f"Teardown failed: {error}", destroyed=gone)
        return ActionResult(ok=False, message=f"Teardown failed: {error}")
    with system_context(reason="agents.graphql.deprovision_agent.recorded"):
        try:
            agent.mark_deprovisioned()
        except TransitionNotAllowed as error:
            return ActionResult(ok=False, message=f"Teardown failed: {error}")
    return ActionResult(ok=True, message=f"Deprovisioned.{note}")


def _deprovision_in_process(agent: Any) -> ActionResult:
    """Tear down an in-process agent and close its sessions in one transaction.

    The agent row is locked first — the lock :meth:`AgentSessionManager.start` takes —
    so the order is always agent, then session, and no session can start after the
    teardown commits. The caller's provisioning lock is a non-blocking try-lock, so it
    never waits on, and cannot deadlock with, this row lock.
    """

    try:
        with system_context(reason="agents.graphql.deprovision_agent.in_process"), transaction.atomic():
            locked = type(agent).system_queryset(lock=("self",)).get(pk=agent.pk)
            locked.mark_deprovisioning()
            apps.get_model("agents", "AgentSession").objects.close_for_agent_as_system(locked)
            locked.mark_deprovisioned()
    except TransitionNotAllowed as error:
        return ActionResult(ok=False, message=f"Teardown failed: {error}")
    return ActionResult(ok=True, message="Deprovisioned.")


def _inspect_conflict(agent: Any, daemon: OperatorDaemon) -> tuple[WorkspaceStatus | None, str | None]:
    """Read the workspace a recorded conflict involves and judge whether it is this agent's.

    That is the conflicting workspace, or for a conflicting service the workspace this
    agent records. Returns the daemon's report (``None`` when the daemon no longer has
    it) and the agent's refusal (``None`` when verified). A conflicting service while
    the agent records no workspace cannot be verified: the daemon is not asked, and
    the refusal says so. Daemon failures raise.
    """

    workspace = _inspected_workspace(agent)
    if not workspace:
        return None, (
            f"Service “{agent.conflict_name}” cannot be verified: this agent records no workspace for it to mount."
        )
    status = daemon.workspace_status(workspace)
    if status is None:
        return None, None
    template = agent.workspace_template
    template_ref = daemon.resolve_template_ref(name=template.name, kind=template.kind) if template else None
    with system_context(reason="agents.graphql.conflict.inspect"):
        return status, agent.conflicting_instance_blocker(status, template_ref=template_ref)


def _inspected_workspace(agent: Any) -> str:
    """Return the workspace a recorded conflict involves: itself, or the one a conflicting service mounts."""

    return str(agent.conflict_name if agent.conflict_kind == OperatorInstanceKind.WORKSPACE else agent.workspace)


def _recorded_elsewhere(agent: Any, *, workspace: str = "", service: str = "") -> str:
    """Name the other agent that records one of these instances (admin-only result text)."""

    instances = ((OperatorInstanceKind.WORKSPACE, workspace), (OperatorInstanceKind.SERVICE, service))
    with system_context(reason="agents.graphql.conflict.recorded_elsewhere"):
        for kind, name in instances:
            if name and (other := agent.instance_recorded_by(kind, name)):
                return f"The operator {kind.label.lower()} “{name}” is recorded by agent “{other.name}”."
    return "Another agent records this operator instance."


def _failed(
    agent: Any,
    error: Exception,
    *,
    verb: str,
    daemon: OperatorDaemon,
    created: _CreatedInstances | None = None,
) -> ActionResult:
    """Record a failed verb under the one rule that recognises a daemon conflict.

    A 409 over a workspace or service no agent records is an outcome, not a failure
    to undo: what this verb created stays recorded and the conflicting instance is
    recorded as the agent's conflict. Any other failure — including a 409 over an
    instance this agent or another one already records — rolls back what this verb
    ``created``. The admin-only result may name another agent; the persisted error
    never does.
    """

    reason = f"agents.graphql.{verb.lower()}.failed"
    conflict = error if isinstance(error, OperatorDaemonConflict) and error.kind is not None and error.name else None
    own = other = None
    if conflict is not None and conflict.kind is not None:
        own = agent.records_instance(conflict.kind, conflict.name)
        with system_context(reason=reason):
            other = agent.instance_recorded_by(conflict.kind, conflict.name)
    if conflict is not None and conflict.kind is not None and not own and other is None:
        with system_context(reason=reason):
            _record_provision_failure(
                agent,
                message=str(conflict),
                conflict_kind=conflict.kind,
                conflict_name=conflict.name,
            )
        return ActionResult(ok=False, message=f"{verb} failed: {conflict}")
    destroyed = created.roll_back(daemon) if created and created.names else {}
    message = shown = str(error)
    if isinstance(error, IntegrityError):
        message = shown = "Another agent records an operator instance this agent tried to record."
    elif conflict is not None and conflict.kind is not None and other is not None:
        label = conflict.kind.label.lower()
        hint = "" if agent.workspace else "; rename this agent to provision it separately"
        message = f"The operator {label} “{conflict.name}” is recorded by another agent{hint}."
        shown = f"The operator {label} “{conflict.name}” is recorded by agent “{other.name}”{hint}."
    with system_context(reason=reason):
        _record_provision_failure(agent, message=message, destroyed=destroyed)
    return ActionResult(ok=False, message=f"{verb} failed: {shown}")


def _record_provision_failure(
    agent: Any,
    message: str,
    *,
    destroyed: Mapping[OperatorInstanceKind, str] | None = None,
    conflict_kind: OperatorInstanceKind | None = None,
    conflict_name: str = "",
) -> None:
    """Persist a failure without hiding the action result; log when the row moved on."""

    try:
        agent.mark_provision_failed(
            message,
            destroyed=destroyed,
            conflict_kind=conflict_kind,
            conflict_name=conflict_name,
        )
    except TransitionNotAllowed:
        logger.warning(
            "agents: agent %s changed state concurrently; its failure was not recorded: %s",
            agent.pk,
            message,
            exc_info=True,
        )


def _destroy(
    daemon: OperatorDaemon,
    *,
    service: str = "",
    workspace: str = "",
    gone: dict[OperatorInstanceKind, str],
) -> None:
    """Destroy ``service``, then the ``workspace`` it mounts; note each destroyed name in ``gone``.

    The service is a stack entry distinct from the workspace it mounts, so it is
    destroyed explicitly and first; otherwise the next provision can 409. The daemon
    client treats an instance it reports absent by name as destroyed. Any other
    failure propagates and stops the teardown — a workspace is never destroyed under
    a service still mounting it — leaving ``gone`` naming exactly the instances
    confirmed absent.
    """

    if service:
        daemon.destroy_service(service)
        gone[OperatorInstanceKind.SERVICE] = service
    if workspace:
        daemon.destroy_workspace(workspace)
        gone[OperatorInstanceKind.WORKSPACE] = workspace


def _render_agent(
    daemon: OperatorDaemon,
    plan: _RenderPlan,
    *,
    on_workspace_created: Callable[[str], None],
    on_service_created: Callable[[str], None],
) -> dict[str, str]:
    """Drive the daemon render for one agent over its REST API; return instance names.

    The daemon owns the template ref format and the secret store. Each instance is
    handed to its ``on_*_created`` callback the moment the daemon creates it; the
    caller rolls back what was created when a later step fails.
    """

    workspace_ref = _workspace_template_ref(daemon, plan)
    _sync_secrets(daemon, plan)
    workspace = daemon.create_workspace(template=workspace_ref, inputs=plan.workspace_inputs)
    if not workspace:
        raise ValueError("The operator did not return a workspace.")
    on_workspace_created(workspace)
    service = _render_service(daemon, plan, workspace, on_service_created=on_service_created)
    return {"workspace": workspace, "service": service}


def _render_service(
    daemon: OperatorDaemon,
    plan: _RenderPlan,
    workspace: str,
    *,
    on_service_created: Callable[[str], None],
) -> str:
    """Render and start an agent service; ``""`` for workspace-only agents.

    Creation and start are separate daemon calls so the created service reaches
    ``on_service_created`` — recorded, and rolled back by the caller — before
    ``ServiceUp`` runs: the daemon deliberately keeps the manifest entry when the
    start fails.
    """

    if plan.service_template is None:
        return ""
    service_ref = daemon.resolve_template_ref(name=plan.service_template[0], kind=plan.service_template[1])
    if not service_ref:
        raise ValueError(f"No operator service template matches {plan.service_template[0]!r}.")
    service = daemon.create_service(
        template=service_ref,
        workspace=workspace,
        inputs=plan.service_inputs,
        start=False,
    )
    if not service:
        raise ValueError("The operator did not return a service.")
    on_service_created(service)
    daemon.start_service(service)
    return service


def _workspace_template_ref(daemon: OperatorDaemon, plan: _RenderPlan) -> str:
    """Return the daemon's ref for the agent's workspace template, or raise."""

    name, kind = plan.workspace_template
    ref = daemon.resolve_template_ref(name=name, kind=kind)
    if not ref:
        raise ValueError(f"No operator workspace template matches {name!r}.")
    return ref


def _render_plan(agent: Any) -> _RenderPlan:
    """Build the operator render plan from an agent's templates, inputs, and secrets."""

    workspace_template = agent.workspace_template
    runtime = agent.runtime_backend
    return _RenderPlan(
        workspace_inputs=agent.provision_workspace_inputs(),
        service_inputs=agent.provision_service_inputs(),
        secret_name=agent.inference_secret_name(),
        secret_value=agent.provision_inference_secret(),
        mcp_secrets=agent.mcp_secrets(),
        workspace_template=((workspace_template.name, workspace_template.kind) if workspace_template else ("", "")),
        service_template=(
            (runtime.service_template_name, runtime.service_template_kind) if runtime.renders_service else None
        ),
    )


def _sync_secrets(daemon: OperatorDaemon, plan: _RenderPlan) -> None:
    """Push the agent's inference + MCP secret values to the operator store."""

    if plan.secret_value:
        daemon.set_secret(plan.secret_name, plan.secret_value)
    for name, value in sorted(plan.mcp_secrets.items()):
        daemon.set_secret(name, value)


def _agent_model() -> Any:
    """Return the composed runtime Agent model without pinning it at import time."""

    return apps.get_model("agents", "Agent")
