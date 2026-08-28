import { expect, type Locator, PageObject } from "@angee/e2e";

/** Operator workspace list, create dialog, and workspace detail lifecycle actions. */
export class OperatorWorkspacesPage extends PageObject {
  readonly path = "/operator/workspaces";

  get newWorkspaceButton(): Locator {
    return this.page.getByRole("button", { name: "New workspace" });
  }

  get createDialog(): Locator {
    return this.page.getByRole("dialog", { name: "Create workspace" });
  }

  get templatePicker(): Locator {
    return this.createDialog.getByRole("combobox", {
      name: "Workspace template",
    });
  }

  templateOption(name: string): Locator {
    return this.page.getByRole("option", { name, exact: true });
  }

  input(name: string): Locator {
    return this.createDialog.getByLabel(name, { exact: true });
  }

  get createButton(): Locator {
    return this.createDialog.getByRole("button", {
      name: "Create workspace",
      exact: true,
    });
  }

  workspaceRow(name: string): Locator {
    return this.page
      .locator("tbody tr")
      .filter({ hasText: name })
      .first();
  }

  get destroyButton(): Locator {
    return this.page.getByRole("button", { name: "Destroy", exact: true });
  }

  async gotoReady(): Promise<void> {
    await this.goto();
    await expect(this.newWorkspaceButton).toBeVisible({ timeout: 20000 });
  }

  async openCreate(): Promise<void> {
    await this.newWorkspaceButton.click();
    await expect(this.createDialog).toBeVisible();
  }

  async chooseTemplate(name: string): Promise<void> {
    await this.templatePicker.click();
    await this.templateOption(name).click();
    await expect(this.templatePicker).toContainText(name);
  }

  async confirmDestroy(): Promise<void> {
    await this.destroyButton.click();
    const confirmation = this.page.getByRole("dialog", {
      name: "Destroy workspace?",
    });
    await confirmation.getByRole("button", { name: "Destroy" }).click();
  }
}
