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

import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Source";

const sourceList = (
  <List model={MODEL} list={GroupListView}>
    <Facet field="repository" label="Repository" labelField="name" />
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
  const t = useIntegrateT();
  const [refresh] = useRecordActionMutation<ActionFieldName>("refreshSource");

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {sourceList}
      <Form model={MODEL}>
        {/* The repository is fixed at create; the patch input omits it. */}
        <Field name="repository" createOnly />
        <Group label={t("integrate.sources.pointer")} columns={2}>
          <Field name="kind" />
          <Field name="ref" />
        </Group>
        <Field name="path" />
        <Field name="lastSyncedAt" readOnly />
        <Action id="refresh" label={t("integrate.action.refresh")} icon="refresh" run={refresh} />
      </Form>
    </DataPage>
  );
}
