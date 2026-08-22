import { expectValidBaseAddon } from "@angee/app/testing";
import { PARTIES_REVIEW_TOOLBAR_SLOT } from "@angee/parties";
import { createRouteHref } from "@angee/ui/runtime";
import { describe, expect, test } from "vitest";

import workflows from "../../../workflows/web/src/index";
import workflowsParties from "./index";

describe("workflows-parties addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(workflowsParties)).not.toThrow();
  });

  test("contributes only the dedupe launcher into the parties review toolbar", () => {
    expect(workflowsParties.routes ?? []).toEqual([]);
    expect((workflowsParties.slots ?? []).map((entry) => [entry.slot, entry.id])).toEqual([
      [PARTIES_REVIEW_TOOLBAR_SLOT, "workflows-parties.dedupe"],
    ]);
  });

  test("its declared workflows dependency composes the workflows.run target", () => {
    const routeHref = createRouteHref([
      ...(workflows.routes ?? []),
      ...(workflowsParties.routes ?? []),
    ]);

    expect(routeHref("workflows.run", { id: "run 1" })).toBe(
      "/workflows/runs/run%201",
    );
  });
});
