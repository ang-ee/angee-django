import * as React from "react";
import { Column, DataPage, Field, Form, Group, List } from "@angee/base";

import { useAgentsT } from "../i18n";
import { AgentProvisioning } from "./AgentProvisioning";

const MODEL = "agents.Agent";

// One model, two list tabs: the server-side ``isTemplate`` filter is the only
// difference between Agents and Templates, and a create on either tab defaults
// ``isTemplate`` to match. A real agent renders into the operator; a template is a
// reusable blueprint, so only the Agents tab carries the provisioning panel. The
// translated group label is passed in because `useAgentsT` must be called at a
// component's render top level.
function agentDataPage(isTemplate: boolean, modelTemplatesLabel: string): React.ReactElement {
  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      filter={{ isTemplate: { exact: isTemplate } }}
      createDefaults={{ isTemplate }}
      recordExtras={
        isTemplate
          ? undefined
          : ({ recordId, reload }) => (
              <AgentProvisioning agentId={recordId} onChanged={reload} />
            )
      }
    >
      <List model={MODEL} pageSize={50}>
        <Column field="name" />
        <Column field="status" widget="statusBadge" />
        <Column field="updatedAt" />
      </List>
      <Form model={MODEL}>
        <Field name="name" title />
        <Field name="description" />
        <Field name="instructions" />
        <Field name="isTemplate" />
        <Group label={modelTemplatesLabel} columns={2}>
          <Field name="model" />
          <Field name="owner" createOnly />
          <Field name="serviceTemplate" />
          <Field name="workspaceTemplate" />
        </Group>
        <Field name="serviceInputs" widget="json" />
        <Field name="workspaceInputs" widget="json" />
        <Field name="status" widget="statusbar" />
      </Form>
    </DataPage>
  );
}

export function AgentsPage(): React.ReactElement {
  const t = useAgentsT();
  return agentDataPage(false, t("agents.agent.modelTemplates"));
}

export function TemplatesPage(): React.ReactElement {
  const t = useAgentsT();
  return agentDataPage(true, t("agents.agent.modelTemplates"));
}
