import { test, expect, roleStatePath } from "@angee/e2e";

import { NotesPage } from "../pages/notes-page";

// The grouped list is server-driven: the top pager pages GROUP buckets, each
// group is folded by default with an aggregate count, and expanding a group
// lazily fetches its own page of records. Status gives four stable groups.
const STATUS_URL = "/notes?group=status";

test.describe("notes list — grouped (folded, two pagers)", () => {
  test.use({ storageState: roleStatePath("alice") });

  test("folds groups by default and pages groups (not records)", async ({ page }) => {
    const notes = new NotesPage(page);
    await page.goto(STATUS_URL);
    await expect(notes.groupHeaders.first()).toBeVisible({ timeout: 25000 });

    // folded → group headers present, no record rows yet
    expect(await notes.groupHeaders.count()).toBeGreaterThanOrEqual(2);
    await expect(notes.recordRows).toHaveCount(0);

    // the top pager reports GROUPS, not records
    await expect(notes.groupPagerLabel).toHaveAttribute(
      "aria-label",
      /Groups\s+\d+-\d+\s*\/\s*\d+\s+groups/,
    );
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("each group header carries its aggregate count", async ({ page }) => {
    const notes = new NotesPage(page);
    await page.goto(STATUS_URL);
    await expect(notes.groupHeaders.first()).toBeVisible({ timeout: 25000 });
    // the "Active" group's row shows the status label and a numeric count
    const activeRow = page.locator("tbody tr", { hasText: "Active" }).first();
    await expect(activeRow).toContainText(/\d/);
  });

  test("expanding a group lazily loads its records", async ({ page }) => {
    const notes = new NotesPage(page);
    await page.goto(STATUS_URL);
    await expect(notes.groupHeaders.first()).toBeVisible({ timeout: 25000 });
    await expect(notes.recordRows).toHaveCount(0); // folded → nothing fetched

    await notes.expandGroup("Active");
    expect(await notes.recordRows.count()).toBeGreaterThan(0);
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("a record opens its form from within an expanded group", async ({ page }) => {
    const notes = new NotesPage(page);
    await page.goto(STATUS_URL);
    await expect(notes.groupHeaders.first()).toBeVisible({ timeout: 25000 });
    await notes.openFirstNote(); // expands a group, then clicks a record
    await expect(page).toHaveURL(/\/notes\/.+/);
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });

  test("the board view renders the groups as Kanban lanes of cards", async ({ page }) => {
    const notes = new NotesPage(page);
    await page.goto("/notes?view=board&group=status");
    // a status lane per group, with cards — and no table
    await expect(page.getByText("Active", { exact: true }).first()).toBeVisible({
      timeout: 25000,
    });
    for (const label of ["Draft", "In Review", "Active", "Archived"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await expect(notes.rows).toHaveCount(0); // lanes, not a table
    expect(await page.locator("article").count()).toBeGreaterThan(0); // cards
    await expect(page.getByText("Something went wrong")).toHaveCount(0);
  });
});

// Every login gets the same folded grouped list over its own scope.
for (const role of ["admin", "alice", "bob"] as const) {
  test.describe(`${role} — grouped list`, () => {
    test.use({ storageState: roleStatePath(role) });

    test("renders folded groups over the scoped records", async ({ page }) => {
      const notes = new NotesPage(page);
      await page.goto(STATUS_URL);
      await expect(notes.groupHeaders.first()).toBeVisible({ timeout: 25000 });
      await expect(notes.groupPagerLabel).toHaveAttribute("aria-label", /groups/);
      await expect(page.getByText("Something went wrong")).toHaveCount(0);
    });
  });
}
