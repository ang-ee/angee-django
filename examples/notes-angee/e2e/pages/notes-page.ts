import type { Locator } from "@playwright/test";
import { PageObject } from "@angee/e2e";

/** The `/notes` list page. */
export class NotesPage extends PageObject {
  readonly path = "/notes";

  /** A note's row/card, located by its visible title. */
  noteByTitle(title: string): Locator {
    return this.page.getByText(title);
  }
}
