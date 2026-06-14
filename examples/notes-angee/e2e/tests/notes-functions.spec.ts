import { test, expect, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

// Each role has its own REBAC scope, so the same list UI shows that role's
// scoped records. Assert only that the scope is non-empty — the count itself is
// seed-dependent (small demo workspace vs the lorem-seeded dev stack), and the
// per-role *difference* is covered by the alice/bob-disjoint test in notes.spec.
const ROLES = ["admin", "alice", "bob"] as const;

// --- every login: the list loads its own scoped records + the controls work ---
for (const role of ROLES) {
  test.describe(`${role} — list`, () => {
    test.use({ storageState: roleStatePath(role) });

    test(`loads /notes with a non-empty scoped list`, async ({ page }) => {
      const notes = new NotesPage(page);
      await notes.gotoReady();
      expect(await notes.recordTotal()).toBeGreaterThan(0);
      await expect(notes.rows.first()).toBeVisible();
    });

    test(`sees the toolbar + chrome buttons`, async ({ page }) => {
      const notes = new NotesPage(page);
      await notes.gotoReady();
      await expect(notes.newNoteButton).toBeVisible();
      await expect(notes.visibleFieldsButton).toBeVisible();
      await expect(notes.groupFavoritesButton).toBeVisible();
      await expect(notes.userMenuButton).toBeVisible();
    });

    test(`can open the Visible-fields chooser and the group popover`, async ({ page }) => {
      const notes = new NotesPage(page);
      await notes.gotoReady();
      expect(await (await notes.openVisibleFields()).count()).toBeGreaterThanOrEqual(3);
      await page.keyboard.press("Escape");
      await notes.openGroupFavorites();
      await expect(page.getByText("Add custom group", { exact: false })).toBeVisible();
    });
  });
}

// --- functions & buttons exercised against a rich scope (alice) ---
test.describe("notes list — functions & buttons", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("the Visible-fields chooser opens, lists columns, and toggles one", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    const headerCount = await page.locator("thead th").count();

    const toggles = await notes.openVisibleFields();
    expect(await toggles.count()).toBeGreaterThanOrEqual(3);

    // toggling a visible column off removes its header column
    await toggles.nth(1).click();
    await page.keyboard.press("Escape");
    await expect(page.locator("thead th")).toHaveCount(headerCount - 1);
  });

  test("the Filter/Group/Favorites popover opens", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openGroupFavorites();
    await expect(page.getByText("Group by", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("Add custom group", { exact: false })).toBeVisible();
  });

  test("the view-switcher toggles between list (table) and board (cards)", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await expect(notes.rows.first()).toBeVisible();

    await notes.boardViewButton.click();
    await expect(notes.rows).toHaveCount(0); // board is not a table
    await expect(page.getByText("Something went wrong")).toHaveCount(0);

    await notes.listViewButton.click();
    await expect(notes.rows.first()).toBeVisible();
  });

  test("the pager advances to the next page", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    // The list opens month-grouped (per-view grouping defaults) and every
    // group fits one pager page; ungroup to page over records.
    await page.getByRole("button", { name: /^Remove group$/ }).click();
    await expect(notes.recordsLabel).toHaveAttribute("aria-label", /^Records /);
    const first = (await notes.recordsLabel.getAttribute("aria-label")) ?? "";
    await notes.nextPageButton.click();
    await expect
      .poll(async () => notes.recordsLabel.getAttribute("aria-label"))
      .not.toBe(first);
  });

  test("clicking a row opens its record form", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openFirstNote();
    await expect(page).toHaveURL(/\/notes\/.+/);
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("the chatter toggle collapses and reopens the panel", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await expect(notes.chatterToggle).toBeVisible();
    await notes.chatterToggle.click();
    await notes.chatterToggle.click();
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("the New note button opens a blank create form", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await notes.openCreateForm();
    await expect(notes.titleInput).toBeVisible();
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("the user menu opens and offers sign out", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    const signOut = await notes.openUserMenu();
    await expect(signOut).toBeVisible();
  });

  test("the command palette is present in the top chrome", async ({ page }) => {
    const notes = new NotesPage(page);
    await notes.gotoReady();
    await expect(notes.commandPalette).toBeVisible();
  });
});
