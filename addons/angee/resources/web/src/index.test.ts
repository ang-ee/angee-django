import type { BaseMenuItem } from "@angee/base";
import { describe, expect, test } from "vitest";

import resources from "./index";

describe("resources addon manifest", () => {
  test("contributes a single Resources section under platform", () => {
    expect(resources.menus).toHaveLength(1);
    const menu = resources.menus?.[0] as BaseMenuItem | undefined;
    expect(menu?.id).toBe("resources");
    expect(menu?.parentId).toBe("platform");
    expect(menu?.label).toBe("Resources");
    expect(menu?.route).toBe("resources.ledger");
    // A single leaf section (no children) — renders as a link, not a dropdown.
    expect(menu?.children).toBeUndefined();
  });

  test("registers one console route for the ledger", () => {
    expect(resources.routes).toHaveLength(1);
    const route = resources.routes?.[0];
    expect(route?.name).toBe("resources.ledger");
    expect(route?.path).toBe("/platform/resources");
    expect(route?.shell).toBe("console");
    expect(route?.component).toBeTypeOf("function");
  });
});
