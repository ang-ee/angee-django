import { Filter, ResourceQuery, holdsPermission, useModelMetadata, type QueryFilter } from "@angee/metadata";
import { useAuthoredQuery, useInvalidateAuthoredModels } from "@angee/refine";
import { useCallback, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  CollectionTreeView, EmptyState, ErrorBanner, InlineTextAction, PaneToolbarMenu, RemovedRecords,
  ResourceViewProvider, ScopedExplorerPane, errorMessage, useAppRuntime, useBreadcrumbCollectionLink,
  useBreadcrumbLeafLabel, useListRecordNavigation, useRouteHref, useRouteRecordId, useRuntimeViewAs,
  useResourceView, useRouteSearch, useLatestRef, useScopedTreeExplorer, parseRecordNavigationScope, recordNavigationSearch,
  type CollectionPageRequest, type ListViewNavigationScope, type ListViewProps, type ScopedExplorerController,
} from "@angee/ui";
import {
  KnowledgePage as KnowledgePageQuery, KnowledgeVault, KnowledgeVaults, PAGE_MODEL, PAGE_READ_MODELS,
  type KnowledgePageDetail,
} from "../data/documents";
import { KNOWLEDGE_PAGE_DND, pageDragPayload, pageIcon, type KnowledgeTreeRow } from "../data/page-rows";
import { pageCollectionFilter, pageSource } from "../data/page-source";
import { usePageActions } from "../data/use-page-actions";
import { KnowledgePageView } from "../KnowledgePageView";
import { useKnowledgeT } from "../i18n";

type VaultRow = { id: string; name: string; permissions: readonly string[] };
type Controller = ScopedExplorerController<VaultRow, KnowledgeTreeRow>;
type Insertion = { kind: "note" | "folder"; parent: string | null; vault: string };
const noRows = (): readonly KnowledgeTreeRow[] => [];
const rootId = (vault: VaultRow) => vault.id;
const rootLabel = (vault: VaultRow) => vault.name;

/** Native explorer and record form; detail access is independent of the navigator. */
export function KnowledgePage() {
  const t = useKnowledgeT();
  const navigate = useNavigate();
  const href = useRouteHref();
  const openId = useRouteRecordId() ?? null;
  const metadata = useModelMetadata(PAGE_MODEL);
  const vaultMetadata = useModelMetadata("knowledge.Vault");
  const search = useRouteSearch();
  const navigationScope = useMemo(() => parseRecordNavigationScope(search, metadata?.resource), [search, metadata]);
  const { defaultResourceFilter } = useAppRuntime();
  const scope = defaultResourceFilter?.resource === PAGE_MODEL ? defaultResourceFilter.filter : undefined;
  const home = href("knowledge.home");
  const [vaultSearch, setVaultSearch] = useState("");
  const handleRootChange = useCallback(() => {
    setVaultSearch("");
    void navigate({ to: home });
  }, [home, navigate]);
  const detailRead = useAuthoredQuery(KnowledgePageQuery, { id: openId ?? "" }, {
    enabled: Boolean(openId), models: PAGE_READ_MODELS,
  });
  const detail = detailRead.data?.pages_by_pk?.id === openId ? detailRead.data.pages_by_pk : undefined;
  const vaults = useAuthoredQuery(KnowledgeVaults, {
    limit: 50, where: vaultMetadata ? ResourceQuery.from(vaultMetadata).toWhere({
      is_trashed: { exact: false }, name: { iContains: vaultSearch },
      ...(Filter.from(scope).facetValues("vault").length ? { id: { inList: Filter.from(scope).facetValues("vault") } } : {}),
    }) : undefined,
  }, { enabled: Boolean(vaultMetadata), models: ["knowledge.Vault"] });
  const controller = useScopedTreeExplorer({
    roots: vaults.data?.vaults ?? [], getRootId: rootId, getTreeRows: noRows,
    selectedRootId: openId ? detail?.vault ?? null : undefined,
    onRootIdChange: handleRootChange,
  });
  const activeVaultId = controller.rootId;
  const [creating, setCreating] = useState<Insertion | null>(null);
  if (creating && creating.vault !== activeVaultId) setCreating(null);
  const vault = useAuthoredQuery(KnowledgeVault, { id: activeVaultId }, {
    enabled: Boolean(activeVaultId), models: ["knowledge.Vault"],
  });
  const roots = useMemo(() => [...new Map([
    ...(vaults.data?.vaults ?? []), ...(vault.data?.vaults_by_pk?.id === activeVaultId ? [vault.data.vaults_by_pk] : []),
  ].map((row) => [row.id, row])).values()], [vaults.data, vault.data, activeVaultId]);
  useBreadcrumbLeafLabel(detail?.title ?? null);
  useBreadcrumbCollectionLink(home, openId ? home : null);
  const openPage = useCallback((id: string, context?: ListViewNavigationScope) => {
    void navigate({ to: href("knowledge.page", { id }),
      search: (current: Record<string, unknown>) => recordNavigationSearch(current, metadata?.resource, context ?? null) });
  }, [href, metadata, navigate]);
  const pager = useListRecordNavigation({ resource: PAGE_MODEL, recordId: openId, navigationScope, onSelect: openPage });
  const invalidate = useInvalidateAuthoredModels();
  const latestCreation = useLatestRef(creating);
  const completeCreation = useCallback((id: string, insertion: Insertion) => {
    if (latestCreation.current !== insertion) return;
    setCreating(null);
    openPage(id);
  }, [latestCreation, openPage]);
  const actions = usePageActions();
  const preview = useRuntimeViewAs();
  const writable = !preview.viewAs && !preview.pending && holdsPermission(roots.find((row) => row.id === activeVaultId), "write");
  const parent = detail?.vault === activeVaultId && !detail.is_trashed
    ? detail.kind === "folder" ? detail.id : detail.parent : null;
  const renderTree = useCallback((controller: Controller, selector: ReactNode) => <KnowledgeTree
    pane={{ selector, overflowItems: [{ icon: "vault", label: t("vault.settings"), onClick: () => void navigate({ to: href("knowledge.settings") }) }] }}
    toolbarActions={controller.root ? <PaneToolbarMenu icon="plus" label={t("newPage.menu")} text disabled={!writable || actions.busy} items={[
        { icon: "note", label: t("newPage.page"), onClick: () => setCreating({ kind: "note", parent, vault: controller.rootId }) },
        { icon: "folder", label: t("newPage.folder"), onClick: () => setCreating({ kind: "folder", parent, vault: controller.rootId }) },
        ...(detail && detail.vault === controller.rootId && !detail.is_trashed ? [{ icon: "note", label: t("newPage.subpage"),
          onClick: () => setCreating({ kind: "note" as const, parent: detail.id, vault: controller.rootId }) }] : []),
]} /> : null}
    vault={controller.rootId} detail={detail ?? null} scope={scope} writable={writable}
    creating={creating?.vault === controller.rootId ? creating : null}
    onCancel={() => setCreating((current) => current === creating ? null : current)}
    createPage={actions.createPage} movePage={actions.movePage} onCreated={completeCreation}
    onSelect={(row, request) => pager.selectRecord(row.id, {
      filter: pageCollectionFilter(controller.rootId, request, scope),
      order: request.order ?? { title: "ASC" }, page: request.page, pageSize: request.pageSize,
    })} />, [actions.busy, actions.createPage, actions.movePage, completeCreation, creating, detail, href, navigate, pager.selectRecord, parent, scope, t, writable]);
  const removed = useCallback((controller: Controller) => controller.rootId ? <RemovedRecords
    resource={metadata?.resource} fields={["title"]} label={(row) => String(row.title)}
    filters={[{ field: "vault", operator: "eq", value: controller.rootId }, { field: "removed_root", operator: "eq", value: true }]} /> : null,
  [metadata?.resource]);
  return <ScopedExplorerPane<VaultRow, KnowledgeTreeRow>
    controller={controller} roots={roots} getRootId={rootId} getRootLabel={rootLabel}
    renderContentWithoutRoots={Boolean(openId)} navigatorLabel={t("nav.label")}
    rootPicker={{ "aria-label": t("vault.label"), placeholder: t("vault.placeholder"),
      searchPlaceholder: t("vault.searchPlaceholder"), onSearchChange: setVaultSearch,
      searchState: { pending: vaults.isFetching, error: vaults.error?.message, retry: () => void vaults.refetch() },
      create: scope ? undefined : { resource: "knowledge.Vault" },
      onCreated: () => invalidate(["knowledge.Vault"]) }}
    renderTree={renderTree} renderNavigatorFooter={removed}
    loading={vaults.isPending} emptyContent={vaults.error
      ? <ErrorBanner description={errorMessage(vaults.error, t("vaults.unavailableTitle"))} />
      : <EmptyState fill icon="vault" title={t("vaults.emptyTitle")} description={t("vaults.emptyDescription")} />}>
    {() => openId ? <KnowledgePageView pageId={openId} navigation={pager.navigation} />
      : <EmptyState fill icon="note" title={t("page.selectTitle")} description={t("page.selectDescription")} />}
  </ScopedExplorerPane>;
}

function KnowledgeTree(props: {
  pane: ListViewProps["pane"]; toolbarActions: ReactNode;
  vault: string; detail: KnowledgePageDetail | null; scope?: QueryFilter; writable: boolean;
  creating: Insertion | null; onCancel: () => void;
  createPage: ReturnType<typeof usePageActions>["createPage"];
  movePage: ReturnType<typeof usePageActions>["movePage"];
  onCreated: (id: string, insertion: Insertion) => void;
  onSelect: (row: KnowledgeTreeRow, request: CollectionPageRequest) => void;
}) {
  return <ResourceViewProvider key={props.vault} scope="local" resource={PAGE_MODEL}
    initialState={{ pageSize: 50, sort: { field: "title", dir: "asc" } }}>
    <KnowledgeTreeContent {...props} />
  </ResourceViewProvider>;
}

function KnowledgeTreeContent({ pane, toolbarActions, vault, detail, scope, creating, onCancel, createPage, movePage, writable, onCreated, onSelect }: Parameters<typeof KnowledgeTree>[0]) {
  const t = useKnowledgeT();
  const metadata = useModelMetadata(PAGE_MODEL);
  const view = useResourceView();
  const source = useMemo(() => metadata ? pageSource(metadata, vault, scope) : null, [metadata, scope, vault]);
  const revealRows = useMemo(() => {
    if (!detail || detail.vault !== vault || detail.is_trashed || (!creating && Object.keys(view.state.filter).length)) return [];
    return [...detail.ancestors, detail].map((page, index, rows) => ({ ...page,
      parent: index ? rows[index - 1]!.id : null, icon: page.icon || pageIcon(page.kind),
    }));
  }, [creating, detail, vault, view.state.filter]);
  const [moveError, setMoveError] = useState<unknown>(null);
  if (!source) return null;
  return <>
    {moveError ? <ErrorBanner description={errorMessage(moveError, t("page.moveError"))} /> : null}
    <CollectionTreeView<KnowledgeTreeRow> source={source} resource={PAGE_MODEL} presentation="workspace"
      columns={[{ field: "title", header: t("editor.titleLabel") }]} textFilterField="title" search={{ box: true }} pane={pane} toolbarActions={toolbarActions}
      rowKey="id" parent="parent" label="title" icon="icon" hasChildren="has_children"
      selectedId={detail?.id} revealRows={revealRows} onRowClick={onSelect}
      draggableRow={(row) => writable && holdsPermission(row, "write") ? pageDragPayload(row) : null}
      dropAccept={KNOWLEDGE_PAGE_DND} canDropOnNode={(_id, row) => writable && holdsPermission(row, "write")}
      onNodeDrop={(id, payload) => {
        if (typeof payload.data !== "string" || payload.data === id || !writable) return;
        setMoveError(null);
        void movePage(payload.data, id).catch(setMoveError);
      }}
      insertion={creating && writable ? { parentId: creating.parent,
        node: <InlineTextAction key={creating.kind + (creating.parent ?? "")} open
          onOpenChange={(open) => { if (!open) onCancel(); }}
          onSubmit={async (title) => {
            const id = await createPage({ ...creating, title });
            if (!id) throw new Error(t("notes.createError"));
            onCreated(id, creating);
          }}
          inputLabel={t("newPage.titleLabel")} submitLabel={t("newPage.create")}
          placeholder={creating.kind === "folder" ? t("newPage.folderPlaceholder") : t("newPage.notePlaceholder")}
          formClassName="w-full items-center gap-1" inputClassName="min-w-0 flex-1" /> } : null}
      emptyContent={t("nav.noMatches")} />
  </>;
}
