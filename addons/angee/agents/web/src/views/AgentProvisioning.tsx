import * as React from "react";
import {
  Alert,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Glyph,
  useConfirm,
} from "@angee/base";
import {
  OperatorTransportProvider,
  ServicesSection,
  WorkspacesSection,
  resolveTemplateRef,
  toAnswerList,
  useOperatorSnapshot,
  useServiceCreate,
  useWorkspaceCreate,
  useWorkspaceDestroy,
} from "@angee/operator/runtime";
import { useAuthoredMutation, useResourceRecord, type Row } from "@angee/sdk";

import {
  DEPROVISION_AGENT_MUTATION,
  PROVISION_AGENT_MUTATION,
  type DeprovisionAgentData,
  type IdVariables,
  type ProvisionAgentData,
  type ProvisionAgentVariables,
} from "../documents";

const AGENT_MODEL = "agents.Agent";

// The agent's provisioning facts: the operator instance names, the templates the
// daemon renders, and the inputs each takes. `*Template.path`/`kind` resolve the
// daemon's own template ref (it owns the ref format — match its `templates` listing).
const PROVISION_FIELDS = [
  "id",
  "workspace",
  "service",
  "workspaceTemplate.path",
  "workspaceTemplate.kind",
  "serviceTemplate.path",
  "serviceTemplate.kind",
  "workspaceInputs",
  "serviceInputs",
] as const;

interface AgentTemplateRef {
  path?: string | null;
  kind?: string | null;
}

interface AgentProvisionRecord extends Row {
  workspace?: string | null;
  service?: string | null;
  workspaceTemplate?: AgentTemplateRef | null;
  serviceTemplate?: AgentTemplateRef | null;
  workspaceInputs?: unknown;
  serviceInputs?: unknown;
}

/**
 * Provisioning panel for one agent, embedded in the agent detail via
 * `DataPage.recordExtras`. Browser-orchestrated: the console renders the agent's
 * workspace/service against the operator daemon and records the result.
 *
 * Two layers, because the daemon provider swaps the ambient urql client: this
 * outer layer runs in the console urql context, so it owns the console-side read
 * (the agent's provisioning fields) and the `provisionAgent`/`deprovisionAgent`
 * write-backs. The inner runtime runs inside `OperatorTransportProvider` (whose
 * urql context is the daemon) and owns the daemon calls + the reused operator
 * status panels.
 */
export function AgentProvisioning({
  agentId,
  onChanged,
}: {
  agentId: string;
  onChanged: () => void;
}): React.ReactElement {
  const { record, fetching, refetch } = useResourceRecord(AGENT_MODEL, agentId, {
    fields: [...PROVISION_FIELDS],
  });
  const [provisionAgent] = useAuthoredMutation<ProvisionAgentData, ProvisionAgentVariables>(
    PROVISION_AGENT_MUTATION,
  );
  const [deprovisionAgent] = useAuthoredMutation<DeprovisionAgentData, IdVariables>(
    DEPROVISION_AGENT_MUTATION,
  );

  const recordProvisioned = React.useCallback(
    async (workspace: string, service: string) => {
      const data = await provisionAgent({ id: agentId, workspace, service });
      if (!data?.provisionAgent.ok) {
        throw new Error(data?.provisionAgent.message ?? "Could not record provisioning.");
      }
      refetch();
      onChanged();
    },
    [agentId, onChanged, provisionAgent, refetch],
  );
  const recordDeprovisioned = React.useCallback(async () => {
    const data = await deprovisionAgent({ id: agentId });
    if (!data?.deprovisionAgent.ok) {
      throw new Error(data?.deprovisionAgent.message ?? "Could not clear provisioning.");
    }
    refetch();
    onChanged();
  }, [agentId, deprovisionAgent, onChanged, refetch]);

  const agent = record as AgentProvisionRecord | null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Provisioning</CardTitle>
      </CardHeader>
      <CardContent>
        {agent ? (
          <OperatorTransportProvider>
            <AgentProvisioningRuntime
              agent={agent}
              onProvisioned={recordProvisioned}
              onDeprovisioned={recordDeprovisioned}
            />
          </OperatorTransportProvider>
        ) : (
          <p className="text-13 text-fg-muted">
            {fetching ? "Loading…" : "Save the agent to provision it."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function AgentProvisioningRuntime({
  agent,
  onProvisioned,
  onDeprovisioned,
}: {
  agent: AgentProvisionRecord;
  onProvisioned: (workspace: string, service: string) => Promise<void>;
  onDeprovisioned: () => Promise<void>;
}): React.ReactElement {
  const confirm = useConfirm();
  const { snapshot } = useOperatorSnapshot({ templates: true });
  const { run: runWorkspaceCreate, result: workspaceCreateState } = useWorkspaceCreate();
  const { run: runServiceCreate, result: serviceCreateState } = useServiceCreate();
  const { run: runWorkspaceDestroy, result: workspaceDestroyState } = useWorkspaceDestroy();
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  const templates = snapshot?.templates ?? [];
  const workspaceRef = resolveTemplateRef(templates, agent.workspaceTemplate);
  const serviceRef = resolveTemplateRef(templates, agent.serviceTemplate);
  const hasService = Boolean(agent.serviceTemplate?.path);
  const provisioned = Boolean(agent.workspace);
  const busy =
    pending ||
    workspaceCreateState.fetching ||
    serviceCreateState.fetching ||
    workspaceDestroyState.fetching;

  const handleProvision = React.useCallback(async () => {
    setError(null);
    setPending(true);
    try {
      if (!workspaceRef) {
        throw new Error(
          "No operator workspace template matches this agent — check the workspace template and that the operator lists it.",
        );
      }
      const created = await runWorkspaceCreate({
        input: { template: workspaceRef, inputs: toAnswerList(agent.workspaceInputs) },
      });
      const workspaceName = created.workspaceCreate?.name;
      if (!workspaceName) throw new Error("The operator did not return a workspace.");
      // Record the workspace before creating the service, so a failed service
      // leaves a recorded, deprovisionable workspace rather than an orphan.
      await onProvisioned(workspaceName, "");
      if (hasService) {
        if (!serviceRef) throw new Error("No operator service template matches this agent.");
        const service = await runServiceCreate({
          input: {
            template: serviceRef,
            workspace: workspaceName,
            inputs: toAnswerList(agent.serviceInputs),
            start: true,
          },
        });
        await onProvisioned(workspaceName, service.serviceCreate?.name ?? "");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Provisioning failed.");
    } finally {
      setPending(false);
    }
  }, [
    agent.serviceInputs,
    agent.workspaceInputs,
    hasService,
    onProvisioned,
    runServiceCreate,
    runWorkspaceCreate,
    serviceRef,
    workspaceRef,
  ]);

  const handleDeprovision = React.useCallback(async () => {
    const confirmed = await confirm({
      title: "Deprovision agent?",
      body: agent.workspace
        ? `The operator workspace “${agent.workspace}” and its services will be destroyed. This cannot be undone.`
        : "Clear this agent's operator instance.",
      confirm: "Deprovision",
      danger: true,
    });
    if (!confirmed) return;
    setError(null);
    setPending(true);
    try {
      if (agent.workspace) {
        await runWorkspaceDestroy({ name: agent.workspace, purge: true });
      }
      await onDeprovisioned();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Deprovisioning failed.");
    } finally {
      setPending(false);
    }
  }, [agent.workspace, confirm, onDeprovisioned, runWorkspaceDestroy]);

  return (
    <div className="flex flex-col gap-4">
      {error ? <Alert intent="danger">{error}</Alert> : null}
      {provisioned ? (
        <>
          {agent.workspace ? (
            <WorkspacesSection names={[agent.workspace]} title="Workspace" />
          ) : null}
          {agent.service ? <ServicesSection names={[agent.service]} title="Service" /> : null}
          <div className="flex justify-end">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              loading={busy}
              onClick={() => void handleDeprovision()}
            >
              <Glyph name="trash" />
              Deprovision
            </Button>
          </div>
        </>
      ) : (
        <div className="flex flex-col items-start gap-2">
          <p className="text-13 text-fg-muted">
            Render this agent into an operator workspace{hasService ? " and service" : ""} from its templates.
          </p>
          <Button
            type="button"
            variant="primary"
            size="sm"
            loading={busy}
            disabled={!workspaceRef}
            onClick={() => void handleProvision()}
          >
            <Glyph name="plus" />
            Provision
          </Button>
          {!agent.workspaceTemplate?.path ? (
            <p className="text-13 text-fg-muted">Set a workspace template on this agent first.</p>
          ) : !workspaceRef ? (
            <p className="text-13 text-fg-muted">
              Waiting for the operator to list a workspace template at “{agent.workspaceTemplate.path}”.
            </p>
          ) : null}
        </div>
      )}
    </div>
  );
}
