import { holdsPermission } from "@angee/metadata";
import { useAuthoredQuery, useInvalidateAuthoredModels } from "@angee/refine";
import { useCallback, useMemo, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";

import {
  EmptyState, LoadingPanel, RemovedDisclosure, RemovedItem, ScopedExplorerPane, TRASH_PERMISSION, TreeView, WikilinkProvider, useChatterContent, useRouteHref, useRouteRecordId, type ChatterTab, type ScopedExplorerController, type WikilinkResolver } from "@angee/ui";

import {
  KnowledgePage as KnowledgePageQuery,
  KnowledgePages,
  KnowledgeVaults,
  KNOWLEDGE_LIST_LIMIT,
  PAGE_MODEL,
  PAGE_READ_MODELS,
  type Backlink,
  type KnowledgePageRow,
} from "../data/documents";
import {
  KNOWLEDGE_PAGE_DND,
  isSelfOrAncestor,
  pageById,
  pageDragPayload,
  pageIdByTitle,
  pageTreeRows,
  removedPages,
  type KnowledgeTreeRow,
  type PageDragData,
} from "../data/page-rows";
import { usePageActions } from "../data/use-page-actions";
import { KnowledgePageView } from "../KnowledgePageView";
import { BacklinksPanel } from "./BacklinksPanel";
import { NewPageControl, type NewPageKind } from "./NewPageControl";
import { useKnowledgeT } from "../i18n";

// One safety-capped read each of vaults/pages; the browser scopes the set
// client-side so the navigator and reader share one fetch.
const EMPTY_BACKLINKS: readonly Backlink[] = [];

type KnowledgeExplorerController = ScopedExplorerController<
  { id: string; name: string },
  KnowledgeTreeRow
>;

/**
 * The knowledge wiki reader. The vault switcher + page-tree navigator publishes
 * into the shell's primary pane (`usePrimaryPane`) and a backlinks tab into the
 * shell's secondary chatter (`useChatterContent`); the page itself renders only
 * the open page's reader. Vaults/pages load once; the switcher and tree drive
 * client-side scoping, and selecting a page reads it.
 */
export function KnowledgePage(): ReactElement {
  const t = useKnowledgeT();
  const variables = useMemo(
    () => ({ offset: 0, limit: KNOWLEDGE_LIST_LIMIT }),
    [],
  );
  const invalidateModels = useInvalidateAuthoredModels();
  const vaultsQuery = useAuthoredQuery(KnowledgeVaults, variables, { models: ["knowledge.Vault"] });
  const pagesQuery = useAuthoredQuery(KnowledgePages, variables, { models: [PAGE_MODEL] });

  const vaults = vaultsQuery.data?.vaults ?? [];
  const pages = pagesQuery.data?.pages ?? [];

  // The open page is route state: `/knowledge/$id` reads that page into the
  // content + aside; `/knowledge` is the empty reader.
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const openPageId = useRouteRecordId() ?? null;
  const openPage = useCallback(
    (id: string) => {
      void navigate({ to: routeHref("knowledge.page", { id }) });
    },
    [navigate, routeHref],
  );
  const closePage = useCallback(() => {
    void navigate({ to: routeHref("knowledge.home") });
  }, [navigate, routeHref]);

  const detailVariables = useMemo(
    () => ({ id: openPageId ?? "" }),
    [openPageId],
  );
  const detailQuery = useAuthoredQuery(KnowledgePageQuery, detailVariables, {
    enabled: openPageId !== null,
    models: PAGE_READ_MODELS,
  });
  const detail = detailQuery.data?.pages_by_pk ?? null;
  const pageNotFound = !detailQuery.isPending && detailQuery.data?.pages_by_pk === null;
  const detailBacklinks = detail?.backlinks ?? EMPTY_BACKLINKS;
  const backlinkSignature = useMemo(
    () => backlinksSignature(detailBacklinks),
    [detailBacklinks],
  );
  const stableBacklinks = useMemo(
    () => detailBacklinks,
    [backlinkSignature],
  );

  const { busy: actionsBusy, createPage, trashPage, restorePage, movePage } =
    usePageActions();
  const activePage = pageById(pages, openPageId);
  // Stable accessors: the explorer memoizes `rootOptions`/`treeRows` on these, and
  // the navigator published into the shell's primary pane keys on those memos.
  const getVaultId = useCallback((vault: { id: string }) => vault.id, []);
  const getVaultLabel = useCallback(
    (vault: { name: string }) => vault.name,
    [],
  );
  const getVaultTreeRows = useCallback(
    (rootId: string) => pageTreeRows(pages, rootId),
    [pages],
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
      create: { resource: "knowledge.Vault" },
      onCreated: () => {
        invalidateModels(["knowledge.Vault"]);
        closePage();
      },
    }),
    [closePage, invalidateModels, t],
  );
  const renderTree = useCallback(
    (controller: KnowledgeExplorerController) => (
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
        className="min-h-0 flex-1 overflow-auto"
      />
    ),
    [handlePageDrop, openPage],
  );
  const renderNavigatorFooter = useCallback(
    (controller: KnowledgeExplorerController) => {
      const createInScope = async (
        kind: NewPageKind,
        title: string,
      ): Promise<void> => {
        if (!controller.rootId) return;
        const parent = activePage?.kind === "folder" ? openPageId : null;
        const id = await createPage({
          vault: controller.rootId,
          title,
          kind,
          parent,
        });
        if (id) openPage(id);
      };
      return (
        <>
          <RemovedPages
            pages={removedPages(pages, controller.rootId ?? "")}
            busy={actionsBusy}
            onRestore={restorePage}
          />
          <NewPageControl busy={actionsBusy} onCreate={createInScope} />
        </>
      );
    },
    [actionsBusy, activePage, createPage, openPage, openPageId, pages, restorePage],
  );

  // The backlinks rail rides along as an additive secondary (chatter) tab.
  const backlinksTabs = useMemo<readonly ChatterTab[]>(
    () =>
      detail
        ? [
            {
              id: "backlinks",
              label: t("backlinks.heading"),
              icon: "link",
              children: (
                <BacklinksPanel
                  backlinks={stableBacklinks}
                  onOpen={openPage}
                />
              ),
            },
          ]
        : [],
    [detail?.id, openPage, stableBacklinks, t],
  );
  const chatter = useMemo(
    () => (backlinksTabs.length > 0 ? { tabs: backlinksTabs } : null),
    [backlinksTabs],
  );
  useChatterContent(chatter);

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
      renderNavigatorFooter={renderNavigatorFooter}
      loading={vaultsQuery.isFetching && vaults.length === 0}
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
            vaultsQuery.error?.message ?? t("vaults.emptyDescription")
          }
        />
      }
    >
      {(controller) => (
        <KnowledgeExplorerContent
          controller={controller}
          pages={pages}
          openPageId={openPageId}
          onOpenPage={openPage}
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
  pages,
  openPageId,
  onOpenPage,
  pageNotFound,
  onDeletePage,
}: {
  controller: KnowledgeExplorerController;
  pages: readonly KnowledgePageRow[];
  openPageId: string | null;
  onOpenPage: (id: string) => void;
  pageNotFound: boolean;
  /** Present only when the reader may delete — and so trash — the open page. */
  onDeletePage?: () => Promise<void>;
}): ReactElement {
  const t = useKnowledgeT();
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

function backlinksSignature(backlinks: readonly Backlink[]): string {
  return backlinks
    .map((backlink) =>
      [backlink.page, backlink.title, backlink.display_text ?? ""].join("\u0000"),
    )
    .join("\u0001");
}
