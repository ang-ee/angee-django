import { expectValidBaseAddon } from "@angee/app/testing";
import { createRouteHref } from "@angee/ui";
import { describe, expect, test } from "vitest";

import workflowsExtraction from "./index";

describe("workflows extraction addon manifest", () => {
  test("registers immutable Extraction evidence as a native resource route", () => {
    expect(() => expectValidBaseAddon(workflowsExtraction)).not.toThrow();
    const routes = workflowsExtraction.routes ?? [];
    expect(routes.map((route) => [route.name, route.resource])).toEqual([
      ["workflows-extraction.extractions", "workflows_extraction.Extraction"],
      ["workflows-extraction.extractions.record", undefined],
    ]);
    const routeHref = createRouteHref(routes.map(({ name, path }) => ({ name, path })));
    expect(routeHref("workflows-extraction.extractions.record", { id: "ext 1" }))
      .toBe("/workflows/extractions/ext%201");
  });
});
