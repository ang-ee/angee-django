import { test, expect, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

// The record form's dirty-save behavior. Button placement + dirty-only is fixed
// (1e71bc4); the seamless post-mutation cache update is a known framework bug
// (see the plan: "Seamless post-mutation update across ALL forms/views/subs").
test.describe("notes form — dirty-save", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("a clean form shows no Save/Discard; editing surfaces both in the view toolbar", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    // the view toolbar (control band) carries the record pager even when clean...
    await expect(notes.controlBand).toContainText(/of\s[\d,]+/);
    // ...while Save/Discard are dirty-only.
    await expect(notes.saveButton).toHaveCount(0);
    await expect(notes.discardButton).toHaveCount(0);

    await notes.editBody(" e2e-edit");
    // Save/Discard surface in the control band (the view toolbar), not in the form.
    await expect(
      notes.controlBand.getByRole("button", { name: /^Save$/ }),
    ).toBeVisible();
    await expect(
      notes.controlBand.getByRole("button", { name: /^Discard$/ }),
    ).toBeVisible();

    // the band sits above the form body. Editing the body can scroll the band
    // out of view, so bring it back before comparing.
    await notes.saveButton.scrollIntoViewIfNeeded();
    const saveBox = await notes.saveButton.boundingBox();
    const bodyBox = await notes.bodyEditor.boundingBox();
    expect(saveBox).not.toBeNull();
    expect(bodyBox).not.toBeNull();
    expect(saveBox!.y).toBeLessThan(bodyBox!.y);
  });

  test("Discard resets the form and clears the dirty actions", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    await notes.editBody(" discard-me");
    await expect(notes.discardButton).toBeVisible();
    await notes.discardButton.click();
    await expect(notes.saveButton).toHaveCount(0);
    await expect(notes.discardButton).toHaveCount(0);
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  // After save the form reflects the saved values in-place, no reload. (The
  // earlier blanking was the form re-seeding to empty defaults on the post-save
  // re-render; fixed by sourcing useForm's defaults from a stable baseline ref.)
  test("saving reflects the new values in-place (no reload)", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    const stamp = " seamless-edit";
    await notes.editBody(stamp);
    await notes.saveButton.click();
    await expect(notes.saveButton).toHaveCount(0); // baseline reset
    // without a reload, the body still contains the saved text.
    await expect(notes.bodyEditor).toContainText(stamp.trim());
  });
});

// The record-sheet chrome: inline title, status stepper, record actions, and the
// inline body (no notebook tabs). These are the layout the form was rebuilt around.
test.describe("notes form — sheet chrome", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("renders the inline title, status stepper, record actions, and inline body", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();

    await expect(notes.titleInput).toBeVisible();
    await expect(notes.statusStep("In Review").first()).toBeVisible(); // the stepper
    await expect(notes.starButton).toBeVisible();
    await expect(notes.shareButton).toBeVisible();
    await expect(notes.bodyEditor).toBeVisible(); // body renders inline, not in a tab
    await expect(page.getByRole("tablist", { name: /notebook/i })).toHaveCount(0);
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("editing then navigating away triggers the unsaved-changes guard", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();
    const formUrl = page.url();

    // edit the title (a reliable dirty trigger) then try to leave via the rail
    await notes.editTitle(" guard");
    await expect(notes.saveButton).toBeVisible();
    await page
      .getByRole("navigation", { name: "Primary navigation" })
      .getByRole("link", { name: "Notes" })
      .click();

    // the guard intercepts: a confirm appears and navigation is blocked
    await expect(page.getByText(/unsaved changes/i)).toBeVisible();
    await expect(page).toHaveURL(formUrl);

    // "Stay" keeps us on the form with the edit intact
    await page.getByRole("button", { name: /^Stay$/ }).click();
    await expect(page).toHaveURL(formUrl);
    await expect(notes.saveButton).toBeVisible();
  });
});

// Every login can open one of its own records and get the same working sheet.
for (const role of ["admin", "alice", "bob"] as const) {
  test.describe(`${role} — record form`, () => {
    test.use({ storageState: roleStatePath(role) });

    test("opens a scoped record and renders the form sheet", async ({ page }) => {
      const notes = new NotesPage(page);
      await notes.gotoReady();
      await notes.openFirstNote();
      await expect(page).toHaveURL(/\/notes\/.+/);
      await expect(notes.titleInput).toBeVisible();
      await expect(notes.bodyEditor).toBeVisible();
      await expect(page.getByText("Something went wrong")).toHaveCount(0);
    });
  });
}
