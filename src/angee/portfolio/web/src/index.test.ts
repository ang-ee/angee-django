import { expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import portfolio from "./index";

describe("portfolio addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(portfolio)).not.toThrow();
  });

  test("promotion requires product creation admission and the project's write permission", () => {
    expect(portfolio.containers?.["projects.Project#actions-menu"]).toMatchObject({
      "portfolio.promote-project": { permission: "write", requires: "portfolio.Product#create", requiredFields: ["permissions"] },
    });
  });
});
