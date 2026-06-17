import * as React from "react";
import {
  Action,
  Column,
  DataPage,
  Field,
  Form,
  List,
  type ActionContext,
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

const MODEL = "integrate.VcsBridge";

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
      <List model={MODEL}>
        <Column field="displayName" />
        <Column
          field="integration.implLabel"
          header={t("integrate.integrations.implClass")}
        />
        <Column
          field="integration.status"
          header={t("integrate.col.status")}
          widget="statusBadge"
        />
        <Column field="lastSyncCompletedAt" />
      </List>
      <Form model={MODEL}>
        {/* The implementation lives on the owning Integration. */}
        <Field name="integration" createOnly />
        <Field name="lastSyncStatus" readOnly />
        {/* Write-only signing secret — set on create, never read back. */}
        <Field name="webhookSecret" widget="text" kind="string" createOnly />
        <Action id="sync" label={t("integrate.action.syncNow")} icon="refresh" run={sync} />
        <Action id="discover" label={t("integrate.vcs.discover")} run={discoverAll} />
      </Form>
    </DataPage>
  );
}
