import * as React from "react";
import {
  Card,
  CardContent,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  type RecordTabDescriptor,
} from "@angee/base";
import { fromRelayGlobalId, useResourceRecord, type Row } from "@angee/sdk";

import { useAgentsT } from "../i18n";
import { AgentChat } from "./AgentChat";
import { AgentProvisioning } from "./AgentProvisioning";
import type { AgentChatView } from "../documents";

const MODEL = "agents.Agent";

// Just what the chat gate needs: a running, service-backed agent gets the chat panel.
// (`sqid` is not a GraphQL field — the agent's public id is recovered from the relay
// `id` for the view envelope; see below.)
const CHAT_FIELDS = ["id", "status", "service"] as const;

/** Read a string field off the boundary record (`Record<string, unknown>`), or "". */
function stringField(record: Row | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
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
    stringField(record, "status") === "RUNNING" && stringField(record, "service") !== "";
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

// Translated copy resolved at a component's render top level (where hooks belong)
// and threaded into the plain `agentDataPage` builder below.
interface AgentLabels {
  modelTemplates: string;
  provisioningInputs: string;
  tabProvision: string;
  tabChat: string;
}

function useAgentLabels(): AgentLabels {
  const t = useAgentsT();
  return {
    modelTemplates: t("agents.agent.modelTemplates"),
    provisioningInputs: t("agents.agent.provisioningInputs"),
    tabProvision: t("agents.agent.tabProvision"),
    tabChat: t("agents.agent.tabChat"),
  };
}

// One model, two list tabs: the server-side ``isTemplate`` filter is the only
// difference between Agents and Templates, and a create on either tab defaults
// ``isTemplate`` to match. A real agent renders into the operator; a template is a
// reusable blueprint, so only the Agents detail carries the Provision/Chat record
// tabs beside the Overview form.
function agentDataPage(isTemplate: boolean, labels: AgentLabels): React.ReactElement {
  const recordTabs: readonly RecordTabDescriptor[] | undefined = isTemplate
    ? undefined
    : [
        {
          id: "provision",
          label: labels.tabProvision,
          render: ({ recordId, reload }) => (
            <AgentProvisioning agentId={recordId} onChanged={reload} />
          ),
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
    >
      <List model={MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="status" widget="statusBadge" />
        <Column field="updatedAt" />
      </List>
      <Form model={MODEL}>
        <Field name="name" title />
        <Field name="status" widget="statusbar" />
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
  return agentDataPage(false, useAgentLabels());
}

export function TemplatesPage(): React.ReactElement {
  return agentDataPage(true, useAgentLabels());
}
