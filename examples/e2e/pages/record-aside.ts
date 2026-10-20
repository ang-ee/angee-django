import { expect, type Locator, type Page } from "@angee/e2e";

/** The shell's shared context aside, independent of the record's page. */
export class RecordAside {
  constructor(private readonly page: Page) {}

  get root(): Locator {
    return this.page.getByRole("complementary", { name: "Context", exact: true });
  }

  tab(name: "Timeline" | "Discussion" | "Tasks" | "Related"): Locator {
    return this.root.getByRole("tab", { name: new RegExp(`^${name}(?:\\s*\\d+)?$`) });
  }

  async reveal(): Promise<void> {
    const toggle = this.page.getByRole("button", { name: /^(Open|Collapse) chatter$/ });
    await expect(toggle).toBeVisible();
    if (await toggle.getAttribute("aria-label") === "Open chatter") await toggle.click();
    await expect(this.root).toBeInViewport();
  }

  async open(name: "Timeline" | "Discussion" | "Tasks" | "Related"): Promise<void> {
    await this.reveal();
    await expect(this.tab(name)).toBeVisible();
    await this.tab(name).click();
    await expect(this.tab(name)).toHaveAttribute("aria-selected", "true");
  }

  scope(name: string): Locator {
    return this.root.getByRole("button", { name: new RegExp(`^${name}(?:\\s*\\d+)?$`) });
  }

  line(verb: RegExp, title?: string): Locator {
    const rows = this.root.getByRole("button", { name: verb });
    return title ? rows.filter({ hasText: title }) : rows;
  }

  message(body: string): Locator {
    return this.root.getByRole("list", { name: "Comments", exact: true })
      .getByRole("listitem").filter({ hasText: body });
  }

  async post(body: string, kind: "Comment" | "Note" = "Comment"): Promise<void> {
    await this.root.getByRole("button", { name: kind, exact: true }).click();
    await this.root.getByRole("textbox", { name: "Message", exact: true }).fill(body);
    await this.root.getByRole("button", { name: kind === "Note" ? "Log" : "Send", exact: true }).click();
    await expect(this.message(body)).toBeVisible();
  }

  get backToTimeline(): Locator {
    return this.root.getByRole("button", { name: "Timeline", exact: true });
  }

  get follow(): Locator {
    return this.page.getByRole("button", { name: /^(Follow|Following)$/ });
  }

  get needsYou(): Locator {
    return this.page.getByRole("button", { name: /^Needs you · \d+$/ });
  }
}
