import { DetailSection, DetailSurface, TextLink, useRouteRecordId } from "@angee/ui";
import { type ReactElement } from "react";

import {
  WORKSPACE_LOGS_QUERY,
  WORKSPACE_LOGS_SUBSCRIPTION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { RowActions } from "../parts/RowActions";
import { LogPanel, useDaemonLogStream } from "./logs";
import { useWorkspaceActions } from "./workspace-actions";

/** Workspace detail: overview + lifecycle actions + the live log tail. */
export function WorkspaceDetail(): ReactElement {
  const t = useOperatorT();
  const name = useRouteRecordId();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const { actions, busy } = useWorkspaceActions(refetch);
  const logs = useDaemonLogStream({
    name,
    historyQuery: WORKSPACE_LOGS_QUERY,
    historyField: "workspaceLogs",
    streamSubscription: WORKSPACE_LOGS_SUBSCRIPTION,
    streamField: "onWorkspaceLogs",
  });

  const workspace =
    (snapshot?.workspaces ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <DetailSurface
      loading={result.fetching && !snapshot}
      loadingMessage={t("workspaces.loading")}
      empty={
        !workspace
          ? {
              icon: "files",
              title: t("workspaces.detail.notFound"),
              description: name,
            }
          : null
      }
      title={workspace?.name}
      meta={
        workspace ? (
          <span className="text-fg-muted">{workspace.template}</span>
        ) : null
      }
      actions={
        workspace ? (
          <RowActions
            actions={actions}
            busy={busy}
            subject={workspace}
            className="flex flex-wrap gap-1"
          />
        ) : undefined
      }
    >
      {workspace ? (
        <>
          <DetailSection
            title={t("workspaces.detail.overview")}
            rows={[
              [t("workspaces.column.template"), workspace.template],
              [t("workspaces.column.path"), workspace.path],
              [
                t("workspaces.column.port"),
                workspace.processComposePort ?? "—",
              ],
              [t("workspaces.column.ttl"), workspace.ttl ?? "—"],
              [
                t("workspaces.detail.expiresAt"),
                workspace.ttlExpiresAt ?? "—",
              ],
              [
                t("workspaces.detail.mcp"),
                workspace.playwrightMcpUrl ? (
                  <TextLink href={workspace.playwrightMcpUrl} target="_blank">
                    {workspace.playwrightMcpUrl}
                  </TextLink>
                ) : (
                  "—"
                ),
              ],
            ]}
          />

          <LogPanel logs={logs} title={t("workspaces.detail.logs")} />
        </>
      ) : null}
    </DetailSurface>
  );
}
