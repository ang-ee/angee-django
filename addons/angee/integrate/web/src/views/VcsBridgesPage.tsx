import * as React from "react";
import { runActionResult, useAuthoredMutation } from "@angee/refine";
import { Action, Column, Facet, Field, Form, List, ResourceList, useEnumOptions, useImplPrefill, useRecordAction, useRecordActionMutation } from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";
import { IntegrateDiscoverRepositories } from "../documents";

const MODEL = "integrate.VcsBridge";

/**
 * VCS bridges own repository discovery and source sync for one integration child row.
 */
export function VcsBridgesPage(): React.ReactElement {
  const t = useIntegrateT();
  const [sync] = useRecordActionMutation<ActionFieldName>("sync_vcs_bridge");
  const [discover] = useAuthoredMutation(IntegrateDiscoverRepositories);
  const backendClassOptions = useEnumOptions(MODEL, "backend_class");
  const backendClassPrefill = useImplPrefill(MODEL, "backend_class");

  const discoverRepositories = React.useCallback(
    async (id: string) => {
      const result = await discover({ vcsBridgeId: id, org: "" });
      return runActionResult(result?.discover_repositories);
    },
    [discover],
  );
  const discoverAll = useRecordAction(discoverRepositories);

  return (
    <ResourceList resource={MODEL} placement="inline" routed>
      <List resource={MODEL}>
        <Facet field="vendor" label="Vendor" labelField="display_name" />
        <Column field="display_name" />
        <Column field="backend_class" header={t("vcs.backendClass")} />
        <Column
          field="status"
          header={t("col.status")}
          widget="statusBadge"
        />
        <Column field="last_sync_completed_at" />
      </List>
      <Form resource={MODEL}>
        <Field name="owner" />
        <Field name="vendor" />
        <Field
          name="backend_class"
          widget="select"
          options={backendClassOptions}
          prefill={backendClassPrefill}
        />
        <Field name="credential" />
        <Field name="status" widget="statusbar" />
        <Field name="config" widget="json" />
        <Field name="last_sync_status" readOnly />
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhookSecret" widget="text" kind="string" createOnly />
        <Action id="sync" label={t("action.syncNow")} icon="refresh" run={sync} />
        <Action id="discover" label={t("vcs.discover")} run={discoverAll} />
      </Form>
    </ResourceList>
  );
}
