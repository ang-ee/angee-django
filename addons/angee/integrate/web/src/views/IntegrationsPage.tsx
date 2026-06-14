import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  Group,
  List,
  type ActionContext,
} from "@angee/base";
import { runActionResult, useAuthoredMutation, type Row } from "@angee/sdk";

import {
  SYNC_INTEGRATION_MUTATION,
  TEST_CONNECTION_MUTATION,
  type IdVariables,
  type SyncIntegrationData,
  type TestConnectionData,
} from "../documents";

const MODEL = "integrate.Integration";

const integrationList = (
  <List model={MODEL} pageSize={50}>
    <Column field="displayName" />
    <Column field="status" widget="statusBadge" />
    <Column field="lastUsedAt" />
  </List>
);

const isActive = (record: Row): boolean =>
  String(record.status ?? "").toUpperCase() === "ACTIVE";

/** Integrations landing: the first-class integrations, their health, and operations. */
export function IntegrationsPage(): React.ReactElement {
  const [syncIntegration] = useAuthoredMutation<SyncIntegrationData, IdVariables>(
    SYNC_INTEGRATION_MUTATION,
  );
  const [testConnection] = useAuthoredMutation<TestConnectionData, IdVariables>(
    TEST_CONNECTION_MUTATION,
  );

  const sync = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await syncIntegration({ id: ctx.record.id });
      ctx.refresh();
      return runActionResult(result?.syncIntegration);
    },
    [syncIntegration],
  );
  const test = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await testConnection({ id: ctx.record.id });
      return runActionResult(result?.testConnection);
    },
    [testConnection],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {integrationList}
      <Form model={MODEL}>
        <Field name="vendor" />
        <Field name="status" widget="statusbar" />
        <Group label="Authentication" columns={2}>
          <Field name="credential" />
          <Field name="account" />
          <Field name="owner" />
        </Group>
        <Field name="config" widget="json" />
        <Action id="sync" label="Sync now" icon="refresh" run={sync} />
        <Action id="test" label="Test connection" run={test} />
        <Action
          id="disable"
          label="Disable"
          danger
          set={{ status: "disabled" }}
          visibleWhen={isActive}
        />
        <Action
          id="activate"
          label="Activate"
          set={{ status: "active" }}
          visibleWhen={(record: Row) => !isActive(record)}
        />
      </Form>
    </DataPage>
  );
}
