import {
  useCallback,
  useMemo,
  useState,
  type ReactElement,
} from "react";
import { useNavigate, useParams } from "@tanstack/react-router";

import {
  EmptyState,
  Explorer,
  LoadingPanel,
  RelationPicker,
  recordPath,
  TreeView,
  WikilinkProvider,
  useConfirm,
  type WikilinkResolver,
} from "@angee/base";
import { useAuthoredQuery, useResourceRecord } from "@angee/sdk";

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

/** The Django model label backing the page crumb. */
const PAGE_MODEL = "knowledge.Page";
// One safety-capped read each of vaults/pages; the browser scopes the set
// client-side so the navigator and reader share one fetch.
const KNOWLEDGE_LIST_LIMIT = 500;

/**
 * The knowledge wiki: an `Explorer` of a vault switcher + page-tree navigator,
 * the open page's reader, and a backlinks aside. Vaults/pages load once; the
 * switcher and tree drive client-side scoping, and selecting a page reads it.
 */
export function KnowledgePage(): ReactElement {
  const t = useKnowledgeT();
  const variables = useMemo(
    () => ({ pagination: { offset: 0, limit: KNOWLEDGE_LIST_LIMIT } }),
    [],
  );
  const vaultsQuery = useAuthoredQuery(KnowledgeVaults, variables);
  const pagesQuery = useAuthoredQuery(KnowledgePages, variables);

  const vaults = vaultsQuery.data?.vaults.results ?? [];
  const pages = pagesQuery.data?.pages.results ?? [];

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
  const detail = detailQuery.data?.page ?? null;

  const [pinnedVaultId, setPinnedVaultId] = useState<string | null>(null);
  const vaultId = pinnedVaultId ?? vaults[0]?.id ?? "";
  const vaultOptions = useMemo(
    () => vaults.map((vault) => ({ value: vault.id, label: vault.name })),
    [vaults],
  );

  // A page write retitles its tree node; refetch the navigator set.
  const handleSaved = useCallback(() => {
    void pagesQuery.refetch();
  }, [pagesQuery]);

  const confirm = useConfirm();
  const pageActions = usePageActions({ onChanged: handleSaved });
  const activePage = pageById(pages, openPageId);
  // New pages land inside the active scope when it is a folder, else at the root.
  const handleNewPage = useCallback(
    async (kind: NewPageKind, title: string) => {
      if (!vaultId) return;
      const parent = activePage?.kind === "folder" ? openPageId : null;
      const id = await pageActions.createPage({ vault: vaultId, title, kind, parent });
      if (id) openPage(id);
    },
    [vaultId, activePage, openPageId, pageActions, openPage],
  );
  // Drop a page onto another to reparent it; the guard blocks dropping a page
  // onto itself or its own descendant (which would orphan the subtree).
  const handlePageDrop = useCallback(
    (targetId: string, dragged: PageDragData) => {
      if (isSelfOrAncestor(pages, dragged.id, targetId)) return;
      void pageActions.movePage(dragged.id, targetId);
    },
    [pages, pageActions],
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
    await pageActions.deletePage(activePage.id);
    closePage();
  }, [activePage, confirm, pageActions, closePage, t]);

  const treeRows = useMemo(
    () => pageTreeRows(pages, vaultId),
    [pages, vaultId],
  );
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

  const navigator = (
    <div className="flex h-full flex-col gap-2 p-2">
      <RelationPicker
        aria-label={t("knowledge.vault.label")}
        value={vaultId}
        options={vaultOptions}
        placeholder={t("knowledge.vault.placeholder")}
        searchPlaceholder={t("knowledge.vault.searchPlaceholder")}
        onChange={(value) => {
          setPinnedVaultId(value);
          closePage();
        }}
        create={{ model: "Vault" }}
        onCreated={(id) => {
          void vaultsQuery.refetch();
          setPinnedVaultId(id);
          closePage();
        }}
      />
      <TreeView<KnowledgeTreeRow>
        rows={treeRows}
        parent="parent"
        label="title"
        rowKey="id"
        icon="icon"
        selectedId={openPageId ?? undefined}
        onSelect={(row) => openPage(row.id)}
        draggableRow={pageDragPayload}
        dropAccept={KNOWLEDGE_PAGE_DND}
        onNodeDrop={(nodeId, payload) =>
          handlePageDrop(nodeId, payload.data as PageDragData)
        }
        className="min-h-0 flex-1 overflow-auto"
      />
      <NewPageControl busy={pageActions.busy} onCreate={handleNewPage} />
    </div>
  );

  return (
    <WikilinkProvider resolve={resolveWikilink}>
      <Explorer
        autoSave="knowledge.browser"
        navigator={navigator}
        aside={<BacklinksPanel backlinks={detail?.backlinks ?? []} onOpen={openPage} />}
      >
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
      </Explorer>
    </WikilinkProvider>
  );
}

/** The record crumb for `/knowledge/$id` — the page's title. */
export function PageCrumb({ id }: { id: string }): ReactElement {
  const t = useKnowledgeT();
  const { fetching, record } = useResourceRecord(PAGE_MODEL, id || null, {
    enabled: id !== "",
    fields: ["title"],
  });
  const title = typeof record?.title === "string" ? record.title.trim() : "";
  if (fetching) return <>…</>;
  return <>{title || t("knowledge.page.crumbFallback")}</>;
}
