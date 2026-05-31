import { describe, expectTypeOf, test } from "vitest";

import type { SaleFilter, SaleOrder } from "./__generated__/public";
import type {
  ResourceFilter,
  ResourceOrder,
  ResourceTypeName,
} from "./__generated__/resource-types";

describe("resource type map", () => {
  test("ResourceTypeName includes the generated model names", () => {
    expectTypeOf<"Sale">().toMatchTypeOf<ResourceTypeName>();
  });

  test("ResourceFilter resolves to the model's generated filter input", () => {
    expectTypeOf<ResourceFilter<"Sale">>().toEqualTypeOf<SaleFilter>();
  });

  test("ResourceOrder resolves to the model's generated order input", () => {
    expectTypeOf<ResourceOrder<"Sale">>().toEqualTypeOf<SaleOrder>();
  });
});
