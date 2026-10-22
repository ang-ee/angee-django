import { Code, DetailSection, formatDateTime, NavLink, useRouteRecordId } from "@angee/ui";
import { type ReactElement } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { DaemonRecord } from "../parts/DaemonRecord";
import { useWorkspaceActions } from "./workspace-actions";

/** Workspace record page: band verbs and the overview facts. */
export function WorkspaceDetail(): ReactElement {
  const t = useOperatorT();
  const name = useRouteRecordId();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const { actions } = useWorkspaceActions(refetch);
  const workspace = (snapshot?.workspaces ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <DaemonRecord
      record={workspace}
      loading={result.fetching && !snapshot}
      loadingMessage={t("workspaces.loading")}
      notFound={{ icon: "files", title: t("workspaces.detail.notFound"), description: name }}
      eyebrow={t("workspaces.record")}
      title={workspace?.name}
      meta={workspace ? <span>{workspace.template}</span> : null}
      verbs={actions}
    >
      {workspace ? (
        <DetailSection
          title={t("workspaces.detail.overview")}
          rows={[
            [t("workspaces.column.template"), workspace.template],
            [t("workspaces.column.path"), <Code truncate>{workspace.path}</Code>],
            [t("workspaces.column.port"), workspace.processComposePort],
            [t("workspaces.column.ttl"), workspace.ttl],
            [t("workspaces.detail.expiresAt"), workspace.ttlExpiresAt ? formatDateTime(workspace.ttlExpiresAt) : null],
            [t("workspaces.detail.mcp"), workspace.playwrightMcpUrl ? (
              <NavLink href={workspace.playwrightMcpUrl} target="_blank" variant="inline">{workspace.playwrightMcpUrl}</NavLink>
            ) : null],
          ]}
        />
      ) : null}
    </DaemonRecord>
  );
}
