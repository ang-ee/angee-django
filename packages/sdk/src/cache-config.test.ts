import { readFileSync } from "node:fs";

import { buildSchema } from "graphql";
import { describe, expect, test } from "vitest";

import { cacheConfigFromSchema } from "./cache-config";

const schema = buildSchema(
  readFileSync(new URL("../schema/contract.graphql", import.meta.url), "utf8"),
);
const { keys, resolvers } = cacheConfigFromSchema(schema);

describe("cache keys", () => {
  const key = (typename: string, data: Record<string, unknown>): string | null =>
    keys[typename]?.({ __typename: typename, ...data }) ?? null;

  test("keys an entity by its relay id", () => {
    expect(key("Sale", { id: "abc" })).toBe("abc");
    expect(key("Viewer", { id: "xyz" })).toBe("xyz");
  });

  test("null-keys page and page-info value objects", () => {
    expect(key("SaleOffsetPaginated", {})).toBeNull();
    expect(key("OffsetPaginationInfo", { offset: 0 })).toBeNull();
  });

  test("null-keys aggregate value objects (no id)", () => {
    expect(key("SaleAggregate", { count: 1 })).toBeNull();
    expect(key("SaleGrouped", { count: 1 })).toBeNull();
    expect(key("DeletePreview", {})).toBeNull();
  });

  test("does not register a key for the root operation types", () => {
    expect(keys.Query).toBeUndefined();
    expect(keys.Mutation).toBeUndefined();
    expect(keys.Subscription).toBeUndefined();
  });
});

describe("relay resolvers", () => {
  test("offset-paginated list fields get no cursor-merge resolver", () => {
    // Offset pages replace the list (jump-to-page); urql caches each page by its
    // variables, so no relay pagination resolver is wired.
    expect(resolvers.Query?.sales).toBeUndefined();
  });

  test("leaves entity and aggregate query fields alone", () => {
    expect(resolvers.Query?.sale).toBeUndefined();
    expect(resolvers.Query?.saleAggregate).toBeUndefined();
  });
});
