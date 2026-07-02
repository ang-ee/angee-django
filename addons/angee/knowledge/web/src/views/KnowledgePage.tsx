import { useAuthoredQuery } from "@angee/refine";
import { useCallback, useMemo, type ReactElement } from "react";
import { useNavigate } from "@tanstack/react-router";

import {
  EmptyState, LoadingPanel, ScopedExplorerPane, recordPath, TreeView, WikilinkProvider, useChatterContent, useConfirm, useRouteRecordId, type ChatterTab, type ScopedExplorerController, type WikilinkResolver } from "@angee/ui";

import {
  KnowledgePage as KnowledgePageQuery,
  KnowledgePages,
  KnowledgeVaults,
  type Backlink,
  type KnowledgePageDetail,
  type KnowledgePageRow,
} from "../data/documents";
import {
  KNOWLEDGE_PAGE_DND,
  isSelfOrAncestor,
  pageById,
  pageDragPayload,
  pageIdByTitle,
  pageTreeRows,
  type KnowledgeTreeRow,
  type PageDragData,
} from "../data/page-rows";
import { usePageActions } from "../data/use-page-actions";
import { BacklinksPanel } from "./BacklinksPanel";
import { NewPageControl, type NewPageKind } from "./NewPageControl";
import { PageEditor } from "./PageEditor";
import { useKnowledgeT } from "../i18n";

// One safety-capped read each of vaults/pages; the browser scopes the set
// client-side so the navigator and reader share one fetch.
const KNOWLEDGE_LIST_LIMIT = 500;
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
  const vaultsQuery = useAuthoredQuery(KnowledgeVaults, variables);
  const pagesQuery = useAuthoredQuery(KnowledgePages, variables);

  const vaults = vaultsQuery.data?.vaults ?? [];
  const pages = pagesQuery.data?.pages ?? [];
  // The query `refetch`es are stable (`useCallback`), unlike the result objects;
  // depend on them so handlers/published nodes keep a stable identity.
  const { refetch: refetchVaults } = vaultsQuery;
  const { refetch: refetchPages } = pagesQuery;

  // The open page is route state: `/knowledge/$id` reads that page into the
  // content + aside; `/knowledge` is the empty reader.
  const navigate = useNavigate();
  const openPageId = useRouteRecordId() ?? null;
  const openPage = useCallback(
    (id: string) => {
      void navigate({ to: recordPath("/knowledge", id) });
    },
    [navigate],
  );
  const closePage = useCallback(() => {
    void navigate({ to: "/knowledge" });
  }, [navigate]);

  const detailVariables = useMemo(
    () => ({ id: openPageId ?? "" }),
    [openPageId],
  );
  const detailQuery = useAuthoredQuery(KnowledgePageQuery, detailVariables, {
    enabled: openPageId !== null,
  });
  const detail = detailQuery.data?.pages_by_pk ?? null;
  const detailBacklinks = detail?.backlinks ?? EMPTY_BACKLINKS;
  const backlinkSignature = useMemo(
    () => backlinksSignature(detailBacklinks),
    [detailBacklinks],
  );
  const stableBacklinks = useMemo(
    () => detailBacklinks,
    [backlinkSignature],
  );

  // A title write retitles its tree node; refetch the navigator set.
  const handleTitleSaved = useCallback(() => {
    void refetchPages();
  }, [refetchPages]);

  const confirm = useConfirm();
  const { busy: actionsBusy, createPage, deletePage, movePage } =
    usePageActions({ onChanged: handleTitleSaved });
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
  const handleDeletePage = useCallback(async () => {
    if (!activePage) return;
    const ok = await confirm({
      title: t("page.deleteConfirmTitle", { title: activePage.title }),
      body: t("page.deleteConfirmBody"),
      confirm: t("page.deleteConfirm"),
      danger: true,
    });
    if (!ok) return;
    await deletePage(activePage.id);
    closePage();
  }, [activePage, confirm, deletePage, closePage, t]);
  const vaultRootPicker = useMemo(
    () => ({
      "aria-label": t("vault.label"),
      placeholder: t("vault.placeholder"),
      searchPlaceholder: t("vault.searchPlaceholder"),
      create: { resource: "Vault" },
      onCreated: () => {
        void refetchVaults();
        closePage();
      },
    }),
    [closePage, refetchVaults, t],
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
      return <NewPageControl busy={actionsBusy} onCreate={createInScope} />;
    },
    [actionsBusy, activePage, createPage, openPage, openPageId],
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
      loading={vaultsQuery.fetching && vaults.length === 0}
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
          detail={detail}
          detailFetching={detailQuery.fetching}
          onOpenPage={openPage}
          onTitleSaved={handleTitleSaved}
          onDeletePage={handleDeletePage}
        />
      )}
    </ScopedExplorerPane>
  );
}

function KnowledgeExplorerContent({
  controller,
  pages,
  openPageId,
  detail,
  detailFetching,
  onOpenPage,
  onTitleSaved,
  onDeletePage,
}: {
  controller: KnowledgeExplorerController;
  pages: readonly KnowledgePageRow[];
  openPageId: string | null;
  detail: KnowledgePageDetail | null;
  detailFetching: boolean;
  onOpenPage: (id: string) => void;
  onTitleSaved: () => void;
  onDeletePage: () => Promise<void>;
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
      {openPageId ? (
        detail && detail.id === openPageId ? (
          <PageEditor
            key={openPageId}
            detail={detail}
            onTitleSaved={onTitleSaved}
            onDelete={onDeletePage}
          />
        ) : detailFetching || detail ? (
          <LoadingPanel message={t("page.loading")} />
        ) : (
          <EmptyState
            fill
            icon="note"
            title={t("page.notFoundTitle")}
            description={t("page.notFoundDescription")}
          />
        )
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
