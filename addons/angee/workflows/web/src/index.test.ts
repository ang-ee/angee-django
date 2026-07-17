import { expectValidBaseAddon } from "@angee/app/testing";
import { FORM_VIEW_RECORD_CHROME_SLOT } from "@angee/ui";
import { describe, expect, test } from "vitest";

import workflows from "./index";

describe("workflows addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(workflows)).not.toThrow();
  });

  test("contributes Run workflow to the saved-record toolbar", () => {
    expect(workflows.slots).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          slot: FORM_VIEW_RECORD_CHROME_SLOT,
          id: "workflows.run-workflow",
        }),
      ]),
    );
  });
});
