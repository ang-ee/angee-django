import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  List,
  useEnumOptions,
  useImplPrefill,
  type ActionContext,
} from "@angee/base";
import { runActionResult, useActionMutation, useAuthoredMutation } from "@angee/sdk";
import type { ActionFieldName } from "@angee/gql/console/actions";

import { useIntegrateT } from "../i18n";
import { IntegrateDiscoverRepositories } from "../documents";

const MODEL = "integrate.VcsBridge";

/**
 * VCS bridges own repository discovery and source sync for one integration child row.
 */
export function VcsBridgesPage(): React.ReactElement {
  const t = useIntegrateT();
  const [syncVcs] = useActionMutation<ActionFieldName>("syncVcsBridge");
  const [discover] = useAuthoredMutation(IntegrateDiscoverRepositories);
  const backendClassOptions = useEnumOptions(MODEL, "backendClass");
  const backendClassPrefill = useImplPrefill(MODEL, "backendClass");

  const sync = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const message = await syncVcs(ctx.record.id);
      ctx.refresh();
      return message;
    },
    [syncVcs],
  );
  const discoverAll = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await discover({ vcsBridgeId: ctx.record.id, org: "" });
      ctx.refresh();
      return runActionResult(result?.discoverRepositories);
    },
    [discover],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      <List model={MODEL}>
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
