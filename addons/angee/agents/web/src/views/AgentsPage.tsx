import * as React from "react";
import { Column, DataPage, Field, Form, Group, List } from "@angee/base";

const MODEL = "agents.Agent";

// One model, two list tabs: the server-side ``isTemplate`` filter is the only
// difference between Agents and Templates, and a create on either tab defaults
// ``isTemplate`` to match.
function agentDataPage(isTemplate: boolean): React.ReactElement {
  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      filter={{ isTemplate: { exact: isTemplate } }}
      createDefaults={{ isTemplate }}
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
        <Group label="Model & operator templates" columns={2}>
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
  return agentDataPage(false);
}

export function TemplatesPage(): React.ReactElement {
  return agentDataPage(true);
}
