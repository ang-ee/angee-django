import { describe, expect, test } from "vitest";

import type { KnowledgePageRow } from "./documents";
import { pageIdByTitle, pageTreeRows, removedPages } from "./page-rows";

function page(id: string, title: string, overrides: Partial<KnowledgePageRow> = {}): KnowledgePageRow {
  return {
    id,
    title,
    kind: "note",
    icon: "",
    vault: "vlt_1",
    parent: null,
    permissions: ["write", "delete"],
    updated_at: "2026-01-01T00:00:00Z",
    created_by_label: "Alex",
    is_trashed: false,
    trash_reason: "",
    trashed_by_label: null,
    ...overrides,
  } as unknown as KnowledgePageRow;
}

const PAGES = [
  page("pg_folder", "Projects", { kind: "folder", is_trashed: true, trash_reason: "Superseded" }),
  page("pg_child", "Plan", { parent: "pg_folder", is_trashed: true, trash_reason: "Superseded" }),
  page("pg_alone", "Draft", { is_trashed: true }),
  page("pg_live", "Notes"),
  page("pg_elsewhere", "Elsewhere", { vault: "vlt_2", is_trashed: true }),
];

describe("knowledge page rows", () => {
  test("the navigator tree leaves trashed pages out", () => {
    expect(pageTreeRows(PAGES, "vlt_1").map((row) => row.id)).toEqual(["pg_live"]);
  });

  test("a wikilink to a trashed page is broken", () => {
    expect(pageIdByTitle(PAGES, "vlt_1", "plan")).toBeNull();
    expect(pageIdByTitle(PAGES, "vlt_1", "notes")).toBe("pg_live");
  });

  test("the removed list names each trashed subtree once, by its top page", () => {
    expect(removedPages(PAGES, "vlt_1").map((row) => row.id)).toEqual(["pg_alone", "pg_folder"]);
  });
});
