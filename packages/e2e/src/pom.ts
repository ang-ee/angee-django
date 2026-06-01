import type { Page } from "@playwright/test";

/**
 * Base for a Page Object — the single source of truth for one page's selectors
 * and intents. Subclasses declare the route they own and expose locators and
 * intent methods; specs read like prose and never re-derive a selector. This is
 * Angee's default e2e authoring style (see `docs/testing/e2e.md`).
 */
export abstract class PageObject {
  protected readonly page: Page;

  /** The route this object owns, relative to the configured `baseURL`. */
  abstract readonly path: string;

  constructor(page: Page) {
    this.page = page;
  }

  /** Navigate to the page this object owns. */
  async goto(): Promise<void> {
    await this.page.goto(this.path);
  }
}
