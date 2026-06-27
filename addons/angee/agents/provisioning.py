"""Operator provisioning workflows for agents.

The GraphQL schema owns the public mutation names; this module owns the daemon
orchestration behind those mutations: status transitions, render plans, secret
sync, workspace/service creation, reprovision, and teardown.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.apps import apps
from rebac import system_context

from angee.graphql.actions import ActionResult, action_target
from angee.graphql.ids import PublicID
from angee.operator.daemon import OperatorDaemon, OperatorDaemonNotFound

# The inference-credential chains ``_render_plan`` walks: the per-agent override
# (``inference_credential``, with its ``oauth_client`` for an OAuth refresh) and the
# model->provider->credential fallback, joined up front so provisioning reads the
# credential in one query instead of lazy FK fetches.
_PROVISION_CHAIN = (
    "model__provider__credential",
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


def provision_agent(id: PublicID) -> ActionResult:
    """Render an agent into an operator workspace + service and record the instance."""

    with action_target(
        _agent_model(),
        id,
        reason="agents.graphql.provision_agent",
        select_related=_PROVISION_CHAIN,
    ) as agent:
        if agent.workspace:
            return ActionResult(ok=False, message="Agent is already provisioned — deprovision it first.")
        if agent.workspace_template is None:
            return ActionResult(ok=False, message="Set a workspace template on this agent first.")
        if not agent.inference_credential_ready():
            return ActionResult(
                ok=False,
                message="Connect a usable inference credential to this agent's provider before provisioning.",
            )
        agent.mark_provisioning()
    created_workspace: list[str] = []

    def record_workspace(workspace: str) -> None:
        created_workspace.append(workspace)
        with system_context(reason="agents.graphql.provision_agent.workspace_recorded"):
            agent.mark_workspace_provisioned(workspace=workspace)

    def record_service(service: str) -> None:
        with system_context(reason="agents.graphql.provision_agent.service_recorded"):
            agent.mark_service_provisioned(service=service)

    try:
        with system_context(reason="agents.graphql.provision_agent.plan"):
            plan = _render_plan(agent)
        result = _render_agent(
            plan,
            on_workspace_created=record_workspace,
            on_service_created=record_service,
        )
    except Exception as error:  # noqa: BLE001 - a render/plan failure is the result, not a 500
        with system_context(reason="agents.graphql.provision_agent.failed"):
            agent.mark_provision_failed(str(error), clear_instances=bool(created_workspace))
        return ActionResult(ok=False, message=f"Provisioning failed: {error}")
    with system_context(reason="agents.graphql.provision_agent.recorded"):
        agent.mark_provisioned(workspace=result["workspace"], service=result["service"])
    return ActionResult(ok=True, message=f"Provisioned “{result['service'] or result['workspace']}”.")


def reprovision_agent(id: PublicID) -> ActionResult:
    """Recreate an agent's service over its existing workspace, re-syncing secrets."""

    with action_target(
        _agent_model(),
        id,
        reason="agents.graphql.reprovision_agent",
        select_related=_PROVISION_CHAIN,
    ) as agent:
        workspace = agent.workspace
        service = agent.service
        if not workspace:
            return ActionResult(ok=False, message="Agent isn't provisioned — provision it first.")
        if not agent.runtime_backend.renders_service:
            return ActionResult(ok=False, message="This agent's runtime renders no service to reprovision.")
        if not agent.inference_credential_ready():
            return ActionResult(
                ok=False,
                message="Connect a usable inference credential to this agent's provider before reprovisioning.",
            )
        agent.mark_provisioning()
    daemon = OperatorDaemon.from_settings()
    service_destroyed = False
    try:
        with system_context(reason="agents.graphql.reprovision_agent.plan"):
            plan = _render_plan(agent)
        _sync_secrets(daemon, plan)
        if service:
            daemon.destroy_service(service)
            service_destroyed = True
        new_service = _render_service(daemon, plan, workspace)
        if new_service:
            with system_context(reason="agents.graphql.reprovision_agent.service_recorded"):
                agent.mark_service_provisioned(service=new_service)
    except Exception as error:  # noqa: BLE001 - a render/plan failure is the result, not a 500
        with system_context(reason="agents.graphql.reprovision_agent.failed"):
            # Once the old service is destroyed its name is stale; clear it so a later
            # deprovision doesn't try to tear down a service the daemon already removed.
            agent.mark_provision_failed(str(error), clear_service=service_destroyed)
        return ActionResult(ok=False, message=f"Reprovisioning failed: {error}")
    with system_context(reason="agents.graphql.reprovision_agent.recorded"):
        agent.mark_provisioned(workspace=workspace, service=new_service)
    return ActionResult(ok=True, message=f"Recreated service “{new_service}”.")


def deprovision_agent(id: PublicID) -> ActionResult:
    """Tear down an agent's operator workspace and services, then clear the record."""

    with action_target(_agent_model(), id, reason="agents.graphql.deprovision_agent") as agent:
        if not agent.workspace and not agent.service:
            agent.mark_deprovisioned()
            return ActionResult(ok=True, message="Deprovisioned.")
        workspace = agent.workspace
        service = agent.service
        agent.mark_deprovisioning()
    daemon = OperatorDaemon.from_settings()
    service_destroyed = False
    try:
        # The service is a stack entry distinct from the workspace it mounts, so destroy
        # it explicitly before the workspace; otherwise the next provision can 409.
        if service:
            try:
                daemon.destroy_service(service)
            except OperatorDaemonNotFound:
                pass
            service_destroyed = True
        if workspace:
            try:
                daemon.destroy_workspace(workspace)
            except OperatorDaemonNotFound:
                pass
    except Exception as error:  # noqa: BLE001 - teardown failure is the result, not a 500
        with system_context(reason="agents.graphql.deprovision_agent.failed"):
            agent.mark_provision_failed(f"Teardown failed: {error}", clear_service=service_destroyed)
        return ActionResult(ok=False, message=f"Teardown failed: {error}")
    with system_context(reason="agents.graphql.deprovision_agent.recorded"):
        agent.mark_deprovisioned()
    return ActionResult(ok=True, message="Deprovisioned.")


def _render_agent(
    plan: _RenderPlan,
    *,
    on_workspace_created: Callable[[str], None] | None = None,
    on_service_created: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """Drive the daemon render for one agent over its REST API; return instance names.

    The daemon owns the template ref format and the secret store. If service render
    fails after the workspace exists, the workspace is torn down so a retry starts clean.
    """

    daemon = OperatorDaemon.from_settings()
    workspace_ref = daemon.resolve_template_ref(name=plan.workspace_template[0], kind=plan.workspace_template[1])
    if not workspace_ref:
        raise ValueError(f"No operator workspace template matches {plan.workspace_template[0]!r}.")
    _sync_secrets(daemon, plan)
    workspace = daemon.create_workspace(template=workspace_ref, inputs=plan.workspace_inputs)
    if not workspace:
        raise ValueError("The operator did not return a workspace.")
    if on_workspace_created is not None:
        on_workspace_created(workspace)
    try:
        service = _render_service(daemon, plan, workspace)
        if service and on_service_created is not None:
            on_service_created(service)
    except Exception:
        with contextlib.suppress(Exception):  # best-effort rollback; surface the original failure
            daemon.destroy_workspace(workspace)
        raise
    return {"workspace": workspace, "service": service}


def _render_service(daemon: OperatorDaemon, plan: _RenderPlan, workspace: str) -> str:
    """Render an agent's service into ``workspace``; ``""`` for workspace-only agents."""

    if plan.service_template is None:
        return ""
    service_ref = daemon.resolve_template_ref(name=plan.service_template[0], kind=plan.service_template[1])
    if not service_ref:
        raise ValueError(f"No operator service template matches {plan.service_template[0]!r}.")
    return daemon.create_service(template=service_ref, workspace=workspace, inputs=plan.service_inputs)


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
