import * as React from "react";
import type {
  Row,
} from "@angee/resources";
import {
  useOne,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";
import {
  OperatorTransportProvider,
  ServiceLogs,
  ServiceRow,
  WorkspaceSources,
  WorkspaceRow,
  useWorkspaceStatus,
  type WorkspaceStatusResult,
  } from "@angee/operator/runtime";
import {
  refineFieldsFromPaths,
  } from "@angee/refine";
import {
  refineResourceName,
} from "@angee/resources";
import {
  useModelMetadata,
} from "@angee/resources";

import { useAgentsT } from "../i18n";
import { agentLifecycle, agentRuntime, stringField } from "./agent-record";

const AGENT_MODEL = "agents.Agent";

const PROVISION_FIELDS = [
  "id",
  "lifecycle",
  "runtime_status",
  "last_error",
  "workspace",
  "service",
  "service_template.id",
  "workspace_template.path",
] as const;

interface AgentProvisionRecord extends Row {
  lifecycle?: string | null;
  runtime_status?: string | null;
  last_error?: string | null;
  workspace?: string | null;
  service?: string | null;
  service_template?: { id?: string | null } | null;
  workspace_template?: { path?: string | null } | null;
}

export type AgentProvisioningPane = "service" | "workspace";

/**
 * Service/workspace runtime panel for one agent. The form toolbar owns lifecycle
 * actions; these record tabs report the rendered operator state.
 */
export function AgentProvisioning({
  agentId,
  pane,
}: {
  agentId: string;
  pane: AgentProvisioningPane;
}): React.ReactElement {
  const t = useAgentsT();
  const metadata = useModelMetadata(AGENT_MODEL);
  const resource = metadata?.resource ?? null;
  const fields = React.useMemo(
    () => refineFieldsFromPaths([...PROVISION_FIELDS]),
    [],
  );
  const run = useOne<RowRecord, HttpError>({
    resource: resource ? refineResourceName(resource) : "__angee_disabled__",
    id: agentId,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    queryOptions: {
      enabled: Boolean(agentId) && resource !== null,
    },
  });

  const agent = (run.result as AgentProvisionRecord | undefined) ?? null;
  const fetching = run.query.isFetching;
  const workspace = stringField(agent, "workspace");
  const service = stringField(agent, "service");
  const lifecycle = agentLifecycle(agent);
  const active = isLifecycleActive(lifecycle);
  const expectsService = Boolean(agent?.service_template);
  const missingRenderedInstances =
    agentRuntime(agent) === "RUNNING" && (!workspace || (expectsService && !service));
  const showRuntime = active || Boolean(workspace) || missingRenderedInstances;
  const hasWorkspaceTemplate = Boolean(agent?.workspace_template?.path);

  // No poll: `agents.Agent` declares `changes(Agent, field="agentChanged")`, so the
  // record auto-invalidates live through the change subscription; workspace state
  // streams over `onWorkspaceStatusChange` and service logs over the `ServiceLogs`
  // socket.

  return (
    <div className="flex flex-col gap-5">
      {!agent ? (
        <p className="text-13 text-fg-muted">
          {fetching
            ? t("agents.provisioning.loading")
            : t("agents.provisioning.saveFirst")}
        </p>
      ) : (
        <>
          {agent.last_error ? (
            <p className="text-13 text-danger-text">{String(agent.last_error)}</p>
          ) : null}
          {showRuntime ? (
            <OperatorTransportProvider>
              <AgentOperatorRuntime
                expectsService={expectsService}
                pane={pane}
                service={service}
                lifecycle={lifecycle}
                workspace={workspace}
              />
            </OperatorTransportProvider>
          ) : (
            <div className="flex flex-col items-start gap-2">
              <p className="text-13 text-fg-muted">
                {t("agents.provisioning.intro")}
              </p>
              {!hasWorkspaceTemplate ? (
                <p className="text-13 text-fg-muted">
                  {t("agents.provisioning.needsTemplate")}
                </p>
              ) : null}
            </div>
          )}
        </>
      )}
    </div>
  );
}

type RowRecord = BaseRecord & Row;

function AgentOperatorRuntime({
  expectsService,
  pane,
  service,
  lifecycle,
  workspace,
}: {
  expectsService: boolean;
  pane: AgentProvisioningPane;
  service: string;
  lifecycle: string;
  workspace: string;
}): React.ReactElement {
  const runtime = useProvisionRuntime(workspace);

  return pane === "service" ? (
    <ServiceRuntimeView
      error={runtime.error}
      expectsService={expectsService}
      service={service}
    />
  ) : (
    <WorkspaceRuntimeView
      error={runtime.error}
      status={runtime.workspaceStatus}
      workspace={workspace}
      lifecycleStatus={lifecycle}
    />
  );
}

function WorkspaceRuntimeView({
  error,
  lifecycleStatus,
  status,
  workspace,
}: {
  error: string;
  lifecycleStatus: string;
  status: WorkspaceStatusResult["status"];
  workspace: string;
}): React.ReactElement {
  const t = useAgentsT();
  const sources = status?.sources ?? [];
  const workspaceError = status?.error ?? status?.innerError ?? "";

  return (
    <div className="flex flex-col gap-4">
      {error ? <p className="text-13 text-danger-text">{error}</p> : null}
      {workspace ? (
        <WorkspaceRow
          emptyMessage={t("agents.provisioning.activityWaiting")}
          name={workspace}
        />
      ) : (
        <p className="text-13 text-fg-muted">
          {isLifecycleActive(lifecycleStatus)
            ? t("agents.provisioning.activityWaiting")
            : t("agents.provisioning.none")}
        </p>
      )}
      {workspaceError ? (
        <p className="text-13 text-danger-text">{workspaceError}</p>
      ) : null}
      <WorkspaceSources
        emptyMessage={t("agents.provisioning.workspaceSourcesEmpty")}
        sources={sources}
        title={t("agents.provisioning.workspaceSources")}
      />
    </div>
  );
}

function ServiceRuntimeView({
  error,
  expectsService,
  service,
}: {
  error: string;
  expectsService: boolean;
  service: string;
}): React.ReactElement {
  const t = useAgentsT();

  return (
    <div className="flex flex-col gap-4">
      {error ? <p className="text-13 text-danger-text">{error}</p> : null}
      {service ? (
        <>
          <ServiceRow
            emptyMessage={t("agents.provisioning.activityWaitingService")}
            name={service}
          />
          <ServiceLogs name={service} title={t("agents.provisioning.serviceLogs")} />
        </>
      ) : (
        <p className="text-13 text-fg-muted">
          {expectsService
            ? t("agents.provisioning.activityWaitingService")
            : t("agents.provisioning.none")}
        </p>
      )}
    </div>
  );
}

function useProvisionRuntime(workspace: string): {
  error: string;
  workspaceStatus: WorkspaceStatusResult["status"];
} {
  const workspaceStatusResult = useWorkspaceStatus(workspace);
  const workspaceStatus = workspaceStatusResult.status;
  const error = workspaceStatusResult.error?.message ?? "";

  return { error, workspaceStatus };
}

function isLifecycleActive(status: string | null | undefined): boolean {
  return ["PROVISIONING", "DEPROVISIONING"].includes((status ?? "").toUpperCase());
}
