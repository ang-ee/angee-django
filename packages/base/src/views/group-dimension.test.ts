// @vitest-environment happy-dom

import { describe, expect, test } from "vitest";

import {
  dataViewGroupToAggregateDimension,
  groupOrderField,
} from "./ListInternals";

describe("dataViewGroupToAggregateDimension", () => {
  test("maps a plain field to its uppercase enum + verbatim key", () => {
    expect(dataViewGroupToAggregateDimension({ field: "status" })).toEqual({
      field: "STATUS",
      key: "status",
    });
  });

  test("snake-cases a camelCase field for the enum, keeps the key verbatim", () => {
    expect(dataViewGroupToAggregateDimension({ field: "createdAt" })).toEqual({
      field: "CREATED_AT",
      key: "createdAt",
    });
  });

  test("round-trips a to-one relation-path axis (camel key ↔ __ enum)", () => {
    // `oauthClient_IsEnabled` is Strawberry's camel form of the Django path
    // `oauth_client__is_enabled`; the group key reads the camel field while the
    // backend groupable-field enum is the double-underscore SNAKE_UPPER form.
    expect(
      dataViewGroupToAggregateDimension({ field: "oauthClient_IsEnabled" }),
    ).toEqual({
      field: "OAUTH_CLIENT__IS_ENABLED",
      key: "oauthClient_IsEnabled",
    });
  });

  test("can group on one row field while querying a different aggregate axis", () => {
    expect(
      dataViewGroupToAggregateDimension({
        field: "vendorLabel",
        aggregateField: "vendor",
        aggregateKey: "vendorId",
      }),
    ).toEqual({
      field: "VENDOR",
      key: "vendorId",
    });
  });

  test("carries granularity through, uppercased", () => {
    expect(
      dataViewGroupToAggregateDimension({
        field: "createdAt",
        granularity: "month",
      }),
    ).toEqual({
      field: "CREATED_AT",
      key: "createdAtMonth",
      granularity: "MONTH",
    });
  });
});

describe("groupOrderField", () => {
  test("snake-cases a plain field for the order-by axis", () => {
    expect(groupOrderField({ field: "createdAt" })).toBe("created_at");
    expect(groupOrderField({ field: "status" })).toBe("status");
  });

  test("emits the Django `__` path for a to-one relation axis", () => {
    // The sort path shares `fieldToSnake` with the group enum; ordering by a
    // relation-path group axis must emit the same `oauth_client__is_enabled`
    // path the group-by query uses, not a single-underscore collapse.
    expect(groupOrderField({ field: "oauthClient_IsEnabled" })).toBe(
      "oauth_client__is_enabled",
    );
  });

  test("orders by the aggregate axis when it differs from the row field", () => {
    expect(
      groupOrderField({
        field: "vendorLabel",
        aggregateField: "vendor",
        aggregateKey: "vendorId",
      }),
    ).toBe("vendor");
  });
});
