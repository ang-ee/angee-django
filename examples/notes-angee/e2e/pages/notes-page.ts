import { expect, type Locator } from "@playwright/test";
import { PageObject } from "@angee/e2e";

/** The `/notes` data-view page: list/board, toolbar controls, and the pager. */
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
  /** The "Records N-M / total" label (a control in the pager). */
  get recordsLabel(): Locator {
    return this.page.locator('[aria-label^="Records "]').first();
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

  /** Navigate to the first record's form by clicking its row. Targets a record
   *  row specifically so a grouped list's header rows don't get clicked. */
  async openFirstNote(): Promise<void> {
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
  /** A notebook tab by name; the body field renders in the first ("Body") tab. */
  notebookTab(name: string | RegExp): Locator {
    return this.page.getByRole("tab", { name });
  }

  /** Edit the title input — a reliable way to mark the form dirty. */
  async editTitle(text: string): Promise<void> {
    await this.titleInput.click();
    await this.page.keyboard.type(text);
  }

  // --- chrome ---
  get globalSearch(): Locator {
    return this.page.getByRole("search");
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
}
