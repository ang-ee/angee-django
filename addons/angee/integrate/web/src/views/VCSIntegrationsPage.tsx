import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  List,
  type ActionContext,
  useEnumOptions,
} from "@angee/base";
import { runActionResult, useAuthoredMutation } from "@angee/sdk";

import { useIntegrateT } from "../i18n";
import {
  DISCOVER_REPOSITORIES_MUTATION,
  SYNC_VCS_INTEGRATION_MUTATION,
  type DiscoverRepositoriesData,
  type DiscoverRepositoriesVariables,
  type IdVariables,
  type SyncVcsIntegrationData,
} from "../documents";

const MODEL = "integrate.VCSIntegration";

const integrationList = (
  <List model={MODEL}>
    <Column field="displayName" />
    <Column field="backendClass" />
    <Column field="status" widget="statusBadge" />
    <Column field="lastSyncCompletedAt" />
  </List>
);

/**
 * VCS integrations: the git-host capabilities, their backend impl, and sync
 * health. The form binds an existing `Integration` (vendor=github) to a backend
 * class, then `discover`/`sync` populate and refresh the repository inventory.
 */
export function VCSIntegrationsPage(): React.ReactElement {
  const t = useIntegrateT();
  const [syncVcs] = useAuthoredMutation<SyncVcsIntegrationData, IdVariables>(
    SYNC_VCS_INTEGRATION_MUTATION,
  );
  const [discover] = useAuthoredMutation<
    DiscoverRepositoriesData,
    DiscoverRepositoriesVariables
  >(DISCOVER_REPOSITORIES_MUTATION);

  // `backendClass` reads as the UPPERCASE enum member but its create input is a
  // lowercase String key; `useEnumOptions` lower-cases the option values, and
  // `createOnly` keeps the read casing off the edit patch.
  const backendClassOptions = useEnumOptions(MODEL, "backendClass");

  const sync = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await syncVcs({ id: ctx.record.id });
      ctx.refresh();
      return runActionResult(result?.syncVcsIntegration);
    },
    [syncVcs],
  );
  const discoverAll = React.useCallback(
    async (ctx: ActionContext) => {
      if (typeof ctx.record?.id !== "string") return;
      const result = await discover({ vcsIntegrationId: ctx.record.id, org: "" });
      ctx.refresh();
      return runActionResult(result?.discoverRepositories);
    },
    [discover],
  );

  return (
    <DataPage model={MODEL} placement="inline" routed>
      {integrationList}
      <Form model={MODEL}>
        {/* The integration and its backend class are fixed at create; the patch
            input carries neither, so both are create-only. */}
        <Field name="integration" createOnly />
        <Field
          name="backendClass"
          widget="select"
          options={backendClassOptions}
          createOnly
        />
        <Field name="status" widget="statusbar" />
        <Field name="config" widget="json" />
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhookSecret" widget="text" kind="string" createOnly />
        <Action id="sync" label={t("integrate.action.syncNow")} icon="refresh" run={sync} />
        <Action id="discover" label={t("integrate.vcs.discover")} run={discoverAll} />
      </Form>
    </DataPage>
  );
}
