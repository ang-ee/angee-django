import { expect, type Locator, PageObject } from "@angee/e2e";

/** The `/notes` resource-view page: list/board, toolbar controls, and the pager. */
export class NotesPage extends PageObject {
  readonly path = "/notes";

  /** A note's row/card, located by its visible title. */
  noteByTitle(title: string): Locator {
    return this.page.getByText(title);
  }

  // --- toolbar / control band ---
  get newNoteButton(): Locator {
    return this.page.getByRole("button", { name: "New note" });
  }
  get groupFavoritesButton(): Locator {
    return this.page.getByRole("button", { name: /filter, group, favorites/i });
  }
  get visibleFieldsButton(): Locator {
    return this.page.getByRole("button", { name: /visible fields/i });
  }
  get listViewButton(): Locator {
    return this.page.getByRole("button", { name: /list view/i });
  }
  get boardViewButton(): Locator {
    return this.page.getByRole("button", { name: /board view/i });
  }

  // --- pager ---
  /** The top pager label. In a flat list it reads "Records N-M / total"; in a
   *  grouped list it pages the groups and reads "Groups N-M / total groups". */
  get recordsLabel(): Locator {
    return this.page
      .locator('[aria-label^="Records "], [aria-label^="Groups "]')
      .first();
  }
  /** A collapsed group-header disclosure in a grouped list. */
  get groupHeaders(): Locator {
    return this.page.locator('tbody tr [aria-expanded]');
  }
  /** Group-header rows, including the visible bucket label and aggregate cells. */
  get groupHeaderRows(): Locator {
    return this.page.locator("tbody tr:has([aria-expanded])");
  }
  /** The group-header disclosure for the group whose row contains `label`. */
  groupHeader(label: string): Locator {
    return this.page
      .locator("tbody tr", { hasText: label })
      .locator("[aria-expanded]")
      .first();
  }
  /** Expand a group by label and wait for its lazily-fetched records. */
  async expandGroup(label: string): Promise<void> {
    await this.groupHeader(label).click();
    await this.recordRows.first().waitFor({ state: "visible", timeout: 12000 });
  }
  /** The top group pager label, e.g. "Groups 1-4 / 4 groups". */
  get groupPagerLabel(): Locator {
    return this.page.locator('[aria-label^="Groups "]').first();
  }
  get nextPageButton(): Locator {
    return this.page.getByRole("button", { name: /next page/i });
  }
  get prevPageButton(): Locator {
    return this.page.getByRole("button", { name: /previous page/i });
  }

  // --- chrome ---
  get userMenuButton(): Locator {
    return this.page.getByRole("button", { name: /user menu/i });
  }
  get chatterToggle(): Locator {
    return this.page.getByRole("button", { name: /chatter/i });
  }

  // --- rows ---
  get rows(): Locator {
    return this.page.locator("tbody tr");
  }
  /** Record rows only — they carry role="link"; grouped lists also render
   *  non-navigable group-header rows, which this excludes. */
  get recordRows(): Locator {
    return this.page.locator("tbody tr[role=link]");
  }

  /** Navigate to /notes and wait past the "Loading workspace…" bootstrap until
   * the list (its pager record label) has rendered. */
  async gotoReady(): Promise<void> {
    await this.goto();
    // Wait past "Loading workspace…" AND the initial "/ 0" until the query
    // resolves and the pager shows a real (non-zero) total.
    await expect(this.recordsLabel).toHaveAttribute(
      "aria-label",
      /\/\s*[1-9][\d,]*/,
      { timeout: 25000 },
    );
  }

  /** Read the pager's record total ("Records 1-50 / 10052" → 10052). */
  async recordTotal(): Promise<number> {
    const label = (await this.recordsLabel.getAttribute("aria-label")) ?? "";
    const match = label.match(/\/\s*([\d,]+)/);
    return match?.[1] ? Number(match[1].replace(/,/g, "")) : 0;
  }

  /** Open the Visible-fields chooser; returns its column checkbox items. */
  async openVisibleFields(): Promise<Locator> {
    await this.visibleFieldsButton.click();
    const items = this.page.getByRole("menuitemcheckbox");
    await items.first().waitFor({ state: "visible" });
    return items;
  }

  /** Open the Filter/Group/Favorites popover. */
  async openGroupFavorites(): Promise<void> {
    await this.groupFavoritesButton.click();
    await this.page.getByText("Group by", { exact: false }).first().waitFor();
  }

  /** Navigate to the first record's form. The list is flat by default (records
   *  visible immediately); the group-expand is a fallback for grouped views. */
  async openFirstNote(): Promise<void> {
    if (!(await this.recordRows.first().isVisible().catch(() => false))) {
      await this.groupHeaders.first().click();
      await this.recordRows.first().waitFor({ state: "visible", timeout: 10000 });
    }
    await this.recordRows.first().click();
    await this.page.waitForURL(/\/notes\/.+/, { timeout: 10000 });
    await this.page.locator(".cm-content").first().waitFor({ timeout: 15000 });
  }

  // --- record form (dirty-save) ---
  get saveButton(): Locator {
    return this.page.getByRole("button", { name: /^Save$/ });
  }
  get discardButton(): Locator {
    return this.page.getByRole("button", { name: /^Discard$/ });
  }

  /** Type into the markdown body to mark the form dirty. */
  async editBody(text: string): Promise<void> {
    const body = this.page.locator(".cm-content").first();
    await body.click();
    await this.page.keyboard.type(text);
  }

  // --- record form sheet (title row, status stepper, actions, notebook) ---
  /** The inline editable record title input. */
  get titleInput(): Locator {
    return this.page.getByRole("textbox", { name: "Title" });
  }
  get starButton(): Locator {
    return this.page.getByRole("button", { name: "Star" });
  }
  get shareButton(): Locator {
    return this.page.getByRole("button", { name: "Share" });
  }
  /** A status-stepper step by its label (Draft / In Review / Active / Archived). */
  statusStep(label: string): Locator {
    return this.page.getByText(label, { exact: true });
  }
  /** The record form's body editor (markdown), rendered inline in the sheet. */
  get bodyEditor(): Locator {
    return this.page.locator(".cm-content").first();
  }
  /** The view toolbar (layout control band) that hosts the dirty Save/Discard
   *  plus the record pager + view switcher when a record is open. */
  get controlBand(): Locator {
    return this.page.locator(".area-control");
  }

  /** Edit the title input — a reliable way to mark the form dirty. */
  async editTitle(text: string): Promise<void> {
    await this.titleInput.click();
    await this.page.keyboard.type(text);
  }

  /** The tag-input control (the tagInput widget). */
  get tagInput(): Locator {
    return this.page.getByRole("textbox", { name: "Tags" });
  }
  /** A chatter-rail tab by name (Angee / Comments / Activity). */
  chatterTab(name: string): Locator {
    return this.page.getByRole("tab", { name: new RegExp(name, "i") });
  }

  // --- chrome ---
  get commandPalette(): Locator {
    return this.page.getByRole("button", { name: /open command palette/i });
  }
  /** Open the user menu and return its sign-out item. */
  async openUserMenu(): Promise<Locator> {
    await this.userMenuButton.click();
    const signOut = this.page.getByRole("menuitem", { name: /sign out|log ?out/i });
    await signOut.waitFor({ state: "visible" });
    return signOut;
  }

  /** Click "New note" and wait for the blank create form. */
  async openCreateForm(): Promise<void> {
    await this.newNoteButton.click();
    await this.titleInput.waitFor({ state: "visible", timeout: 10000 });
  }

  // --- bulk delete (list selection) ---
  get rowCheckboxes(): Locator {
    return this.page.getByRole("checkbox", { name: "Select row" });
  }
  /** The selection bar's "N selected" count text. */
  get selectionCount(): Locator {
    return this.page.getByText(/\d+ selected/);
  }
  /** The selection bar's danger Delete button (only the list-level one). */
  get bulkDeleteButton(): Locator {
    return this.page.getByRole("button", { name: "Delete", exact: true });
  }
  get deleteDialog(): Locator {
    return this.page.getByRole("dialog");
  }
  /** The cascade-preview dialog title, e.g. "Delete 3 records?". */
  get deleteDialogTitle(): Locator {
    return this.page.getByText(/Delete \d+ records\?/);
  }
  get deleteDialogCancel(): Locator {
    return this.page.getByRole("button", { name: "Cancel", exact: true });
  }

  /** Expand the first group if needed, then tick `count` record checkboxes. */
  async selectRecords(count: number): Promise<void> {
    if (!(await this.rowCheckboxes.first().isVisible().catch(() => false))) {
      await this.groupHeaders.first().click();
      await this.rowCheckboxes
        .first()
        .waitFor({ state: "visible", timeout: 10000 });
    }
    for (let index = 0; index < count; index += 1) {
      await this.rowCheckboxes.nth(index).click();
    }
  }
}
