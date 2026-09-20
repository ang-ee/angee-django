// @vitest-environment happy-dom

import { describe, expect, test } from "vitest";

import {
  createNamespaceTTestDouble,
  createUiRouteTestDoubles,
  createUiTestModule,
} from "./testing";

describe("shared UI mock contracts", () => {
  test("groups overrides over one imported UI module", async () => {
    const imported = { useRouteSearch: () => ({ original: true }), untouched: "owner" };
    const module = await createUiTestModule(
      async () => imported as never,
      createUiRouteTestDoubles({ search: { decision: "decision-1" } }),
    );

    expect((module as unknown as { untouched: string }).untouched).toBe("owner");
    expect(module.useRouteSearch()).toEqual({ decision: "decision-1" });
  });

  test("uses namespace fallback messages and interpolation", () => {
    const useT = createNamespaceTTestDouble()("test", { greeting: "Hello {name}" });
    expect(useT()("greeting", { name: "Ada" })).toBe("Hello Ada");
    expect(useT()("missing")).toBe("missing");
  });
});
