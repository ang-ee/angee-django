import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import {
  authoredQueryKey, requestAuthoredData, sharedAuthoredMeta, useActiveDataProviderName,
  useAuthoredErrorPolicy, useAuthoredLiveInterest, useAuthoredMutation, useAuthoredQuery,
  useDataProvider, useInfiniteQuery, useQueryClient, useSetAuthoredQueryData, type AuthoredVariables,
} from "@angee/refine";
import { jsonValueFromUnknown, type JsonValue } from "@angee/ui";
import {
  parseDashboardSnapshot,
  type DashboardCapabilities,
  type DashboardCatalogueBinding,
  type DashboardLoadState,
  type DashboardSaveCommand,
  type DashboardSaveResult,
  type DashboardSnapshot,
  type DashboardStore,
  type DashboardSummary,
  type DashboardTarget,
  type WidgetVisibility,
} from "@angee/ui/dashboard/headless";

import {
  CreatePersonalDashboardDocument,
  DashboardDocument,
  DashboardSummariesDocument,
  DashboardWidgetVisibilityDocument,
  DuplicateDashboardDocument,
  ResetDashboardDocument,
  SaveDashboardDocument,
} from "./documents.console";

const DASHBOARD_MODELS = ["dashboards.Dashboard", "dashboards.DashboardWidget"] as const;

function dashboardSnapshotInput(snapshot: DashboardSaveCommand["snapshot"]): JsonValue {
  const value = jsonValueFromUnknown(snapshot);
  if (value === undefined) throw new TypeError("Dashboard snapshot must be a JSON value.");
  return value;
}

function targetInput(target: DashboardTarget) {
  if (target.scope === "personal") return { scope: "PERSONAL" as const, id: target.id };
  if (target.scope === "addon") return { scope: "ADDON" as const, key: target.key };
  return { scope: "RESOURCE" as const, key: target.key };
}

type QueryPayload = DocumentType<typeof DashboardDocument>["dashboard"];
type FullPayload = NonNullable<
  | QueryPayload
  | DocumentType<typeof CreatePersonalDashboardDocument>["create_personal_dashboard"]
  | DocumentType<typeof SaveDashboardDocument>["save_dashboard"]
  | DocumentType<typeof DuplicateDashboardDocument>["duplicate_dashboard"]
>;
type ErrorPayload = Pick<FullPayload, "status" | "current_revision" | "message">;

function capabilities(payload: FullPayload): DashboardCapabilities {
  return {
    canEdit: Boolean(payload.can_edit),
    canReset: Boolean(payload.can_reset),
    canTrash: Boolean(payload.can_trash),
  };
}

function payloadError(payload: ErrorPayload | null | undefined): Error | null {
  if (!payload) return new Error("The dashboard API returned no result.");
  if (payload.status === "ready" || payload.status === "absent") return null;
  if (payload.status === "conflict") {
    const suffix = payload.current_revision == null ? "" : ` Current revision: ${payload.current_revision}.`;
    return new Error(`${payload.message ?? "The dashboard changed since it was loaded."}${suffix}`);
  }
  return new Error(payload.message ?? "The dashboard operation failed.");
}

function saveResult(payload: FullPayload | null | undefined): DashboardSaveResult {
  const error = payloadError(payload);
  if (error) throw error;
  if (!payload?.id || !payload.name || payload.revision == null || payload.snapshot == null) {
    throw new Error("The dashboard API returned an incomplete snapshot.");
  }
  return {
    persistedId: payload.id,
    revision: payload.revision,
    snapshot: parseDashboardSnapshot(payload.snapshot),
    capabilities: capabilities(payload),
  };
}

function loadState(
  payload: QueryPayload | null | undefined,
  fetching: boolean,
  error: Error | null,
): DashboardLoadState {
  const rights = payload ? capabilities(payload) : { canEdit: false, canReset: false, canTrash: false };
  if (error) return { status: "error", error, capabilities: rights };
  if (!payload) return fetching ? { status: "loading", capabilities: rights } : { status: "unavailable", capabilities: rights };
  if (payload.status === "absent") return { status: "absent", capabilities: rights };
  if (payload.status === "forbidden") return { status: "forbidden", message: payload.message ?? undefined, capabilities: rights };
  if (payload.status === "unavailable") return { status: "unavailable", message: payload.message ?? undefined, capabilities: rights };
  if (payload.status !== "ready") return { status: "error", error: payloadError(payload) ?? new Error("Dashboard load failed."), capabilities: rights };
  if (!payload.id || payload.revision == null || payload.snapshot == null || !payload.name) {
    return { status: "error", error: new Error("The dashboard API returned an incomplete snapshot."), capabilities: rights };
  }
  try {
    return {
      status: "ready",
      persistedId: payload.id,
      revision: payload.revision,
      name: payload.name,
      description: payload.description ?? undefined,
      declarationRevision: payload.declaration_revision ?? "",
      snapshot: parseDashboardSnapshot(payload.snapshot),
      capabilities: rights,
    };
  } catch (cause) {
    return { status: "error", error: cause instanceof Error ? cause : new Error(String(cause)), capabilities: rights };
  }
}

/** The mutation returns the complete Dashboard projection for this target. */
function useAcceptDashboard() {
  const acceptQuery = useSetAuthoredQueryData();
  return React.useCallback(async (payload: FullPayload | null | undefined, target: AuthoredVariables<typeof DashboardDocument>["target"]) => {
    saveResult(payload);
    if (!payload) return;
    await acceptQuery(DashboardDocument, { target }, { dashboard: payload });
  }, [acceptQuery]);
}

function useCreatePersonal() {
  const acceptDashboard = useAcceptDashboard();
  const [create] = useAuthoredMutation(CreatePersonalDashboardDocument, {
    invalidateModels: DASHBOARD_MODELS,
    errorFrom: (data) => payloadError(data?.create_personal_dashboard),
    acceptResponse: async (data) => {
      const payload = data?.create_personal_dashboard;
      await acceptDashboard(payload, { scope: "PERSONAL", id: saveResult(payload).persistedId });
    },
  });
  return React.useCallback(async (input: { name: string; description?: string; idempotencyKey: string }) => {
    const result = await create({
      name: input.name,
      description: input.description ?? "",
      idempotencyKey: input.idempotencyKey,
    });
    return saveResult(result?.create_personal_dashboard);
  }, [create]);
}

function useDashboard(target: DashboardTarget) {
  const variables = React.useMemo(() => ({ target: targetInput(target) }), [target]);
  const query = useAuthoredQuery(DashboardDocument, variables, { models: DASHBOARD_MODELS });
  const loadedState = loadState(query.data?.dashboard, query.isFetching, query.error);
  const acceptDashboard = useAcceptDashboard();
  const [saveMutation] = useAuthoredMutation(SaveDashboardDocument, {
    invalidateModels: DASHBOARD_MODELS,
    errorFrom: (data) => payloadError(data?.save_dashboard),
    acceptResponse: (data, input) => acceptDashboard(data?.save_dashboard, input.target),
  });
  const [resetMutation] = useAuthoredMutation(ResetDashboardDocument, {
    invalidateModels: DASHBOARD_MODELS,
    errorFrom: (data) => payloadError(data?.reset_dashboard),
  });
  const [duplicateMutation] = useAuthoredMutation(DuplicateDashboardDocument, {
    invalidateModels: DASHBOARD_MODELS,
    errorFrom: (data) => payloadError(data?.duplicate_dashboard),
    acceptResponse: async (data) => {
      const payload = data?.duplicate_dashboard;
      await acceptDashboard(payload, { scope: "PERSONAL", id: saveResult(payload).persistedId });
    },
  });
  const createPersonal = useCreatePersonal();

  return {
    state: loadedState,
    reload: React.useCallback(async () => { await query.refetch(); }, [query.refetch]),
    save: React.useCallback(async (command: DashboardSaveCommand) => {
      const result = await saveMutation({
        target: targetInput(command.target),
        snapshot: dashboardSnapshotInput(command.snapshot),
        persistedId: command.persistedId,
        expectedRevision: command.expectedRevision,
        declarationRevision: command.declarationRevision ?? "",
        name: command.name ?? "",
        description: command.description ?? "",
      });
      return saveResult(result?.save_dashboard);
    }, [saveMutation]),
    reset: React.useCallback(async (resetTarget: DashboardTarget, persistedId: string, expectedRevision: number) => {
      const result = await resetMutation({ target: targetInput(resetTarget), persistedId, expectedRevision });
      const error = payloadError(result?.reset_dashboard);
      if (error) throw error;
    }, [resetMutation]),
    createPersonal,
    duplicate: React.useCallback(async (duplicateTarget: DashboardTarget, input: { name: string; idempotencyKey: string; snapshot: DashboardSnapshot }) => {
      const result = await duplicateMutation({
        target: targetInput(duplicateTarget),
        name: input.name,
        idempotencyKey: input.idempotencyKey,
        snapshot: dashboardSnapshotInput(input.snapshot),
      });
      return saveResult(result?.duplicate_dashboard);
    }, [duplicateMutation]),
  };
}

function summaryTarget(row: { id: string; scope: string; scope_key?: string | null }): DashboardTarget {
  if (row.scope === "PERSONAL") return { scope: "personal", id: row.id };
  if (row.scope === "ADDON" && row.scope_key) return { scope: "addon", key: row.scope_key };
  if (row.scope === "RESOURCE" && row.scope_key) return { scope: "resource", key: row.scope_key };
  return { scope: "personal", id: row.id };
}

function useCatalogue(): DashboardCatalogueBinding {
  type Page = DocumentType<typeof DashboardSummariesDocument>["dashboard_summaries"];
  type Cursor = { cursor: string | null; version: string | null };
  const client = useQueryClient();
  const dataProvider = useDataProvider();
  const provider = useActiveDataProviderName() ?? "default";
  const queryKey = React.useMemo(() => [
    ...authoredQueryKey(DashboardSummariesDocument, { cursor: null, version: null }, provider), "catalogue",
  ], [provider]);
  const restarts = React.useRef(0);
  const query = useInfiniteQuery({
    queryKey,
    meta: sharedAuthoredMeta(client, queryKey, DASHBOARD_MODELS),
    initialPageParam: { cursor: null, version: null } as Cursor,
    queryFn: async (context): Promise<Page> => (await requestAuthoredData<DocumentType<typeof DashboardSummariesDocument>>(
      dataProvider, provider, DashboardSummariesDocument, context.pageParam, context,
    )).dashboard_summaries,
    getNextPageParam: (page): Cursor | undefined => page.status === "ready" && page.version && page.next_cursor
      ? { cursor: page.next_cursor, version: page.version } : undefined,
  });
  useAuthoredLiveInterest(true, DASHBOARD_MODELS);
  useAuthoredErrorPolicy([queryKey]);
  const createPersonal = useCreatePersonal();
  const lastPage = query.data?.pages.at(-1);
  const restart = React.useCallback((manual = false) => {
    if (manual) restarts.current = 0;
    void client.resetQueries({ queryKey, exact: true }).catch(() => undefined);
  }, [client, queryKey]);
  React.useEffect(() => {
    if (query.isFetching || query.error || !lastPage) return;
    if (lastPage.status === "collection_changed") {
      if (restarts.current < 2) { restarts.current += 1; restart(); }
      return;
    }
    if (lastPage.status === "ready" && lastPage.version) {
      if (query.hasNextPage) void query.fetchNextPage().catch(() => undefined);
      else restarts.current = 0;
    }
  }, [lastPage, query.isFetching, query.error, query.hasNextPage, query.fetchNextPage, restart]);
  const collectionError = !lastPage || lastPage.status === "ready" && lastPage.version ? null
    : lastPage.status === "collection_changed" && restarts.current < 2 ? null
      : new Error(lastPage.status === "collection_changed"
        ? "The dashboard catalogue kept changing. Retry when updates settle."
        : lastPage.status === "limit" ? "The dashboard catalogue exceeds the 5,000 item limit."
          : "The dashboard catalogue returned an invalid page.");
  const rows = React.useMemo(() => query.data?.pages.flatMap((page) => page.status === "ready" ? page.items : []) ?? [], [query.data]);
  const summaries = React.useMemo<readonly DashboardSummary[]>(() =>
    rows.map((row) => ({
      id: row.id,
      target: summaryTarget(row),
      title: row.name,
      description: row.description || undefined,
      owner: row.owner_label ?? row.owner ?? undefined,
      resources: row.resources,
      presets: row.presets,
      revision: row.revision,
      customized: row.scope !== "PERSONAL",
      available: !row.is_trashed,
      capabilities: {
        canEdit: row.can_edit,
        canReset: row.scope !== "PERSONAL" && row.can_edit,
        canTrash: row.can_trash,
      },
    })), [rows]);
  return {
    summaries,
    loading: query.isFetching || query.isPending || query.hasNextPage,
    error: collectionError ?? query.error,
    refresh: () => restart(true),
    createPersonal,
  };
}

function useWidgetVisibility(policies: readonly WidgetVisibility[]) {
  const query = useAuthoredQuery(DashboardWidgetVisibilityDocument, { policies: [...policies] }, {
    models: [...new Set(policies.map(({ resource }) => resource))],
  });
  return {
    allowed: query.data?.dashboard_widget_visibility ?? [],
    loading: !query.data,
    error: query.error,
  };
}

export const dashboardStore: DashboardStore = {
  useDashboard,
  useCatalogue,
  useWidgetVisibility,
};
