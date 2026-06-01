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

// Stable anchors from the demo seed (`resources load demo`). The seed grows over
// time, so assert these are *present* rather than pinning a volatile count.
const ALICE_ANCHORS = ["Quarterly planning", "Reading list", "Welcome to Angee"];

async function noteIds(request: APIRequestContext): Promise<Set<string>> {
  const result = await new GraphQLClient(request).query<NotesData>(NOTES_QUERY);
  expect(result.errors).toBeUndefined();
  return new Set((result.data?.notes.results ?? []).map((note) => note.id));
}

test.describe("alice — authenticated", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("sees her notes in the UI", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.goto();
    for (const title of ALICE_ANCHORS) {
      await expect(notes.noteByTitle(title)).toBeVisible();
    }
  });

  test("notes query returns her scoped notes, including the demo anchors", async ({ api }) => {
    const result = await api.query<NotesData>(NOTES_QUERY);
    expect(result.errors).toBeUndefined();
    expect(result.data?.notes.totalCount).toBeGreaterThan(0);
    const titles = (result.data?.notes.results ?? []).map((note) => note.title);
    for (const anchor of ALICE_ANCHORS) {
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
