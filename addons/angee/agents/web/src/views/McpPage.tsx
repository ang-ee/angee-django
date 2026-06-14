import * as React from "react";
import { Column, DataPage, Field, Form, Group, List } from "@angee/base";

import { useAgentsT } from "../i18n";

const SERVER_MODEL = "agents.MCPServer";
const TOOL_MODEL = "agents.MCPTool";

export function McpServersPage(): React.ReactElement {
  const t = useAgentsT();
  return (
    <DataPage model={SERVER_MODEL} placement="inline" routed>
      <List model={SERVER_MODEL}>
        <Column field="name" />
        <Column field="placement" />
        <Column field="transport" />
        <Column field="url" />
      </List>
      <Form model={SERVER_MODEL}>
        <Field name="name" title />
        <Field name="description" />
        <Group label={t("agents.mcp.endpoint")} columns={2}>
          <Field name="placement" />
          <Field name="transport" />
          <Field name="url" />
          <Field name="credential" />
        </Group>
        <Field name="config" widget="json" />
      </Form>
    </DataPage>
  );
}

export function McpToolsPage(): React.ReactElement {
  return (
    <DataPage model={TOOL_MODEL} placement="inline" routed>
      <List model={TOOL_MODEL}>
        <Column field="name" />
        <Column field="enabled" />
        <Column field="updatedAt" />
      </List>
      <Form model={TOOL_MODEL}>
        <Field name="server" createOnly />
        <Field name="name" title />
        <Field name="description" />
        <Field name="inputSchema" widget="json" />
        <Field name="enabled" />
      </Form>
    </DataPage>
  );
}
