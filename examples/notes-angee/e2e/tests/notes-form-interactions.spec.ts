import { test, expect, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

// The record form's interactive controls beyond body editing: the tag input, the
// status stepper, the star action, and the chatter Activity tab. Each change is
// reverted with Discard so the spec is non-destructive.
test.describe("notes form — field interactions", () => {
  test.use({ storageState: roleStatePath("alice") });

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

  // Star/Share are presentational stubs (no action wired yet), so they aren't
  // exercised here beyond the presence checks in notes-form.spec.

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

  test("the Activity tab renders the revision timeline", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    await notes.chatterTab("Activity").click();
    // The panel renders (a revision entry or its empty state), with no error.
    await expect(notes.chatterTab("Activity")).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });
});
