import { expect, type Locator, type Page } from "@angee/e2e";

/** ScopedExplorerPane's shared navigator, root picker and inline create field. */
export class ExplorerPane {
  constructor(private readonly page: Page, private readonly label: "Files" | "Pages") {}

  get root(): Locator {
    return this.page.getByRole("navigation", { name: this.label, exact: true });
  }

  get picker(): Locator {
    return this.root.getByRole("button", { name: this.label === "Files" ? /^Drive:/ : /^Vault:/ });
  }

  async reveal(): Promise<void> {
    const open = this.page.getByRole("button", { name: "Open primary panel", exact: true });
    await expect(open.or(this.picker)).toBeVisible();
    if (await open.count()) await open.click();
    await expect(this.picker).toBeVisible();
  }

  async selectRoot(name: string): Promise<void> {
    await this.picker.click();
    await this.page.getByRole("option", { name, exact: true }).click();
  }

  async create(kind: "folder" | "note", title: string): Promise<void> {
    // One labeled "New" menu in the picker band, above the tree.
    const menu = this.root.getByRole("button", { name: "New", exact: true });
    await expect(menu).toBeEnabled();
    const pickerBox = await this.picker.boundingBox();
    const menuBox = await menu.boundingBox();
    expect(pickerBox).not.toBeNull();
    expect(menuBox).not.toBeNull();
    expect(Math.abs(pickerBox!.y - menuBox!.y)).toBeLessThan(8);
    await menu.click();
    await this.page.getByRole("menuitem", { name: kind === "folder" ? "Folder" : "Page", exact: true }).click();
    // The new item is typed as a row in the tree where it will live; Enter creates it.
    const input = this.root.getByRole("tree").getByRole("textbox", {
      name: this.label === "Files" ? "New folder name" : "New page title", exact: true,
    });
    await input.fill(title);
    await input.press("Enter");
  }

  node(title: string): Locator {
    // A folder's accessible name also includes its Expand/Collapse control.
    return this.root.getByRole("treeitem").filter({ has: this.page.getByText(title, { exact: true }) });
  }
}
