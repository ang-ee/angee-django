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
import { OperatorTransportProvider, ServicesSection, WorkspacesSection } from "@angee/operator/runtime";
import { useAuthoredMutation, useResourceRecord, type Row } from "@angee/sdk";

import {
  DEPROVISION_AGENT_MUTATION,
  PROVISION_AGENT_MUTATION,
  type ActionResultData,
  type DeprovisionAgentData,
  type IdVariables,
  type ProvisionAgentData,
} from "../documents";

const AGENT_MODEL = "agents.Agent";

// Just what the panel needs: the operator instance names (status filter), and whether
// a workspace template is set (Provision is disabled without one). The rendering and
// credential resolution all happen server-side in `provisionAgent`.
const PROVISION_FIELDS = ["id", "workspace", "service", "workspaceTemplate.path"] as const;

interface AgentProvisionRecord extends Row {
  workspace?: string | null;
  service?: string | null;
  workspaceTemplate?: { path?: string | null } | null;
}

/**
 * Provisioning panel for one agent, embedded in the agent detail via
 * `DataPage.recordExtras`. Provisioning is a single server-side Django flow
 * (`provisionAgent`): it resolves the agent's inputs + credential, syncs the
 * inference secret to the operator, and drives the daemon render over REST. So
 * this panel just triggers it and watches the live workspace/service status,
 * which it reads straight from the daemon via the reused operator widgets.
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
  const [provisionAgent, provisionState] = useAuthoredMutation<ProvisionAgentData, IdVariables>(
    PROVISION_AGENT_MUTATION,
  );
  const [deprovisionAgent, deprovisionState] = useAuthoredMutation<DeprovisionAgentData, IdVariables>(
    DEPROVISION_AGENT_MUTATION,
  );
  const confirm = useConfirm();
  const [error, setError] = React.useState<string | null>(null);
  const busy = provisionState.fetching || deprovisionState.fetching;

  // Both actions return `{ ok, message }`: surface a business failure as an error
  // (the daemon render failed), and refresh the agent + form on success.
  const settle = React.useCallback(
    (result: ActionResultData | undefined): void => {
      if (!result?.ok) throw new Error(result?.message ?? "The action failed.");
      refetch();
      onChanged();
    },
    [onChanged, refetch],
  );

  const handleProvision = React.useCallback(async () => {
    setError(null);
    try {
      settle((await provisionAgent({ id: agentId }))?.provisionAgent);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Provisioning failed.");
    }
  }, [agentId, provisionAgent, settle]);

  const handleDeprovision = React.useCallback(async () => {
    const confirmed = await confirm({
      title: "Deprovision agent?",
      body: "The operator workspace and its services will be destroyed. This cannot be undone.",
      confirm: "Deprovision",
      danger: true,
    });
    if (!confirmed) return;
    setError(null);
    try {
      settle((await deprovisionAgent({ id: agentId }))?.deprovisionAgent);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Deprovisioning failed.");
    }
  }, [agentId, confirm, deprovisionAgent, settle]);

  const agent = record as AgentProvisionRecord | null;
  const provisioned = Boolean(agent?.workspace);
  const hasWorkspaceTemplate = Boolean(agent?.workspaceTemplate?.path);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Provisioning</CardTitle>
      </CardHeader>
      <CardContent>
        {!agent ? (
          <p className="text-13 text-fg-muted">
            {fetching ? "Loading…" : "Save the agent to provision it."}
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            {error ? <Alert intent="danger">{error}</Alert> : null}
            {provisioned ? (
              <>
                <OperatorTransportProvider>
                  {agent.workspace ? (
                    <WorkspacesSection names={[agent.workspace]} title="Workspace" />
                  ) : null}
                  {agent.service ? <ServicesSection names={[agent.service]} title="Service" /> : null}
                </OperatorTransportProvider>
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
                  Render this agent into an operator workspace and service from its templates.
                </p>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  loading={busy}
                  disabled={!hasWorkspaceTemplate}
                  onClick={() => void handleProvision()}
                >
                  <Glyph name="plus" />
                  Provision
                </Button>
                {!hasWorkspaceTemplate ? (
                  <p className="text-13 text-fg-muted">Set a workspace template on this agent first.</p>
                ) : null}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
