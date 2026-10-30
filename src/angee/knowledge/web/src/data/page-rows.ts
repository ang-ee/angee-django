import type { DndPayload } from "@angee/ui";
import type { KnowledgePageRow } from "./documents";

export type KnowledgeTreeRow = Pick<KnowledgePageRow, "id" | "title" | "parent" | "kind" | "icon" | "permissions" | "has_children">;
export const KNOWLEDGE_PAGE_DND = "knowledge.page";

/** Drag identity; the hierarchy owner validates moves. */
export function pageDragPayload(row: KnowledgeTreeRow): DndPayload<string> {
  return { type: KNOWLEDGE_PAGE_DND, data: row.id };
}

/** Registry glyph for a page's kind. */
export function pageIcon(kind: string): string {
  switch (kind) {
    case "folder": return "folder";
    case "template": return "template";
    default: return "note";
  }
}
