import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  LoadingPanel,
  LogStream,
  MetaGrid,
  RecordHeader,
  TextLink,
} from "@angee/base";
import { useMemo, useState, type ReactElement } from "react";
import { useParams } from "@tanstack/react-router";
import { useQuery } from "urql";

import {
  WORKSPACE_LOGS_QUERY,
  WORKSPACE_LOGS_SUBSCRIPTION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot, useOperatorSubscription } from "../../data/transport";
import { WorkspaceActions, useWorkspaceActions } from "./workspace-actions";

const HISTORY_LIMIT = 500;
const MAX_LIVE_LINES = 2000;

/**
 * The workspace's log buffer (one-shot history query) followed by the live tail
 * (`onWorkspaceLogs` streams line-by-line). The subscription's `onData`
 * accumulates each emission, so no line is dropped between renders.
 */
function useWorkspaceLogs(name: string | undefined): readonly string[] {
  const [history] = useQuery<{ workspaceLogs: string }>({
    query: WORKSPACE_LOGS_QUERY,
    variables: { name: name ?? "", limit: HISTORY_LIMIT },
    pause: !name,
  });
  const [live, setLive] = useState<readonly string[]>([]);
  useOperatorSubscription<{ onWorkspaceLogs: string }, { name: string }>(
    WORKSPACE_LOGS_SUBSCRIPTION,
    { name: name ?? "" },
    {
      enabled: Boolean(name),
      onData: (value) => {
        const line = value.onWorkspaceLogs;
        if (line == null) return;
        setLive((prev) => {
          const next = [...prev, line];
          return next.length > MAX_LIVE_LINES ? next.slice(-MAX_LIVE_LINES) : next;
        });
      },
    },
  );
  return useMemo(() => {
    const text = history.data?.workspaceLogs ?? "";
    const historyLines = text === "" ? [] : text.replace(/\n$/, "").split("\n");
    return [...historyLines, ...live];
  }, [history.data, live]);
}

/** Workspace detail: overview + lifecycle actions + the live log tail. */
export function WorkspaceDetail(): ReactElement {
  const t = useOperatorT();
  const params = useParams({ strict: false });
  const name = "name" in params && typeof params.name === "string" ? params.name : undefined;
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const { actions, busy } = useWorkspaceActions(refetch);
  const logs = useWorkspaceLogs(name);

  const workspace =
    (snapshot?.workspaces ?? []).find((candidate) => candidate.name === name) ?? null;

  if (result.fetching && !snapshot) {
    return <LoadingPanel message={t("operator.workspaces.loading")} />;
  }
  if (!workspace) {
    return (
      <EmptyState
        fill
        icon="files"
        title={t("operator.workspaces.detail.notFound")}
        description={name}
      />
    );
  }

  return (
    <div className="flex min-h-0 flex-col gap-4 p-4">
      <RecordHeader
        title={workspace.name}
        meta={<span className="text-fg-muted">{workspace.template}</span>}
      />

      <WorkspaceActions
        actions={actions}
        busy={busy}
        workspace={workspace}
        className="flex flex-wrap gap-1"
      />

      <Card>
        <CardHeader>
          <CardTitle>{t("operator.workspaces.detail.overview")}</CardTitle>
        </CardHeader>
        <CardContent>
          <MetaGrid
            rows={[
              [t("operator.workspaces.column.template"), workspace.template],
              [t("operator.workspaces.column.path"), workspace.path],
              [t("operator.workspaces.column.port"), workspace.processComposePort ?? "—"],
              [t("operator.workspaces.column.ttl"), workspace.ttl ?? "—"],
              [t("operator.workspaces.detail.expiresAt"), workspace.ttlExpiresAt ?? "—"],
              [
                t("operator.workspaces.detail.mcp"),
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
        </CardContent>
      </Card>

      <Card className="flex min-h-0 flex-1 flex-col">
        <CardHeader>
          <CardTitle>{t("operator.workspaces.detail.logs")}</CardTitle>
        </CardHeader>
        <CardContent className="flex min-h-0 flex-1 flex-col">
          <LogStream
            lines={logs}
            className="min-h-64 flex-1"
            emptyMessage={t("operator.workspaces.detail.logs.empty")}
          />
        </CardContent>
      </Card>
    </div>
  );
}
