import * as React from "react";
import { runActionResult, useAuthoredMutation } from "@angee/refine";
import { Action, Column, Facet, Field, Form, Group, List, ResourceList, useEnumOptions, useImplPrefill, useRecordAction, useRecordActionMutation } from "@angee/ui";
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
        <Facet field="vendor" label={t("col.vendor")} labelField="display_name" />
        <Column field="display_name" />
        <Column field="backend_class" header={t("vcs.backendClass")} />
        <Column
          field="lifecycle"
          header={t("col.lifecycle")}
          widget="statusBadge"
        />
        <Column
          field="runtime_status"
          header={t("col.runtimeStatus")}
          widget="colorDot"
        />
        <Column field="sync_stage" />
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
        <Field name="lifecycle" widget="statusbar" readOnly />
        <Field name="runtime_status" readOnly />
        <Field name="config" widget="json" />
        <Group label={t("bridge.group.sync")} columns={2}>
          <Field name="is_syncing" readOnly />
          <Field name="sync_stage" readOnly />
          <Field name="sync_error" readOnly />
          <Field name="sync_progress" widget="json" readOnly />
          <Field name="last_sync_summary" widget="json" readOnly />
          <Field name="last_sync_items" readOnly />
          <Field name="last_sync_completed_at" readOnly />
        </Group>
        <Field name="last_sync_status" readOnly />
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhookSecret" widget="text" kind="string" createOnly />
        <Action id="sync" label={t("action.syncNow")} icon="refresh" run={sync} />
        <Action id="discover" label={t("vcs.discover")} run={discoverAll} />
      </Form>
    </ResourceList>
  );
}
