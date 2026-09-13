import * as React from "react";
import type { DocumentType } from "@angee/gql/console";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  parseDashboardSnapshot,
  type DashboardCapabilities,
  type DashboardCatalogueBinding,
  type DashboardLoadState,
  type DashboardSaveCommand,
  type DashboardSaveResult,
  type DashboardStore,
  type DashboardSummary,
  type DashboardTarget,
} from "@angee/ui/dashboard/headless";

import {
  ArchiveDashboardDocument,
  CreatePersonalDashboardDocument,
  DashboardDocument,
  DashboardSummariesDocument,
  DuplicateDashboardDocument,
  ResetDashboardDocument,
  SaveDashboardDocument,
} from "./documents.console";

const DASHBOARD_MODELS = ["dashboards.Dashboard", "dashboards.DashboardWidget"] as const;

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
  | DocumentType<typeof ArchiveDashboardDocument>["set_personal_dashboard_archived"]
>;
type ErrorPayload = Pick<FullPayload, "status" | "current_revision" | "message">;

function capabilities(payload: FullPayload): DashboardCapabilities {
  return {
    canEdit: Boolean(payload.can_edit),
    canReset: Boolean(payload.can_reset),
    canArchive: Boolean(payload.can_archive),
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
  if (!payload?.id || payload.revision == null || payload.snapshot == null) {
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
  if (error) return { status: "error", error };
  if (!payload) return fetching ? { status: "loading" } : { status: "unavailable" };
  if (payload.status === "absent") return { status: "absent" };
  if (payload.status === "forbidden") return { status: "forbidden", message: payload.message ?? undefined };
  if (payload.status === "unavailable") return { status: "unavailable", message: payload.message ?? undefined };
  if (payload.status !== "ready") return { status: "error", error: payloadError(payload) ?? new Error("Dashboard load failed.") };
  if (!payload.id || payload.revision == null || payload.snapshot == null || !payload.name) {
    return { status: "error", error: new Error("The dashboard API returned an incomplete snapshot.") };
  }
  try {
    return {
      status: "ready",
      persistedId: payload.id,
      revision: payload.revision,
      name: payload.name,
      description: payload.description ?? undefined,
      snapshot: parseDashboardSnapshot(payload.snapshot),
      capabilities: capabilities(payload),
    };
  } catch (cause) {
    return { status: "error", error: cause instanceof Error ? cause : new Error(String(cause)) };
  }
}

function useCreatePersonal() {
  const [create] = useAuthoredMutation(CreatePersonalDashboardDocument, {
    invalidateModels: DASHBOARD_MODELS,
  });
  return React.useCallback(async (input: { name: string; description?: string; clientCreationKey: string }) => {
    const result = await create({
      name: input.name,
      description: input.description ?? "",
      clientCreationKey: input.clientCreationKey,
    });
    return saveResult(result?.create_personal_dashboard);
  }, [create]);
}

function useDashboard(target: DashboardTarget) {
  const variables = React.useMemo(() => ({ target: targetInput(target) }), [target]);
  const query = useAuthoredQuery(DashboardDocument, variables, { models: DASHBOARD_MODELS });
  const loadedState = loadState(query.data?.dashboard, query.isFetching, query.error);
  const [saveMutation] = useAuthoredMutation(SaveDashboardDocument, { invalidateModels: DASHBOARD_MODELS });
  const [resetMutation] = useAuthoredMutation(ResetDashboardDocument, { invalidateModels: DASHBOARD_MODELS });
  const [duplicateMutation] = useAuthoredMutation(DuplicateDashboardDocument, { invalidateModels: DASHBOARD_MODELS });
  const [archiveMutation] = useAuthoredMutation(ArchiveDashboardDocument, { invalidateModels: DASHBOARD_MODELS });
  const createPersonal = useCreatePersonal();

  return {
    state: loadedState,
    reload: React.useCallback(async () => { await query.refetch(); }, [query.refetch]),
    save: React.useCallback(async (command: DashboardSaveCommand) => {
      const result = await saveMutation({
        target: targetInput(command.target),
        snapshot: command.snapshot,
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
    duplicate: React.useCallback(async (duplicateTarget: DashboardTarget, input: { name: string; clientCreationKey: string }) => {
      const result = await duplicateMutation({
        target: targetInput(duplicateTarget),
        name: input.name,
        clientCreationKey: input.clientCreationKey,
      });
      return saveResult(result?.duplicate_dashboard);
    }, [duplicateMutation]),
    archive: React.useCallback(async (id: string, expectedRevision: number, archived: boolean) => {
      const result = await archiveMutation({ id, expectedRevision, archived });
      return saveResult(result?.set_personal_dashboard_archived);
    }, [archiveMutation]),

  };
}

function summaryTarget(row: { id: string; scope: string; scope_key?: string | null }): DashboardTarget {
  if (row.scope === "PERSONAL") return { scope: "personal", id: row.id };
  if (row.scope === "ADDON" && row.scope_key) return { scope: "addon", key: row.scope_key };
  if (row.scope === "RESOURCE" && row.scope_key) return { scope: "resource", key: row.scope_key };
  return { scope: "personal", id: row.id };
}

function useCatalogue(): DashboardCatalogueBinding {
  type SummaryRow = DocumentType<typeof DashboardSummariesDocument>["dashboard_summaries"]["items"][number];
  const [request, setRequest] = React.useState<{ cursor: string | null; version: string | null }>({ cursor: null, version: null });
  const [rows, setRows] = React.useState<readonly SummaryRow[]>([]);
  const [complete, setComplete] = React.useState(false);
  const [collectionError, setCollectionError] = React.useState<Error | null>(null);
  const restarts = React.useRef(0);
  const processedPage = React.useRef<string | null>(null);
  const query = useAuthoredQuery(
    DashboardSummariesDocument,
    { cursor: request.cursor, version: request.version },
    { models: DASHBOARD_MODELS },
  );
  const createPersonal = useCreatePersonal();
  const restart = React.useCallback((manual = false) => {
    if (manual) restarts.current = 0;
    processedPage.current = null;
    setRows([]);
    setComplete(false);
    setCollectionError(null);
    setRequest({ cursor: null, version: null });
  }, []);
  React.useEffect(() => {
    const page = query.data?.dashboard_summaries;
    if (!page) return;
    if (page.status === "collection_changed") {
      if (restarts.current >= 2) {
        setCollectionError(new Error("The dashboard catalogue kept changing. Retry when updates settle."));
        setComplete(true);
        return;
      }
      restarts.current += 1;
      restart();
      return;
    }
    if (page.status === "limit") {
      setCollectionError(new Error("The dashboard catalogue exceeds the 5,000 item limit."));
      setComplete(true);
      return;
    }
    if (page.status !== "ready" || !page.version) {
      setCollectionError(new Error("The dashboard catalogue returned an invalid page."));
      setComplete(true);
      return;
    }
    const pageKey = `${request.cursor ?? "__first__"}:${page.version}`;
    if (processedPage.current === pageKey) return;
    processedPage.current = pageKey;
    setRows((current) => request.cursor ? [...current, ...page.items] : page.items);
    if (page.next_cursor) {
      setRequest({ cursor: page.next_cursor, version: page.version });
    } else {
      setComplete(true);
      restarts.current = 0;
    }
  }, [query.data, request.cursor, restart]);
  const summaries = React.useMemo<readonly DashboardSummary[]>(() =>
    rows.map((row) => ({
      id: row.id,
      target: summaryTarget(row),
      title: row.name,
      description: row.description || undefined,
      owner: row.owner_label ?? row.owner ?? undefined,
      resources: row.resources,
      revision: row.revision,
      customized: row.scope !== "PERSONAL",
      available: !row.is_archived,
      capabilities: {
        canEdit: row.can_edit,
        canReset: row.scope !== "PERSONAL" && row.can_edit,
        canArchive: row.can_archive,
      },
    })), [rows]);
  return {
    summaries,
    loading: query.isFetching || !complete,
    error: collectionError ?? query.error,
    refresh: () => restart(true),
    createPersonal,
  };
}

export const dashboardStore: DashboardStore = {
  useDashboard,
  useCatalogue,
};
