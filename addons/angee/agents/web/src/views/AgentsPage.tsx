import * as React from "react";
import {
  Action,
  Button,
  Card,
  CardContent,
  Column,
  DataPage,
  Field,
  Form,
  Glyph,
  Group,
  List,
  useToast,
  type RecordToolbarContext,
  type RecordTabDescriptor,
} from "@angee/base";
import {
  fromRelayGlobalId,
  useAuthoredMutation,
  useModelInvalidation,
  useResourceRecord,
  type Row,
} from "@angee/sdk";

import { useAgentsT } from "../i18n";
import { AgentChat } from "./AgentChat";
import { AgentProvisioning } from "./AgentProvisioning";
import {
  DEPROVISION_AGENT_MUTATION,
  PROVISION_AGENT_MUTATION,
  type ActionResultData,
  type AgentChatView,
  type DeprovisionAgentData,
  type IdVariables,
  type ProvisionAgentData,
} from "../documents";

const MODEL = "agents.Agent";

// Just what the chat gate needs: a running, service-backed agent gets the chat panel.
// (`sqid` is not a GraphQL field — the agent's public id is recovered from the relay
// `id` for the view envelope; see below.)
const CHAT_FIELDS = ["id", "runtimeStatus", "service"] as const;

/** Read a string field off the boundary record (`Record<string, unknown>`), or "". */
function stringField(record: Row | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

// The agent carries two state axes, each reading as its UPPERCASE enum name: `lifecycle`
// is the provision journey (DRAFT→PROVISIONING→READY→DEPROVISIONING→DEPROVISIONED) and
// `runtimeStatus` the observed run state (STOPPED/RUNNING/ERROR/WARNING — the colored dot).
function agentLifecycle(record: Row | null): string {
  return stringField(record, "lifecycle").toUpperCase();
}

function agentRuntime(record: Row | null): string {
  return stringField(record, "runtimeStatus").toUpperCase();
}

function canProvisionAgent(record: Row | null): boolean {
  // Provision a fresh or torn-down agent, or retry one whose last operation errored.
  return (
    agentRuntime(record) === "ERROR" ||
    ["DRAFT", "DEPROVISIONED"].includes(agentLifecycle(record))
  );
}

function canDeprovisionAgent(record: Row | null): boolean {
  return (
    ["PROVISIONING", "READY", "DEPROVISIONING"].includes(agentLifecycle(record)) ||
    stringField(record, "workspace") !== "" ||
    stringField(record, "service") !== ""
  );
}

function canDeleteAgent(record: Row): boolean {
  return (
    !["PROVISIONING", "READY", "DEPROVISIONING"].includes(agentLifecycle(record)) &&
    stringField(record, "workspace") === "" &&
    stringField(record, "service") === ""
  );
}

function actionMessage(
  result: ActionResultData | undefined,
  fallback: string,
): string {
  if (!result?.ok) throw new Error(result?.message ?? fallback);
  return result.message ?? "";
}

/**
 * The agent detail's Chat tab. Once the agent is RUNNING and has a rendered
 * `service` (the routed WebSocket the browser connects to) it shows the live ACP
 * chat; otherwise a hint to provision first. The view envelope tells the agent the
 * user is looking at this agent record.
 */
function AgentChatPanel({ agentId }: { agentId: string }): React.ReactElement {
  const t = useAgentsT();
  const { record } = useResourceRecord(MODEL, agentId, { fields: [...CHAT_FIELDS] });
  const running =
    agentRuntime(record) === "RUNNING" && stringField(record, "service") !== "";
  if (!running) {
    return (
      <Card>
        <CardContent>
          <p className="text-13 text-fg-muted">{t("agents.agent.chatUnavailable")}</p>
        </CardContent>
      </Card>
    );
  }
  const view: AgentChatView = { kind: "record", type: "agents/agent", sqid: fromRelayGlobalId(agentId) };
  return <AgentChat agentId={agentId} view={view} />;
}

function AgentProvisionToolbarAction({
  patchRecord,
  record,
  recordId,
  reload,
}: RecordToolbarContext): React.ReactElement | null {
  const t = useAgentsT();
  const toast = useToast();
  const invalidateAgent = useModelInvalidation(MODEL);
  const [optimisticProvisioning, setOptimisticProvisioning] = React.useState(false);
  const [provisionAgent, provisionState] = useAuthoredMutation<ProvisionAgentData, IdVariables>(
    PROVISION_AGENT_MUTATION,
  );
  const lifecycle = agentLifecycle(record);

  React.useEffect(() => {
    if (lifecycle !== "PROVISIONING") setOptimisticProvisioning(false);
  }, [lifecycle]);

  if (!recordId) return null;
  // Keep the button mounted in its loading state while the mutation is in flight: the
  // optimistic status patch has already flipped the record out of a provisionable state,
  // so gating only on canProvisionAgent/optimistic would unmount it before the spinner shows.
  if (!provisionState.fetching && (optimisticProvisioning || !canProvisionAgent(record))) {
    return null;
  }

  const handleProvision = async (): Promise<void> => {
    const previousLifecycle = stringField(record, "lifecycle");
    setOptimisticProvisioning(true);
    patchRecord({ lifecycle: "PROVISIONING" });
    try {
      const message = actionMessage(
        (await provisionAgent({ id: recordId }))?.provisionAgent,
        t("agents.provisioning.provisionFailed"),
      );
      invalidateAgent();
      reload();
      if (message) toast.success({ title: message });
    } catch (caught) {
      setOptimisticProvisioning(false);
      if (previousLifecycle) patchRecord({ lifecycle: previousLifecycle });
      toast.danger({
        title: t("agents.provisioning.provisionFailed"),
        description:
          caught instanceof Error
            ? caught.message
            : t("agents.provisioning.actionFailed"),
      });
    }
  };

  return (
    <Button
      type="button"
      variant="primary"
      size="sm"
      loading={provisionState.fetching}
      onClick={() => void handleProvision()}
    >
      <Glyph name="plus" />
      {t("agents.provisioning.provision")}
    </Button>
  );
}

// Translated copy resolved at a component's render top level (where hooks belong)
// and threaded into the plain `agentDataPage` builder below.
interface AgentLabels {
  modelTemplates: string;
  provisioningInputs: string;
  tabService: string;
  tabWorkspace: string;
  tabChat: string;
}

function useAgentLabels(): AgentLabels {
  const t = useAgentsT();
  return {
    modelTemplates: t("agents.agent.modelTemplates"),
    provisioningInputs: t("agents.agent.provisioningInputs"),
    tabService: t("agents.agent.tabService"),
    tabWorkspace: t("agents.agent.tabWorkspace"),
    tabChat: t("agents.agent.tabChat"),
  };
}

// One model, two list tabs: the server-side ``isTemplate`` filter is the only
// difference between Agents and Templates, and a create on either tab defaults
// ``isTemplate`` to match. A real agent renders into the operator; a template is a
// reusable blueprint, so only the Agents detail carries the Provision/Chat record
// tabs beside the Overview form.
function AgentDataPage({
  isTemplate,
}: {
  isTemplate: boolean;
}): React.ReactElement {
  const labels = useAgentLabels();
  const t = useAgentsT();
  const invalidateAgent = useModelInvalidation(MODEL);
  const [deprovisionAgent] = useAuthoredMutation<DeprovisionAgentData, IdVariables>(
    DEPROVISION_AGENT_MUTATION,
  );
  const recordTabs: readonly RecordTabDescriptor[] | undefined = isTemplate
    ? undefined
    : [
        {
          id: "service",
          label: labels.tabService,
          render: ({ recordId }) => <AgentProvisioning agentId={recordId} pane="service" />,
        },
        {
          id: "workspace",
          label: labels.tabWorkspace,
          render: ({ recordId }) => <AgentProvisioning agentId={recordId} pane="workspace" />,
        },
        {
          id: "chat",
          label: labels.tabChat,
          render: ({ recordId }) => <AgentChatPanel agentId={recordId} />,
        },
      ];
  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      filter={{ isTemplate: { exact: isTemplate } }}
      createDefaults={{ isTemplate }}
      recordTabs={recordTabs}
      returning={isTemplate ? undefined : ["lifecycle", "runtimeStatus", "workspace", "service"]}
    >
      <List model={MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtimeStatus" widget="colorDot" />
        <Column field="updatedAt" />
      </List>
      <Form
        model={MODEL}
        deleteVisibleWhen={isTemplate ? undefined : canDeleteAgent}
        toolbarStart={
          isTemplate
            ? undefined
            : (context) => <AgentProvisionToolbarAction {...context} />
        }
      >
        {!isTemplate ? (
          <Action
            id="deprovision"
            label={t("agents.provisioning.deprovision")}
            icon="trash"
            danger
            confirm={{
              title: t("agents.provisioning.confirmTitle"),
              body: t("agents.provisioning.confirmBody"),
              danger: true,
            }}
            visibleWhen={canDeprovisionAgent}
            run={async ({ record, refresh }) => {
              const id = stringField(record, "id");
              if (!id) throw new Error(t("agents.provisioning.saveFirst"));
              const message = actionMessage(
                (await deprovisionAgent({ id }))?.deprovisionAgent,
                t("agents.provisioning.deprovisionFailed"),
              );
              invalidateAgent();
              refresh();
              return message;
            }}
          />
        ) : null}
        <Field name="name" title />
        <Field name="lifecycle" widget="statusbar" />
        {/* Description then instructions lead the Overview tab as full-width
            textareas; `body={false}` keeps `description` a normal field rather than
            the form's auto-detected body. */}
        <Group columns={1}>
          <Field name="description" widget="textarea" body={false} />
        </Group>
        <Group columns={1}>
          <Field name="instructions" widget="textarea" body={false} />
        </Group>
        <Group label={labels.modelTemplates} columns={2}>
          <Field name="model" />
          <Field name="owner" createOnly />
          <Field name="serviceTemplate" />
          <Field name="workspaceTemplate" />
        </Group>
        <Group label={labels.provisioningInputs} columns={2}>
          <Field name="serviceInputs" widget="json" />
          <Field name="workspaceInputs" widget="json" />
        </Group>
        <Group columns={1}>
          <Field name="isTemplate" />
        </Group>
      </Form>
    </DataPage>
  );
}

export function AgentsPage(): React.ReactElement {
  return <AgentDataPage isTemplate={false} />;
}

export function TemplatesPage(): React.ReactElement {
  return <AgentDataPage isTemplate />;
}
