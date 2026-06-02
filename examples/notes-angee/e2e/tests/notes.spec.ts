import type { APIRequestContext } from "@playwright/test";
import { test, expect, roleStatePath, GraphQLClient } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

const NOTES_QUERY = `query Notes {
  notes {
    totalCount
    results { id title }
  }
}`;

interface NotesData {
  notes: { totalCount: number; results: { id: string; title: string }[] };
}

// Stable curated notes from the demo seed (`resources load demo`), all owned by
// alice. The dev stack also bulk-seeds thousands of lorem notes, so these are
// paginated off page 1 — assert each via a title-filtered query (page-order
// independent), never by scanning a single visible page.
const ALICE_ANCHORS = ["Quarterly planning", "Reading list", "Welcome to Angee"];

function notesByTitleQuery(title: string): string {
  // Titles here are trusted test constants with no embedded quotes.
  return `query { notes(filters: { title: { exact: "${title}" } }) {
    totalCount
    results { id title }
  } }`;
}

async function noteIds(request: APIRequestContext): Promise<Set<string>> {
  const result = await new GraphQLClient(request).query<NotesData>(NOTES_QUERY);
  expect(result.errors).toBeUndefined();
  return new Set((result.data?.notes.results ?? []).map((note) => note.id));
}

test.describe("alice — authenticated", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("sees her scoped notes in the UI, reflecting her backend scope", async ({ page, api }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    // The list renders (group rows by default) and its top pager shows a
    // non-zero total — groups when grouped, records when flat.
    await expect(notes.rows.first()).toBeVisible();
    expect(await notes.recordTotal()).toBeGreaterThan(0);

    // Her backend scope is non-empty and scoped to her (isolation is covered
    // separately); the pager total reflects grouping, so don't equate it to the
    // raw record count here.
    const result = await api.query<NotesData>(NOTES_QUERY);
    expect(result.errors).toBeUndefined();
    expect(result.data?.notes.totalCount).toBeGreaterThan(0);
  });

  test("her REBAC scope includes the curated shared notes", async ({ api }) => {
    for (const anchor of ALICE_ANCHORS) {
      const result = await api.query<NotesData>(notesByTitleQuery(anchor));
      expect(result.errors).toBeUndefined();
      const titles = (result.data?.notes.results ?? []).map((note) => note.title);
      expect(titles).toContain(anchor);
    }
  });
});

test.describe("per-user isolation", () => {
  test("alice and bob see disjoint note sets", async ({ browser }) => {
    const alice = await browser.newContext({ storageState: roleStatePath("alice") });
    const bob = await browser.newContext({ storageState: roleStatePath("bob") });
    try {
      const aliceIds = await noteIds(alice.request);
      const bobIds = await noteIds(bob.request);
      expect(aliceIds.size).toBeGreaterThan(0);
      expect(bobIds.size).toBeGreaterThan(0);
      const shared = [...aliceIds].filter((id) => bobIds.has(id));
      expect(shared).toEqual([]);
    } finally {
      await alice.close();
      await bob.close();
    }
  });
});

test.describe("anonymous — denied", () => {
  test("creating a note without a session is denied", async ({ api }) => {
    const result = await api.query<{ createNote: { id: string } | null }>(
      'mutation { createNote(data: { title: "x" }) { id } }',
    );
    expect(result.data?.createNote ?? null).toBeNull();
    const codes = (result.errors ?? []).map((error) => error.extensions?.code);
    expect(codes).toContain("PERMISSION_DENIED");
  });
});
