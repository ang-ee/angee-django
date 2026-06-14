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
import { runActionResult, useAuthoredMutation } from "@angee/sdk";

import {
  REFRESH_SOURCE_MUTATION,
  type IdVariables,
  type RefreshSourceData,
} from "../documents";

const MODEL = "integrate.Source";

const sourceList = (
  <List model={MODEL} pageSize={50}>
    <Column field="kind" />
    <Column field="ref" />
    <Column field="path" />
    <Column field="lastSyncedAt" />
  </List>
);

/**
 * Sources: ref+path pointers into a repository. The form binds a repository
 * (fixed at create) and its kind/ref/path; `refresh` re-reads the pointer from
 * the host.
 */
export function SourcesPage(): React.ReactElement {
  const [refreshSource] = useAuthoredMutation<RefreshSourceData, IdVariables>(
    REFRESH_SOURCE_MUTATION,
  );
  const refresh = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await refreshSource({ id: ctx.record.id });
      ctx.refresh();
      return runActionResult(result?.refreshSource);
    },
    [refreshSource],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {sourceList}
      <Form model={MODEL}>
        {/* The repository is fixed at create; the patch input omits it. */}
        <Field name="repository" createOnly />
        <Group label="Pointer" columns={2}>
          <Field name="kind" />
          <Field name="ref" />
        </Group>
        <Field name="path" />
        <Field name="lastSyncedAt" readOnly />
        <Action id="refresh" label="Refresh" icon="refresh" run={refresh} />
      </Form>
    </DataPage>
  );
}
