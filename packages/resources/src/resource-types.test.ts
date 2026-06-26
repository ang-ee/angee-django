import { describe, expectTypeOf, test } from "vitest";

import type { ResourceFilter, ResourceOrder, ResourceTypeName } from "./resource-types";

describe("resource type contract", () => {
  test("ResourceTypeName is open (any model name) with no registered models", () => {
    expectTypeOf<string>().toEqualTypeOf<ResourceTypeName>();
    expectTypeOf<"notes.Note">().toMatchTypeOf<ResourceTypeName>();
  });

  test("an unregistered model's filter/order type loosely", () => {
    expectTypeOf<ResourceFilter<"notes.Note">>().toEqualTypeOf<Record<string, unknown>>();
    expectTypeOf<ResourceOrder<"notes.Note">>().toEqualTypeOf<Record<string, unknown>>();
  });
});
