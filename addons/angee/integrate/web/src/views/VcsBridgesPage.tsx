import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Facet,
  Field,
  Form,
  GroupListView,
  List,
  useEnumOptions,
  useImplPrefill,
  useRecordAction,
  useRecordActionMutation,
} from "@angee/base";
import { runActionResult, useAuthoredMutation } from "@angee/sdk";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";
import { IntegrateDiscoverRepositories } from "../documents";

const MODEL = "integrate.VcsBridge";

/**
 * VCS bridges own repository discovery and source sync for one integration child row.
 */
export function VcsBridgesPage(): React.ReactElement {
  const t = useIntegrateT();
  const [sync] = useRecordActionMutation<ActionFieldName>("syncVcsBridge");
  const [discover] = useAuthoredMutation(IntegrateDiscoverRepositories);
  const backendClassOptions = useEnumOptions(MODEL, "backendClass");
  const backendClassPrefill = useImplPrefill(MODEL, "backendClass");

  const discoverRepositories = React.useCallback(
    async (id: string) => {
      const result = await discover({ vcsBridgeId: id, org: "" });
      return runActionResult(result?.discoverRepositories);
    },
    [discover],
  );
  const discoverAll = useRecordAction(discoverRepositories);

  return (
    <DataPage model={MODEL} placement="inline" routed>
      <List model={MODEL} list={GroupListView}>
        <Facet field="vendor" label="Vendor" labelField="displayName" />
        <Column field="displayName" />
        <Column field="backendClass" header={t("integrate.vcs.backendClass")} />
        <Column
          field="status"
          header={t("integrate.col.status")}
          widget="statusBadge"
        />
        <Column field="lastSyncCompletedAt" />
      </List>
      <Form model={MODEL}>
        <Field name="owner" />
        <Field name="vendor" />
        <Field
          name="backendClass"
          widget="select"
          options={backendClassOptions}
          prefill={backendClassPrefill}
        />
        <Field name="credential" />
        <Field name="status" widget="statusbar" />
        <Field name="config" widget="json" />
        <Field name="lastSyncStatus" readOnly />
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhookSecret" widget="text" kind="string" createOnly />
        <Action id="sync" label={t("integrate.action.syncNow")} icon="refresh" run={sync} />
        <Action id="discover" label={t("integrate.vcs.discover")} run={discoverAll} />
      </Form>
    </DataPage>
  );
}
