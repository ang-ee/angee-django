import * as React from "react";
import { Action, type ActionContext, Column, DataPage, Field, Form, Group, List } from "@angee/base";
import { useAuthoredMutation } from "@angee/sdk";

import { REFRESH_SOURCE_MUTATION, type IdVariables, type RefreshSourceData } from "../documents";

// Skill sources are `integrate.Source` rows of kind "skill". The repository and its
// VCS integration are set up in the integrate console; here a source points at a
// repo path, and Refresh (re-)discovers its skills.
const MODEL = "integrate.Source";

export function SourcesPage(): React.ReactElement {
  const [refreshSource] = useAuthoredMutation<RefreshSourceData, IdVariables>(REFRESH_SOURCE_MUTATION);
  const refresh = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await refreshSource({ id: ctx.record.id });
      ctx.refresh();
      return result?.refreshSource.message;
    },
    [refreshSource],
  );

  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      filter={{ kind: { exact: "skill" } }}
      createDefaults={{ kind: "skill" }}
    >
      <List model={MODEL} pageSize={50}>
        <Column field="path" />
        <Column field="ref" />
        <Column field="lastSyncedAt" />
      </List>
      <Form model={MODEL}>
        {/* The repository is fixed at create; the patch input omits it. `kind` is
            pinned to "skill" (createDefaults) so the source lands in this filtered list. */}
        <Field name="repository" createOnly />
        <Group label="Pointer" columns={2}>
          <Field name="kind" readOnly />
          <Field name="ref" />
        </Group>
        <Field name="path" />
        <Field name="lastSyncedAt" readOnly />
        <Action id="refresh" label="Refresh skills" icon="refresh" run={refresh} />
      </Form>
    </DataPage>
  );
}
