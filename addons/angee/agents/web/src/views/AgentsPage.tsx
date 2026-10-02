import * as React from "react";
import type {
  Row,
} from "@angee/metadata";
import { useOne, type BaseRecord, type HttpError, } from "@refinedev/core";
import {
  Card, CardContent, Column, ResourceList, Field, Form, Group, List, type RecordTabDescriptor } from "@angee/ui";
import {
  useModelMetadata,
} from "@angee/metadata";
import {
  refineFieldsFromPaths,
} from "@angee/refine";
import {
  textRoleVariants } from "@angee/ui";
import {
  refineResourceName,
} from "@angee/metadata";
import { usePrincipalAccessRecordTab } from "@angee/iam";

import { useAgentsT } from "../i18n";
import { AGENT_LIFECYCLE_FIELDS, AGENT_MODEL as MODEL, useAgentLifecycleActions } from "./agent-actions";
import { booleanField, stringField } from "./agent-record";
import { AgentChat } from "./AgentChat";
import { AgentProvisioning } from "./AgentProvisioning";
import { type AgentChatView } from "../documents";

// AgentChat provides ACP by default and admits runtime-owned transports through its slot.
// (`sqid` is not a GraphQL field — the agent's public id is carried by `id` for
// the view envelope; see below.)
const CHAT_FIELDS = ["id", "can_chat", "runtime_class"] as const;

function canDeleteAgent(record: Row): boolean {
  return booleanField(record, "can_delete");
}

/**
 * The agent detail's Chat tab. AgentChat resolves the composed runtime surface.
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
    resource: refineResourceName(resource),
    id: agentId,
    dataProviderName: resource?.schemaName,
    meta: { fields },
    queryOptions: {
      enabled: Boolean(agentId) && resource !== null,
    },
  });
  const record = (run.result as Row | undefined) ?? null;
  const runtimeClass = stringField(record, "runtime_class");
  if (!booleanField(record, "can_chat")) {
    return (
      <Card>
        <CardContent>
          <p className={textRoleVariants({ role: "meta" })}>{t("agent.chatUnavailable")}</p>
        </CardContent>
      </Card>
    );
  }
  const view: AgentChatView = { kind: "record", type: "agents/agent", sqid: agentId };
  return <AgentChat agentId={agentId} view={view} runtimeClass={runtimeClass} />;
}

type RowRecord = BaseRecord & Row;

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
// reusable blueprint, so only the Agents detail carries the lifecycle actions and
// the Service/Workspace/Chat record tabs beside the Overview form.
function AgentResourceListPage({
  isTemplate,
}: {
  isTemplate: boolean;
}): React.ReactElement {
  const labels = useAgentLabels();
  const accessTab = usePrincipalAccessRecordTab();
  const lifecycleActions = useAgentLifecycleActions();
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
        accessTab,
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
          : ["lifecycle", "runtime_status", "workspace", "service", ...AGENT_LIFECYCLE_FIELDS, "assignment_subject"]
      }
    >
      <List resource={MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="updated_at" />
      </List>
      <Form resource={MODEL} deleteVisibleWhen={isTemplate ? undefined : canDeleteAgent}>
        {isTemplate ? null : lifecycleActions}
        <Field name="name" title />
        <Field name="lifecycle" widget="statusbar" status readOnly />
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
          <Field name="inference_credential" />
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
