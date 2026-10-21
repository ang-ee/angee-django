import { ResourceQuery, holdsPermission, useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery, useInvalidateAuthoredModels } from "@angee/refine";
import { useCallback, useMemo, useState, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";

import {
  EmptyState, ErrorBanner, InlineTextAction, LoadingPanel, PaneToolbarMenu, RemovedDisclosure, RemovedItem, ScopedExplorerPane,
  SearchInput, TRASH_PERMISSION, TreeView, WikilinkProvider, errorMessage, useBreadcrumbCollectionLink, useBreadcrumbLeafLabel,
  useAppRuntime, useRouteHref, useRouteRecordId, type RecordNavigation, type ScopedExplorerController, type WikilinkResolver,
} from "@angee/ui";

import {
  KnowledgePage as KnowledgePageQuery,
  KnowledgePages,
  KnowledgeVaults,
  KNOWLEDGE_LIST_LIMIT,
  PAGE_MODEL,
  PAGE_READ_MODELS,
  type KnowledgePageRow,
} from "../data/documents";
import {
  KNOWLEDGE_PAGE_DND,
  childPages,
  isSelfOrAncestor,
  pageById,
  pageDragPayload,
  pageIdByTitle,
  pageOrder,
  pagePath,
  pageTreeRows,
  removedPages,
  type KnowledgeTreeRow,
  type PageDragData,
} from "../data/page-rows";
import { usePageActions } from "../data/use-page-actions";
import { KnowledgePageView, type KnowledgePageFrame } from "../KnowledgePageView";
import { useKnowledgeT } from "../i18n";

// One safety-capped read each of vaults/pages; a mounted collection's predicate
// is applied by the query owner before the navigator groups its pages by vault.
type NewPageKind = "note" | "folder";

type KnowledgeExplorerController = ScopedExplorerController<
  { id: string; name: string },
  KnowledgeTreeRow
>;

/**
 * The knowledge wiki on the record-page shape. Navigator: the vault picker,
 * a tree filter, one labeled "New" menu and the vault's overflow in the pane
 * toolbar; a new page is typed where it will live, as a row in the tree.
 * Subject: the open page framed by the command band and the record header.
 * The page's backlinks and bindings are Related groups on its record.
 */
export function KnowledgePage(): ReactElement {
  const t = useKnowledgeT();
  const variables = useMemo(
    () => ({ offset: 0, limit: KNOWLEDGE_LIST_LIMIT }),
    [],
  );
  const invalidateModels = useInvalidateAuthoredModels();
  const { defaultResourceFilter } = useAppRuntime();
  const metadata = useModelMetadata(PAGE_MODEL);
  const routeFilter = defaultResourceFilter?.resource === PAGE_MODEL ? defaultResourceFilter.filter : undefined;
  const scope = useMemo(() => {
    if (!routeFilter) return { where: undefined, error: null };
    try {
      if (!metadata) throw new Error(t("vaults.unavailableTitle"));
      return { where: ResourceQuery.from(metadata).toWhere(routeFilter), error: null };
    } catch (error) {
      return { where: undefined, error };
    }
  }, [metadata, routeFilter, t]);
  const vaultsQuery = useAuthoredQuery(KnowledgeVaults, variables, { models: ["knowledge.Vault"] });
  const pagesQuery = useAuthoredQuery(KnowledgePages, { ...variables, where: scope.where }, {
    models: [PAGE_MODEL], enabled: scope.error === null,
  });

  const pages = pagesQuery.data?.pages ?? [];
  // A mounted page collection keeps the same reader and only offers roots
  // represented in that collection. The ordinary wiki retains empty vaults.
  const vaults = useMemo(() => {
    const all = vaultsQuery.data?.vaults ?? [];
    if (!routeFilter) return all;
    const ids = new Set(pages.map((page) => page.vault));
    return all.filter((vault) => ids.has(vault.id));
  }, [pages, routeFilter, vaultsQuery.data]);

  // The open page is route state: `/knowledge/$id` reads that page into the
  // content + aside; `/knowledge` is the empty reader.
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const homeHref = routeHref("knowledge.home");
  const openPageId = useRouteRecordId() ?? null;
  const openPage = useCallback(
    (id: string) => {
      void navigate({ to: routeHref("knowledge.page", { id }) });
    },
    [navigate, routeHref],
  );
  const closePage = useCallback(() => {
    void navigate({ to: homeHref });
  }, [navigate, homeHref]);

  const detailVariables = useMemo(
    () => ({ id: openPageId ?? "" }),
    [openPageId],
  );
  const detailQuery = useAuthoredQuery(KnowledgePageQuery, detailVariables, {
    enabled: openPageId !== null,
    models: PAGE_READ_MODELS,
  });
  const pageNotFound = !detailQuery.isPending && detailQuery.data?.pages_by_pk === null;
  const { busy: actionsBusy, createPage, trashPage, restorePage, movePage } =
    usePageActions();
  const activePage = pageById(pages, openPageId);
  // The breadcrumb names the open page; the collection crumb returns to the wiki.
  useBreadcrumbLeafLabel(activePage?.title ?? null);
  useBreadcrumbCollectionLink(homeHref, openPageId ? homeHref : null);
  // Stable accessors: the explorer memoizes `rootOptions`/`treeRows` on these, and
  // the navigator published into the shell's primary pane keys on those memos.
  const getVaultId = useCallback((vault: { id: string }) => vault.id, []);
  const getVaultLabel = useCallback(
    (vault: { name: string }) => vault.name,
    [],
  );
  // The tree filter keeps a matching page and its ancestors, so a hit stays reachable.
  const [filter, setFilter] = useState("");
  const [filtering, setFiltering] = useState(false);
  const getVaultTreeRows = useCallback(
    (rootId: string) => {
      const rows = pageTreeRows(pages, rootId);
      const wanted = filter.trim().toLowerCase();
      if (!wanted) return rows;
      const byId = new Map(rows.map((row) => [row.id, row]));
      const keep = new Set<string>();
      for (const row of rows) {
        if (!row.title.toLowerCase().includes(wanted)) continue;
        let current: KnowledgeTreeRow | undefined = row;
        while (current && !keep.has(current.id)) {
          keep.add(current.id);
          current = current.parent ? byId.get(current.parent) : undefined;
        }
      }
      return rows.filter((row) => keep.has(row.id));
    },
    [filter, pages],
  );
  // Drop a page onto another to reparent it; the guard blocks dropping a page
  // onto itself or its own descendant (which would orphan the subtree).
  const handlePageDrop = useCallback(
    (targetId: string, dragged: PageDragData) => {
      if (isSelfOrAncestor(pages, dragged.id, targetId)) return;
      void movePage(dragged.id, targetId);
    },
    [pages, movePage],
  );
  // Removal is the shared trash: the page and the pages below it leave every
  // reader who cannot delete them, and wait in the vault's "Removed" list.
  const handleTrashPage = useCallback(async () => {
    if (!activePage) return;
    if (await trashPage(activePage.id, activePage.title)) closePage();
  }, [activePage, trashPage, closePage]);
  const canTrashActivePage = holdsPermission(activePage, TRASH_PERMISSION);
  const vaultRootPicker = useMemo(
    () => ({
      "aria-label": t("vault.label"),
      placeholder: t("vault.placeholder"),
      searchPlaceholder: t("vault.searchPlaceholder"),
      create: routeFilter ? undefined : { resource: "knowledge.Vault" },
      onCreated: () => {
        invalidateModels(["knowledge.Vault"]);
        closePage();
      },
    }),
    [closePage, invalidateModels, routeFilter, t],
  );
  // A new page is typed where it will live: under the open folder, beside the
  // open page, or at the vault root. The row closes on submit and the page opens.
  const [creating, setCreating] = useState<NewPageKind | null>(null);
  const insertParent = activePage
    ? activePage.kind === "folder" ? activePage.id : (activePage.parent ?? null)
    : null;
  const renderTree = useCallback(
    (controller: KnowledgeExplorerController) => {
      const createInScope = async (title: string): Promise<void> => {
        if (!controller.rootId || !creating) return;
        const id = await createPage({ vault: controller.rootId, title, kind: creating, parent: insertParent });
        setCreating(null);
        if (id) openPage(id);
      };
      return (
        <>
          {filtering ? (
            <SearchInput
              autoFocus
              size="sm"
              aria-label={t("nav.search")}
              placeholder={t("nav.searchPlaceholder")}
              value={filter}
              onChange={(event) => setFilter(event.currentTarget.value)}
              onClear={() => { setFilter(""); setFiltering(false); }}
              className="mx-1"
            />
          ) : null}
          <TreeView<KnowledgeTreeRow>
            rows={controller.treeRows}
            parent="parent"
            label="title"
            rowKey="id"
            icon="icon"
            selectedId={controller.selectedId}
            onSelect={(row) => openPage(row.id)}
            draggableRow={pageDragPayload}
            dropAccept={KNOWLEDGE_PAGE_DND}
            onNodeDrop={(nodeId, payload) =>
              handlePageDrop(nodeId, payload.data as PageDragData)
            }
            insertion={creating ? {
              parentId: insertParent,
              node: (
                <InlineTextAction
                  key={creating}
                  open
                  onOpenChange={(next) => { if (!next) setCreating(null); }}
                  busy={actionsBusy}
                  onSubmit={(title) => void createInScope(title)}
                  inputLabel={t("newPage.titleLabel")}
                  placeholder={creating === "folder" ? t("newPage.folderPlaceholder") : t("newPage.notePlaceholder")}
                  submitLabel={t("newPage.create")}
                  formClassName="w-full items-center gap-1"
                  inputClassName="min-w-0 flex-1"
                />
              ),
            } : null}
            emptyContent={filter ? t("nav.noMatches") : undefined}
            className="min-h-0 flex-1 overflow-auto"
          />
        </>
      );
    },
    [actionsBusy, createPage, creating, filter, filtering, handlePageDrop, insertParent, openPage, t],
  );
  const renderNavigatorFooter = useCallback(
    (controller: KnowledgeExplorerController) => (
      <RemovedPages
        pages={removedPages(pages, controller.rootId ?? "")}
        busy={actionsBusy}
        onRestore={restorePage}
      />
    ),
    [actionsBusy, pages, restorePage],
  );
  const explorerToolbar = useCallback(
    (controller: KnowledgeExplorerController) => ({
      search: true,
      onSearch: () => setFiltering((open) => !open),
      actions: [{
        icon: "plus", label: t("newPage.menu"), text: true, disabled: actionsBusy || !controller.rootId,
        items: [
          { icon: "note", label: t("newPage.page"), onClick: () => setCreating("note") },
          { icon: "folder", label: t("newPage.folder"), onClick: () => setCreating("folder") },
        ],
      }],
      overflow: (
        <PaneToolbarMenu items={[
          { icon: "vault", label: t("vault.settings"), onClick: () => void navigate({ to: routeHref("knowledge.settings") }) },
        ]} />
      ),
    }),
    [actionsBusy, navigate, routeHref, t],
  );

  if (scope.error || pagesQuery.error) {
    return <ErrorBanner description={errorMessage(scope.error ?? pagesQuery.error, t("vaults.unavailableTitle"))} />;
  }
  return (
    <ScopedExplorerPane<{ id: string; name: string }, KnowledgeTreeRow>
      roots={vaults}
      getRootId={getVaultId}
      getRootLabel={getVaultLabel}
      getTreeRows={getVaultTreeRows}
      selectedId={openPageId}
      selectedRootId={activePage?.vault ?? null}
      navigatorLabel={t("nav.label")}
      rootPicker={vaultRootPicker}
      onRootChange={closePage}
      renderTree={renderTree}
      toolbar={explorerToolbar}
      renderNavigatorFooter={renderNavigatorFooter}
      loading={(vaultsQuery.isFetching || pagesQuery.isFetching) && vaults.length === 0}
      loadingContent={<LoadingPanel message={t("loading")} />}
      emptyContent={
        <EmptyState
          fill
          icon="vault"
          title={
            vaultsQuery.error
              ? t("vaults.unavailableTitle")
              : t("vaults.emptyTitle")
          }
          description={
            vaultsQuery.error ? errorMessage(vaultsQuery.error, t("vaults.unavailableTitle")) : t("vaults.emptyDescription")
          }
        />
      }
    >
      {(controller) => (
        <KnowledgeExplorerContent
          controller={controller}
          vaultName={vaults.find((vault) => vault.id === controller.rootId)?.name ?? ""}
          pages={pages}
          activePage={activePage}
          openPageId={openPageId}
          onOpenPage={openPage}
          onNewPage={() => setCreating("note")}
          pageNotFound={pageNotFound}
          {...(canTrashActivePage ? { onDeletePage: handleTrashPage } : {})}
        />
      )}
    </ScopedExplorerPane>
  );
}

/** The vault's "Removed (n)": each trashed subtree once, restorable by its deleters. */
function RemovedPages({ pages, busy, onRestore }: {
  pages: readonly KnowledgePageRow[];
  busy: boolean;
  onRestore: (id: string) => Promise<boolean>;
}): ReactElement | null {
  return (
    <RemovedDisclosure count={pages.length} className="px-2">
      {pages.map((page) => (
        <RemovedItem
          key={page.id}
          label={page.title}
          trashedByLabel={page.trashed_by_label}
          trashReason={page.trash_reason}
          busy={busy}
          {...(holdsPermission(page, TRASH_PERMISSION) ? { onRestore: () => void onRestore(page.id) } : {})}
        />
      ))}
    </RemovedDisclosure>
  );
}

function KnowledgeExplorerContent({
  controller,
  vaultName,
  pages,
  activePage,
  openPageId,
  onOpenPage,
  onNewPage,
  pageNotFound,
  onDeletePage,
}: {
  controller: KnowledgeExplorerController;
  vaultName: string;
  pages: readonly KnowledgePageRow[];
  activePage: KnowledgePageRow | null;
  openPageId: string | null;
  onOpenPage: (id: string) => void;
  onNewPage: () => void;
  pageNotFound: boolean;
  /** Present only when the reader may delete — and so trash — the open page. */
  onDeletePage?: () => Promise<void>;
}): ReactElement {
  const t = useKnowledgeT();
  const vaultId = controller.rootId ?? "";
  // A `[[wikilink]]` resolves to a page by title within the vault; clicking it
  // opens that page, or renders broken when nothing matches.
  const resolveWikilink = useCallback<WikilinkResolver>(
    (target) => {
      const id = pageIdByTitle(pages, controller.rootId, target);
      return id
        ? { broken: false, onActivate: () => onOpenPage(id) }
        : { broken: true };
    },
    [controller.rootId, onOpenPage, pages],
  );
  // The pager walks the vault in tree order.
  const order = useMemo(() => pageOrder(pages, vaultId), [pages, vaultId]);
  const navigation = useMemo<RecordNavigation | null>(() => {
    if (!openPageId) return null;
    const index = order.indexOf(openPageId);
    if (index < 0) return { total: order.length };
    const prev = order[index - 1];
    const next = order[index + 1];
    return {
      current: index + 1,
      total: order.length,
      ...(prev ? { onPrev: () => onOpenPage(prev) } : {}),
      ...(next ? { onNext: () => onOpenPage(next) } : {}),
    };
  }, [onOpenPage, openPageId, order]);
  const frame = useMemo<KnowledgePageFrame>(() => ({
    crumbs: [t("page.eyebrow"), [vaultName, ...pagePath(pages, activePage)].filter(Boolean).join(" / ")].filter(Boolean).join(" · "),
    navigation,
    children: openPageId ? childPages(pages, vaultId, openPageId) : [],
    onOpenPage,
    onNewPageHere: onNewPage,
  }), [activePage, navigation, onNewPage, onOpenPage, openPageId, pages, t, vaultId, vaultName]);

  return (
    <WikilinkProvider resolve={resolveWikilink}>
      {openPageId && pageNotFound ? (
        <EmptyState
          fill
          icon="note"
          title={t("page.notFoundTitle")}
          description={t("page.notFoundDescription")}
        />
      ) : openPageId ? (
        <KnowledgePageView
          pageId={openPageId}
          onDelete={onDeletePage}
          frame={frame}
        />
      ) : (
        <EmptyState
          fill
          icon="note"
          title={t("page.selectTitle")}
          description={t("page.selectDescription")}
        />
      )}
    </WikilinkProvider>
  );
}
