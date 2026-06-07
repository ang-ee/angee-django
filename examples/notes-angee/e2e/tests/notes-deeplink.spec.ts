import { test, expect, roleStatePath, GraphQLClient } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

const NOTES_QUERY = `query Notes {
  notes {
    results { id }
  }
}`;

interface NotesData {
  notes: { results: { id: string }[] };
}

async function firstNoteId(api: GraphQLClient): Promise<string> {
  const result = await api.query<NotesData>(NOTES_QUERY);
  expect(result.errors).toBeUndefined();
  const id = result.data?.notes.results[0]?.id;
  expect(id).toBeTruthy();
  return id!;
}

test.describe("notes record routes", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("opening /notes/$id directly deep-links into the record form", async ({
    page,
    api,
  }) => {
    const notes = new NotesPage(page);
    const id = await firstNoteId(api);

    await page.goto(`/notes/${encodeURIComponent(id)}`);

    await expect(notes.titleInput).toBeVisible({ timeout: 25000 });
    await expect(notes.bodyEditor).toBeVisible();
    await expect(page).toHaveURL(new RegExp(`/notes/${encodeURIComponent(id)}$`));
  });

  test("browser Back from an open record returns to the list", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    await page.goBack();

    await expect(page).toHaveURL(/\/notes(?:\?.*)?$/);
    await expect(notes.recordsLabel).toHaveAttribute(
      "aria-label",
      /\/\s*[1-9][\d,]*/,
      { timeout: 25000 },
    );
    await expect(notes.titleInput).toHaveCount(0);
  });

  test("/notes/new shows a blank create form", async ({ page }) => {
    const notes = new NotesPage(page);

    await page.goto("/notes/new");

    await expect(notes.titleInput).toBeVisible({ timeout: 25000 });
    await expect(notes.titleInput).toHaveValue("");
    await expect(notes.bodyEditor).toBeVisible();
  });
});
