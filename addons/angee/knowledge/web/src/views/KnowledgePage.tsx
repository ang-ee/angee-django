import { useCallback, useMemo, type ReactElement } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";

import {
  EmptyState,
  LoadingPanel,
  RelationPicker,
  recordPath,
  TreeView,
  WikilinkProvider,
  useChatterContent,
  useConfirm,
  usePrimaryPane,
  useScopedTreeExplorer,
  type ChatterTab,
  type WikilinkResolver,
} from "@angee/ui";
import { useAuthoredQuery } from "@angee/ui";

import {
  KnowledgePage as KnowledgePageQuery,
  KnowledgePages,
  KnowledgeVaults,
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
  const params = useParams({ strict: false });
  const openPageId =
    "id" in params && typeof params.id === "string" ? params.id : null;
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

  // A page write retitles its tree node; refetch the navigator set.
  const handleSaved = useCallback(() => {
    void refetchPages();
  }, [refetchPages]);

  const confirm = useConfirm();
  const { busy: actionsBusy, createPage, deletePage, movePage } =
    usePageActions({ onChanged: handleSaved });
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
  const explorer = useScopedTreeExplorer({
    roots: vaults,
    getRootId: getVaultId,
    getRootLabel: getVaultLabel,
    getTreeRows: getVaultTreeRows,
    selectedId: openPageId,
    selectedRootId: activePage?.vault ?? null,
  });
  const vaultId = explorer.rootId;
  const vaultOptions = explorer.rootOptions;
  // New pages land inside the active scope when it is a folder, else at the root.
  const handleNewPage = useCallback(
    async (kind: NewPageKind, title: string) => {
      if (!vaultId) return;
      const parent = activePage?.kind === "folder" ? openPageId : null;
      const id = await createPage({ vault: vaultId, title, kind, parent });
      if (id) openPage(id);
    },
    [vaultId, activePage, openPageId, createPage, openPage],
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
      title: t("knowledge.page.deleteConfirmTitle", { title: activePage.title }),
      body: t("knowledge.page.deleteConfirmBody"),
      confirm: t("knowledge.page.deleteConfirm"),
      danger: true,
    });
    if (!ok) return;
    await deletePage(activePage.id);
    closePage();
  }, [activePage, confirm, deletePage, closePage, t]);

  const { selectedId, setRootId, treeRows } = explorer;
  // A `[[wikilink]]` resolves to a page by title within the vault; clicking it
  // opens that page, or renders broken when nothing matches.
  const resolveWikilink = useCallback<WikilinkResolver>(
    (target) => {
      const id = pageIdByTitle(pages, vaultId, target);
      return id
        ? { broken: false, onActivate: () => openPage(id) }
        : { broken: true };
    },
    [pages, vaultId, openPage],
  );

  const hasVaults = vaults.length > 0;

  // The vault switcher + page-tree navigator lives in the shell's primary pane.
  const navigator = useMemo(
    () => (
      <div
        role="navigation"
        aria-label={t("knowledge.nav.label")}
        className="flex h-full flex-col gap-2 p-2"
      >
        <RelationPicker
          aria-label={t("knowledge.vault.label")}
          value={vaultId}
          options={vaultOptions}
          placeholder={t("knowledge.vault.placeholder")}
          searchPlaceholder={t("knowledge.vault.searchPlaceholder")}
          onChange={(value) => {
            setRootId(value);
            closePage();
          }}
          create={{ resource: "Vault" }}
          onCreated={(id) => {
            void refetchVaults();
            setRootId(id);
            closePage();
          }}
        />
        <TreeView<KnowledgeTreeRow>
          rows={treeRows}
          parent="parent"
          label="title"
          rowKey="id"
          icon="icon"
          selectedId={selectedId}
          onSelect={(row) => openPage(row.id)}
          draggableRow={pageDragPayload}
          dropAccept={KNOWLEDGE_PAGE_DND}
          onNodeDrop={(nodeId, payload) =>
            handlePageDrop(nodeId, payload.data as PageDragData)
          }
          className="min-h-0 flex-1 overflow-auto"
        />
        <NewPageControl busy={actionsBusy} onCreate={handleNewPage} />
      </div>
    ),
    [
      t,
      vaultId,
      vaultOptions,
      setRootId,
      closePage,
      refetchVaults,
      treeRows,
      selectedId,
      openPage,
      handlePageDrop,
      actionsBusy,
      handleNewPage,
    ],
  );
  // Publish the navigator only once vaults exist; the loading/empty states own
  // the whole surface and the shell falls back to its own primary content.
  usePrimaryPane(hasVaults ? navigator : null);

  // The backlinks rail rides along as an additive secondary (chatter) tab.
  const backlinksTabs = useMemo<readonly ChatterTab[]>(
    () =>
      detail
        ? [
            {
              id: "backlinks",
              label: t("knowledge.backlinks.heading"),
              icon: "link",
              children: (
                <BacklinksPanel
                  backlinks={detail?.backlinks ?? []}
                  onOpen={openPage}
                />
              ),
            },
          ]
        : [],
    [detail, openPage, t],
  );
  const chatter = useMemo(
    () => (backlinksTabs.length > 0 ? { tabs: backlinksTabs } : null),
    [backlinksTabs],
  );
  useChatterContent(chatter);

  if (vaultsQuery.fetching && vaults.length === 0) {
    return <LoadingPanel message={t("knowledge.loading")} />;
  }
  if (vaults.length === 0) {
    return (
      <EmptyState
        fill
        icon="vault"
        title={
          vaultsQuery.error
            ? t("knowledge.vaults.unavailableTitle")
            : t("knowledge.vaults.emptyTitle")
        }
        description={
          vaultsQuery.error?.message ?? t("knowledge.vaults.emptyDescription")
        }
      />
    );
  }

  return (
    <WikilinkProvider resolve={resolveWikilink}>
      {openPageId ? (
        detail && detail.id === openPageId ? (
          <PageEditor
            key={openPageId}
            detail={detail}
            onSaved={handleSaved}
            onDelete={handleDeletePage}
          />
        ) : detailQuery.fetching || detail ? (
          <LoadingPanel message={t("knowledge.page.loading")} />
        ) : (
          <EmptyState
            fill
            icon="note"
            title={t("knowledge.page.notFoundTitle")}
            description={t("knowledge.page.notFoundDescription")}
          />
        )
      ) : (
        <EmptyState
          fill
          icon="note"
          title={t("knowledge.page.selectTitle")}
          description={t("knowledge.page.selectDescription")}
        />
      )}
    </WikilinkProvider>
  );
}
