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

  test("keys an entity by its Sqid id", () => {
    expect(key("Sale", { id: "abc" })).toBe("abc");
    expect(key("Owner", { id: "xyz" })).toBe("xyz");
  });

  test("null-keys connection and page-info value objects", () => {
    expect(key("SaleConnection", {})).toBeNull();
    expect(key("SaleEdge", {})).toBeNull();
    expect(key("PageInfo", {})).toBeNull();
  });

  test("null-keys aggregate value objects (no id)", () => {
    expect(key("SaleAggregate", { count: 1 })).toBeNull();
    expect(key("SaleSumFields", { total: "1" })).toBeNull();
    expect(key("SaleGroupByResult", {})).toBeNull();
  });

  test("does not register a key for the root operation types", () => {
    expect(keys.Query).toBeUndefined();
    expect(keys.Mutation).toBeUndefined();
  });
});

describe("relay resolvers", () => {
  test("installs a pagination resolver on each connection-returning query field", () => {
    expect(typeof resolvers.Query?.sales).toBe("function");
  });

  test("leaves entity-returning query fields alone", () => {
    expect(resolvers.Query?.sale).toBeUndefined();
    expect(resolvers.Query?.salesAggregate).toBeUndefined();
  });
});
