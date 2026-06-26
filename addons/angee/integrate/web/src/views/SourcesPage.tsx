import * as React from "react";
import {
  Action,
  Column,
  ResourceList,
  Facet,
  Field,
  Form,
  Group,
  List,
  useRecordActionMutation,
} from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Source";

const sourceList = (
  <List resource={MODEL}>
    <Facet field="repository" label="Repository" labelField="name" />
    <Column field="kind" />
    <Column field="ref" />
    <Column field="path" />
    <Column field="last_synced_at" />
  </List>
);

/**
 * Sources: ref+path pointers into a repository. The form binds a repository
 * (fixed at create) and its kind/ref/path; `refresh` re-reads the pointer from
 * the host.
 */
export function SourcesPage(): React.ReactElement {
  const t = useIntegrateT();
  const [refresh] = useRecordActionMutation<ActionFieldName>("refresh_source");

  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      {sourceList}
      <Form resource={MODEL}>
        {/* The repository is fixed at create; the patch input omits it. */}
        <Field name="repository" createOnly />
        <Group label={t("integrate.sources.pointer")} columns={2}>
          <Field name="kind" />
          <Field name="ref" />
        </Group>
        <Field name="path" />
        <Field name="last_synced_at" readOnly />
        <Action id="refresh" label={t("integrate.action.refresh")} icon="refresh" run={refresh} />
      </Form>
    </ResourceList>
  );
}
