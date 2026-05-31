import { describe, expect, test, vi } from "vitest";

import { createRefetchRegistry } from "./relay-registry";

describe("createRefetchRegistry", () => {
  test("invalidate calls every refetch registered for a typename", () => {
    const registry = createRefetchRegistry();
    const a = vi.fn();
    const b = vi.fn();
    registry.register("Sale", a);
    registry.register("Sale", b);
    registry.invalidate(["Sale"]);
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);
  });

  test("unregister stops a refetch from firing", () => {
    const registry = createRefetchRegistry();
    const a = vi.fn();
    const off = registry.register("Sale", a);
    off();
    registry.invalidate(["Sale"]);
    expect(a).not.toHaveBeenCalled();
  });

  test("invalidate ignores typenames with no registrations", () => {
    const registry = createRefetchRegistry();
    expect(() => registry.invalidate(["Unknown"])).not.toThrow();
  });

  test("a refetch fires only for its own typename", () => {
    const registry = createRefetchRegistry();
    const sale = vi.fn();
    registry.register("Sale", sale);
    registry.invalidate(["Owner"]);
    expect(sale).not.toHaveBeenCalled();
  });
});
