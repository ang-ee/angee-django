import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Facet,
  Field,
  Form,
  Group,
  GroupListView,
  List,
  useRecordActionMutation,
} from "@angee/base";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useAgentsT } from "../i18n";

// Skill sources are `integrate.Source` rows of kind "skill". The repository and its
// VCS bridge are set up in the integrate console; here a source points at a
// repo path, and Refresh (re-)discovers its skills.
const MODEL = "integrate.Source";
const SKILL_DEFAULTS = { kind: "skill" };

export function SourcesPage(): React.ReactElement {
  const t = useAgentsT();
  const [refresh] = useRecordActionMutation<ActionFieldName>("refreshSource");

  return (
    <DataPage
      model={MODEL}
      placement="inline"
      routed
      filter={{ kind: { exact: "skill" } }}
      createDefaults={SKILL_DEFAULTS}
    >
      <List model={MODEL} list={GroupListView} pageSize={50}>
        <Facet field="repository" label="Repository" labelField="name" />
        <Column field="path" />
        <Column field="ref" />
        <Column field="lastSyncedAt" />
      </List>
      <Form model={MODEL}>
        {/* repository + kind are fixed at create. `kind` is `createOnly` (not
            `readOnly`) so the `createDefaults` "skill" seed is actually submitted —
            `mutationData` drops readOnly fields — then locked read-only on edit. */}
        <Field name="repository" createOnly />
        <Group label={t("agents.sources.pointer")} columns={2}>
          <Field name="kind" createOnly />
          <Field name="ref" />
        </Group>
        <Field name="path" />
        <Field name="lastSyncedAt" readOnly />
        <Action id="refresh" label={t("agents.sources.refreshSkills")} icon="refresh" run={refresh} />
      </Form>
    </DataPage>
  );
}
