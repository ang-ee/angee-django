import { expect, requireGraphQLData, type Locator, type Page } from "@angee/e2e";

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

  groupWithVerb(name: string): Locator {
    // `has` is relative to each list item, so it must not include the aside ancestor.
    return this.root.getByRole("listitem").filter({ has: this.page.getByRole("button", { name, exact: true }) });
  }

  get currentHistoryEntry(): Locator {
    return this.root.locator('button[aria-current="true"]');
  }

  async post(body: string, kind: "Comment" | "Note" = "Comment"): Promise<void> {
    await this.root.getByRole("button", { name: kind, exact: true }).click();
    const input = this.root.getByRole("textbox", { name: "Message", exact: true });
    await input.fill(body);
    const { post_record_message: posted } = await this.mutate<{
      post_record_message: { error: string | null; message: { id: string } | null };
    }>("post_record_message", this.root.getByRole("button", { name: kind === "Note" ? "Log" : "Send", exact: true }));
    expect(posted.error).toBeNull();
    expect(posted.message?.id).toBeTruthy();
    await expect(input).toHaveValue("");
    await expect(this.message(body)).toBeVisible();
  }

  async completeTask(title: string): Promise<void> {
    const planned = this.root.getByRole("region", { name: "Planned", exact: true });
    const { complete_task: completed } = await this.mutate<{
      complete_task: { ok: boolean; message: string };
    }>("complete_task", planned.getByRole("listitem").filter({ hasText: title })
      .getByRole("button", { name: "Mark done", exact: true }));
    expect(completed.ok, completed.message).toBe(true);
  }

  /** Await the server's write before asserting the separately refreshed view. */
  private async mutate<T>(field: string, button: Locator): Promise<T> {
    const response = this.page.waitForResponse((result) =>
      new URL(result.url()).pathname === "/graphql/console/" &&
      result.request().method() === "POST" &&
      Boolean(result.request().postData()?.includes(field)),
    );
    await button.click();
    return requireGraphQLData<T>(await (await response).json());
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
