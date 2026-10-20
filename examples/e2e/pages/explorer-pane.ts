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
    const create = this.root.getByRole("button", { name: `New ${kind}`, exact: true });
    await expect(create).toBeEnabled();
    const pickerBox = await this.picker.boundingBox();
    const createBox = await create.boundingBox();
    expect(pickerBox).not.toBeNull();
    expect(createBox).not.toBeNull();
    // Both explorer verbs belong in the picker band, above the tree.
    expect(Math.abs(pickerBox!.y - createBox!.y)).toBeLessThan(8);
    await create.click();
    const input = this.root.getByRole("textbox", {
      name: this.label === "Files" ? "New folder name" : "New page title", exact: true,
    });
    await input.fill(title);
    await this.root.getByRole("button", { name: "Create", exact: true }).click();
  }

  node(title: string): Locator {
    return this.root.getByRole("treeitem", { name: title, exact: true });
  }
}
