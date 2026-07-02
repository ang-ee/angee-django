import * as React from "react";
import type {
  Row,
} from "@angee/metadata";
import { useOne, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  Action, Button, Card, CardContent, Column, ResourceList, Field, Form, Glyph, Group, List, errorMessage, useRecordActionMutation, useToast, type RecordToolbarContext, type RecordTabDescriptor } from "@angee/ui";
import {
  refineInvalidationParams,
  resourceInvalidationTargets,
  useModelMetadata,
  useSchemaFieldMetadata,
} from "@angee/metadata";
import {
  refineFieldsFromPaths,
  useActionMutation,
} from "@angee/refine";
import {
  textRoleVariants } from "@angee/ui";
import {
  refineResourceName,
} from "@angee/metadata";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useAgentsT } from "../i18n";
import { agentRuntime, booleanField, stringField } from "./agent-record";
import { AgentChat } from "./AgentChat";
import { AgentProvisioning } from "./AgentProvisioning";
import { type AgentChatView } from "../documents";

const MODEL = "agents.Agent";

// Just what the chat gate needs: a running, service-backed agent gets the chat panel.
// (`sqid` is not a GraphQL field — the agent's public id is carried by `id` for
// the view envelope; see below.)
const CHAT_FIELDS = ["id", "runtime_status", "service"] as const;

function canProvisionAgent(record: Row | null): boolean {
  return booleanField(record, "can_provision");
}

function canDeprovisionAgent(record: Row | null): boolean {
  return booleanField(record, "can_deprovision");
}

function canDeleteAgent(record: Row): boolean {
  return booleanField(record, "can_delete");
}

/**
 * The agent detail's Chat tab. Once the agent is RUNNING and has a rendered
 * `service` (the routed WebSocket the browser connects to) it shows the live ACP
 * chat; otherwise a hint to provision first. The view envelope tells the agent the
 * user is looking at this agent record.
 */
function AgentChatPanel({ agentId }: { agentId: string }): React.ReactElement {
  const t = useAgentsT();
  const metadata = useModelMetadata(MODEL);
  const resource = metadata?.resource ?? null;
  const fields = React.useMemo(
    () => refineFieldsFromPaths([...CHAT_FIELDS]),
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
  const record = (run.result as Row | undefined) ?? null;
  const running =
    agentRuntime(record) === "RUNNING" && stringField(record, "service") !== "";
  if (!running) {
    return (
      <Card>
        <CardContent>
          <p className={textRoleVariants({ role: "meta" })}>{t("agent.chatUnavailable")}</p>
        </CardContent>
      </Card>
    );
  }
  const view: AgentChatView = { kind: "record", type: "agents/agent", sqid: agentId };
  return <AgentChat agentId={agentId} view={view} />;
}

type RowRecord = BaseRecord & Row;

function AgentProvisionToolbarAction({
  record,
  recordId,
  reload,
}: RecordToolbarContext): React.ReactElement | null {
  const t = useAgentsT();
  const toast = useToast();
  const schemaMetadata = useSchemaFieldMetadata();
  const invalidates = React.useMemo(
    () =>
      resourceInvalidationTargets(schemaMetadata, [MODEL]).map(
        refineInvalidationParams,
      ),
    [schemaMetadata],
  );
  const [optimisticProvisioning, setOptimisticProvisioning] = React.useState(false);
  const [provisionAgent, provisionState] = useActionMutation<ActionFieldName>(
    "provision_agent",
    { invalidates },
  );
  const canProvision = canProvisionAgent(record);

  React.useEffect(() => {
    if (canProvision) setOptimisticProvisioning(false);
  }, [canProvision]);

  if (!recordId) return null;
  // Keep the button mounted in its loading state while the mutation is in flight.
  if (!provisionState.fetching && (optimisticProvisioning || !canProvision)) {
    return null;
  }

  const handleProvision = async (): Promise<void> => {
    setOptimisticProvisioning(true);
    try {
      const message = await provisionAgent(recordId);
      reload();
      if (message) toast.success({ title: message });
    } catch (caught) {
      setOptimisticProvisioning(false);
      toast.danger({
        title: t("provisioning.provisionFailed"),
        description: errorMessage(caught, t("provisioning.actionFailed")),
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
      {t("provisioning.provision")}
    </Button>
  );
}

// Translated copy resolved at a component's render top level (where hooks belong)
// and threaded into the plain `agentResourceListPage` builder below.
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
    modelTemplates: t("agent.modelTemplates"),
    provisioningInputs: t("agent.provisioningInputs"),
    tabService: t("agent.tabService"),
    tabWorkspace: t("agent.tabWorkspace"),
    tabChat: t("agent.tabChat"),
  };
}

// One model, two list tabs: the server-side ``is_template`` filter is the only
// difference between Agents and Templates, and a create on either tab defaults
// ``is_template`` to match. A real agent renders into the operator; a template is a
// reusable blueprint, so only the Agents detail carries the Provision/Chat record
// tabs beside the Overview form.
function AgentResourceListPage({
  isTemplate,
}: {
  isTemplate: boolean;
}): React.ReactElement {
  const labels = useAgentLabels();
  const t = useAgentsT();
  const [deprovision] = useRecordActionMutation<ActionFieldName>("deprovision_agent", {
    invalidateModels: [MODEL],
    missingRecordMessage: t("provisioning.saveFirst"),
  });
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
    <ResourceList
      resource={MODEL}
      placement="inline"
      routed
      baseFilter={{ is_template: { exact: isTemplate } }}
      createDefaults={{ is_template: isTemplate }}
      recordTabs={recordTabs}
      returning={
        isTemplate
          ? undefined
          : [
              "lifecycle",
              "runtime_status",
              "workspace",
              "service",
              "can_provision",
              "can_deprovision",
              "can_delete",
            ]
      }
    >
      <List resource={MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="updated_at" />
      </List>
      <Form
        resource={MODEL}
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
            label={t("provisioning.deprovision")}
            icon="trash"
            danger
            confirm={{
              title: t("provisioning.confirmTitle"),
              body: t("provisioning.confirmBody"),
              danger: true,
            }}
            visibleWhen={canDeprovisionAgent}
            run={deprovision}
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
          <Field name="runtime_class" />
          <Field name="workspace_template" />
        </Group>
        <Group label={labels.provisioningInputs} columns={2}>
          <Field name="service_inputs" widget="json" />
          <Field name="workspace_inputs" widget="json" />
        </Group>
        <Group columns={1}>
          <Field name="is_template" />
        </Group>
      </Form>
    </ResourceList>
  );
}

export function AgentsPage(): React.ReactElement {
  return <AgentResourceListPage isTemplate={false} />;
}

export function TemplatesPage(): React.ReactElement {
  return <AgentResourceListPage isTemplate />;
}
