import { test, expect, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

// The list-level bulk delete: select records, open the cascade-preview dialog,
// and cancel without deleting. Runs as alice, who owns seeded notes. The destroy
// (confirm) path is covered by the backend delete-preview tests; here we exercise
// every button on the selection bar + dialog non-destructively.
test.describe("notes bulk delete — cascade preview", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("a single selection opens the preview dialog and cancels cleanly", async ({
    page,
  }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();

    await notes.selectRecords(1);
    await expect(page.getByText("1 selected")).toBeVisible();

    await notes.bulkDeleteButton.click();
    await expect(page.getByText("Delete 1 records?")).toBeVisible({
      timeout: 10000,
    });
    // The dialog renders the cascade summary + tree, not just the title.
    await expect(notes.deleteDialog.getByText(/rows affected/i)).toBeVisible();

    await notes.deleteDialogCancel.click();
    await expect(notes.deleteDialogTitle).toBeHidden();
    // Non-destructive: the row is still selectable (still present).
    await expect(page.getByText("1 selected")).toBeVisible();
  });

  test("a multi-row selection aggregates into one preview dialog", async ({
    page,
  }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();

    await notes.selectRecords(3);
    await expect(page.getByText("3 selected")).toBeVisible();

    await notes.bulkDeleteButton.click();
    await expect(page.getByText("Delete 3 records?")).toBeVisible({
      timeout: 10000,
    });

    await notes.deleteDialogCancel.click();
    await expect(notes.deleteDialogTitle).toBeHidden();
  });
});
