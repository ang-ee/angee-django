import { expect, grantRecordAccess, GraphQLClient, requireGraphQLData, revokeRecordAccess, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";
import { test } from "./fixtures/notes";

// The record form's interactive controls beyond body editing: the tag input, the
// status stepper, the star action, and the chatter Activity tab. Form edits use
// Discard; the persisted Star regression owns a fresh note and its cleanup.
test.describe("notes form — field interactions", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("Star acknowledges and persists toggles while a reader stays read-only", async ({ page, api, browser, note }) => {
    const notes = new NotesPage(page);
    const readStar = async (): Promise<boolean | null> => {
      const { notes_by_pk: row } = requireGraphQLData(await api.query<{
        notes_by_pk: { is_starred: boolean | null };
      }>(`query NoteStar($id: String!) {
        notes_by_pk(id: $id) { is_starred }
      }`, { id: note.id }));
      return row.is_starred;
    };
    await notes.openNote(note.id);
    await expect(notes.starButton).toBeEnabled();
    await expect(notes.starButton).toHaveAttribute("aria-pressed", "false");

    await test.step("Star saves independently and the acknowledged value survives reload", async () => {
      await notes.starButton.click();
      await expect(notes.starButton).toHaveAttribute("aria-pressed", "true");
      await expect(notes.starButton).toBeEnabled();
      expect(await readStar()).toBe(true);
      await expect(notes.saveButton).toHaveCount(0);
      await page.reload();
      await expect(notes.starButton).toHaveAttribute("aria-pressed", "true");
    });

    await test.step("a dirty form blocks Star until Discard restores its baseline", async () => {
      await notes.editTitle(" unsaved");
      await expect(notes.saveButton).toBeVisible();
      await expect(notes.starButton).toBeDisabled();
      expect(await readStar()).toBe(true);
      await notes.discardButton.click();
      await expect(notes.starButton).toBeEnabled();
      await expect(notes.starButton).toHaveAttribute("aria-pressed", "true");
    });

    await test.step("a granted reader cannot toggle the owner's redacted flag", async () => {
      const admin = await browser.newContext({ storageState: roleStatePath("admin") });
      const reader = await browser.newContext({ storageState: roleStatePath("bob") });
      const consoleApi = new GraphQLClient(page.request, "/graphql/console/");
      try {
        const { users } = requireGraphQLData(await new GraphQLClient(admin.request, "/graphql/console/").query<{
          users: Array<{ username: string; assignment_subject: string }>;
        }>(`query NoteStarReader { users(limit: 100) { username assignment_subject } }`));
        const bob = users.find((user) => user.username === "bob");
        if (!bob) throw new Error("The Notes reference stack must seed bob.");
        const grant = await grantRecordAccess(consoleApi, {
          targetType: "notes/note", targetId: note.id, relation: "reader", subject: bob.assignment_subject,
        });
        try {
          const readerPage = await reader.newPage();
          await readerPage.goto(page.url());
          const readerNotes = new NotesPage(readerPage);
          await expect(readerNotes.starButton).toBeVisible();
          await expect(readerNotes.starButton).toBeDisabled();
          const { notes_by_pk: row } = requireGraphQLData(await new GraphQLClient(reader.request).query<{
            notes_by_pk: { title: string; is_starred: boolean | null };
          }>(`query ReaderNoteStar($id: String!) {
            notes_by_pk(id: $id) { title is_starred }
          }`, { id: note.id }));
          expect(row.title).toBe(note.title);
          expect(row.is_starred).toBeNull();
          expect(await readStar()).toBe(true);
        } finally {
          await revokeRecordAccess(consoleApi, grant);
        }
      } finally {
        await reader.close();
        await admin.close();
      }
    });

    await test.step("a second toggle clears the flag and persists after reload", async () => {
      await notes.starButton.click();
      await expect(notes.starButton).toHaveAttribute("aria-pressed", "false");
      await expect(notes.starButton).toBeEnabled();
      expect(await readStar()).toBe(false);
      await page.reload();
      await expect(notes.starButton).toHaveAttribute("aria-pressed", "false");
    });
  });

  test("adding a tag surfaces a chip and marks the form dirty", async ({
    page,
  }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();
    await expect(notes.saveButton).toHaveCount(0);

    await notes.tagInput.click();
    await notes.tagInput.fill("e2e-check");
    await notes.tagInput.press("Enter");

    await expect(page.getByText("e2e-check")).toBeVisible();
    await expect(notes.saveButton).toBeVisible();

    await notes.discardButton.click();
    await expect(notes.saveButton).toHaveCount(0);
    await expect(page.getByText("e2e-check")).toHaveCount(0);
  });

  test("choosing a different status step marks the form dirty", async ({
    page,
  }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();
    await expect(notes.saveButton).toHaveCount(0);

    // Click each step until one differs from the current status and the form
    // turns dirty (robust to whatever status the first record happens to hold).
    for (const label of ["Archived", "Draft", "Active", "In Review"]) {
      await notes.statusStep(label).first().click();
      if (await notes.saveButton.isVisible().catch(() => false)) break;
    }
    await expect(notes.saveButton).toBeVisible();

    await notes.discardButton.click();
    await expect(notes.saveButton).toHaveCount(0);
  });
});

test.describe("notes chatter — activity", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("the Timeline tab renders the revision lines", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    await notes.chatterTab("Timeline").click();
    // The panel renders (a revision entry or its empty state), with no error.
    await expect(notes.chatterTab("Timeline")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });
});
