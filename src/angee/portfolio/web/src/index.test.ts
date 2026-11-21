import { containerChildren, expectValidBaseAddon } from "@angee/app/testing";
import { describe, expect, test } from "vitest";

import portfolio from "./index";

describe("portfolio addon manifest", () => {
  test("satisfies rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(portfolio)).not.toThrow();
  });

  test("promotion adds only product creation admission at the container", () => {
    const contribution = containerChildren(portfolio, "projects.Project#actions-menu")
      .find(([id]) => id === "portfolio.promote-project")?.[1];
    expect(contribution).toMatchObject({ requires: "portfolio.Product#create" });
    expect(contribution).not.toHaveProperty("permission");
    expect(contribution).not.toHaveProperty("requiredFields");
  });
});
