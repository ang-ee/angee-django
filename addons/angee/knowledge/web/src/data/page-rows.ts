import type { DndPayload } from "@angee/ui";

import type { KnowledgePageRow } from "./documents";

// The browser fetches every vault/page once and scopes client-side, so this
// transform owns the projection: a vault's pages → navigator tree rows, folded
// by their (normalised) parent pointer.

/** A page projected for the navigator tree. */
export interface KnowledgeTreeRow extends Record<string, unknown> {
  id: string;
  title: string;
  parent: string;
  icon: string;
}

/** Dnd payload kind for a dragged page; tree nodes accept it to reparent. */
export const KNOWLEDGE_PAGE_DND = "knowledge.page";

/** The body of a dragged-page payload: the page's public id. */
export interface PageDragData {
  id: string;
}

/** Make a tree node draggable: its move payload, keyed by the page's node id. */
export function pageDragPayload(
  row: KnowledgeTreeRow,
): DndPayload<PageDragData> {
  return { type: KNOWLEDGE_PAGE_DND, data: { id: row.id } };
}

/** Registry glyph for a page's kind. */
function pageIcon(kind: string): string {
  switch (kind) {
    case "folder":
      return "folder";
    case "template":
      return "template";
    default:
      return "note";
  }
}

/** Build the navigator rows for one vault's pages outside the trash, folders before notes. */
export function pageTreeRows(
  pages: readonly KnowledgePageRow[],
  vaultId: string,
): KnowledgeTreeRow[] {
  return pages
    .filter((page) => page.vault === vaultId && !page.is_trashed)
    .slice()
    .sort(comparePages)
    .map((page) => ({
      id: page.id,
      title: page.title,
      parent: page.parent ?? "",
      icon: pageIcon(page.kind),
    }));
}

function comparePages(left: KnowledgePageRow, right: KnowledgePageRow): number {
  const leftFolder = left.kind === "folder" ? 0 : 1;
  const rightFolder = right.kind === "folder" ? 0 : 1;
  if (leftFolder !== rightFolder) return leftFolder - rightFolder;
  return left.title.localeCompare(right.title);
}

/** Resolve a `[[wikilink]]` title to a page id within a vault (case-insensitive),
 * mirroring how the backend indexer resolves links against the vault; a trashed
 * page is a broken link. */
export function pageIdByTitle(
  pages: readonly KnowledgePageRow[],
  vaultId: string,
  title: string,
): string | null {
  const wanted = title.trim().toLowerCase();
  const match = pages.find(
    (page) =>
      page.vault === vaultId &&
      !page.is_trashed &&
      page.title.trim().toLowerCase() === wanted,
  );
  return match?.id ?? null;
}

/**
 * The vault's removed pages a manager restores one by one: trashing a folder
 * trashes its subtree with it, so only pages whose parent is not trashed too
 * are listed — restoring one brings back the pages trashed along with it.
 */
export function removedPages(
  pages: readonly KnowledgePageRow[],
  vaultId: string,
): KnowledgePageRow[] {
  const trashed = new Set(
    pages.filter((page) => page.vault === vaultId && page.is_trashed).map((page) => page.id),
  );
  return pages
    .filter((page) => trashed.has(page.id) && !(page.parent && trashed.has(page.parent)))
    .sort((left, right) => left.title.localeCompare(right.title));
}

/** The selected page's list record, by its node id. */
export function pageById(
  pages: readonly KnowledgePageRow[],
  id: string | null,
): KnowledgePageRow | null {
  if (!id) return null;
  return pages.find((page) => page.id === id) ?? null;
}

/**
 * Whether `ancestorId` is `nodeId` itself or one of its ancestors — the guard
 * that stops a drag-move from reparenting a page under its own subtree.
 */
export function isSelfOrAncestor(
  pages: readonly KnowledgePageRow[],
  ancestorId: string,
  nodeId: string,
): boolean {
  if (ancestorId === nodeId) return true;
  const byId = new Map(pages.map((page) => [page.id, page]));
  const seen = new Set<string>();
  let current = byId.get(nodeId);
  while (current) {
    const parentId = current.parent;
    if (!parentId || seen.has(parentId)) return false;
    if (parentId === ancestorId) return true;
    seen.add(parentId);
    current = byId.get(parentId);
  }
  return false;
}
