import * as React from "react";
import { Action, Column, ResourceList, Facet, Field, Form, Group, List, useRecordActionMutation } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useAgentsT } from "../i18n";

// Skill sources are `integrate.Source` rows of kind "skill". The repository and its
// VCS bridge are set up in the integrate console; here a source points at a
// repo path, and Refresh (re-)discovers its skills.
const MODEL = "integrate.Source";
const SKILL_MODEL = "agents.Skill";
const SKILL_DEFAULTS = { kind: "skill" };

export function SourcesPage(): React.ReactElement {
  const t = useAgentsT();
  const [refresh] = useRecordActionMutation<ActionFieldName>("refresh_source", {
    invalidateModels: [SKILL_MODEL],
  });

  return (
    <ResourceList
      resource={MODEL}
      placement="inline"
      routed
      baseFilter={{ kind: { exact: "skill" } }}
      createDefaults={SKILL_DEFAULTS}
    >
      <List resource={MODEL} pageSize={50}>
        <Facet field="repository" label="Repository" labelField="name" />
        <Column field="path" />
        <Column field="ref" />
        <Column field="last_synced_at" />
      </List>
      <Form resource={MODEL}>
        {/* repository + kind are fixed at create. `kind` is `createOnly` (not
            `readOnly`) so the `createDefaults` "skill" seed is actually submitted —
            `mutationData` drops readOnly fields — then locked read-only on edit. */}
        <Field name="repository" createOnly />
        <Group label={t("sources.pointer")} columns={2}>
          <Field name="kind" createOnly />
          <Field name="ref" />
        </Group>
        <Field name="path" />
        <Field name="last_synced_at" readOnly />
        <Action id="refresh" label={t("sources.refreshSkills")} icon="refresh" run={refresh} />
      </Form>
    </ResourceList>
  );
}
