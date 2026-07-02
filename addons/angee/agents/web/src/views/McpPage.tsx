import * as React from "react";
import { Column, ResourceList, Facet, Field, Form, Group, List, useEnumOptions } from "@angee/ui";

import { useAgentsT } from "../i18n";

const SERVER_MODEL = "agents.MCPServer";
const TOOL_MODEL = "agents.MCPTool";

export function McpServersPage(): React.ReactElement {
  const t = useAgentsT();
  const placementOptions = useEnumOptions(SERVER_MODEL, "placement");
  const transportOptions = useEnumOptions(SERVER_MODEL, "transport");
  return (
    <ResourceList resource={SERVER_MODEL} placement="inline" routed>
      <List resource={SERVER_MODEL}>
        <Column field="name" />
        <Column field="placement" />
        <Column field="transport" />
        <Column field="url" />
      </List>
      <Form resource={SERVER_MODEL}>
        <Field name="name" title />
        <Field name="description" />
        <Group label={t("mcp.endpoint")} columns={2}>
          <Field name="placement" widget="select" options={placementOptions} createOnly />
          <Field name="transport" widget="select" options={transportOptions} createOnly />
          <Field name="url" />
          <Field name="credential" />
        </Group>
        <Field name="config" widget="json" />
      </Form>
    </ResourceList>
  );
}

export function McpToolsPage(): React.ReactElement {
  return (
    <ResourceList resource={TOOL_MODEL} placement="inline" routed>
      <List resource={TOOL_MODEL}>
        <Facet field="server" label="Server" labelField="name" />
        <Column field="name" />
        <Column field="enabled" />
        <Column field="updated_at" />
      </List>
      <Form resource={TOOL_MODEL}>
        <Field name="server" createOnly />
        <Field name="name" title />
        <Field name="description" />
        <Field name="input_schema" widget="json" />
        <Field name="enabled" />
      </Form>
    </ResourceList>
  );
}
