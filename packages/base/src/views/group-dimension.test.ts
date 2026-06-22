// @vitest-environment happy-dom

import { describe, expect, test } from "vitest";

import type { ModelMetadata } from "@angee/sdk";

import {
  bucketValueLabels,
  dataViewGroupToAggregateDimension,
  groupLabelDimension,
  groupLabelOrderField,
  groupOrderField,
} from "./ListInternals";

// A model whose `party` relation registers a `party__display_name` label axis.
const PARTY_LABEL_METADATA = {
  fields: {
    party: {
      name: "party",
      kind: "relation",
      relationFilter: {
        field: "party",
        mode: "lookup",
        aggregateKey: "partyId",
        labelKey: "party_DisplayName",
      },
    },
  },
} as unknown as ModelMetadata;

const PARTY_GROUP = {
  field: "party.displayName",
  aggregateField: "party",
  aggregateKey: "partyId",
};

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

describe("relation group display label (Odoo (id, display_name))", () => {
  test("groupLabelDimension carries the registered label axis", () => {
    expect(groupLabelDimension(PARTY_GROUP, PARTY_LABEL_METADATA)).toEqual({
      field: "PARTY__DISPLAY_NAME",
      key: "party_DisplayName",
    });
  });

  test("groupLabelDimension is null without a label axis", () => {
    expect(groupLabelDimension(PARTY_GROUP, null)).toBeNull();
    expect(groupLabelDimension({ field: "status" }, PARTY_LABEL_METADATA)).toBeNull();
  });

  test("groupLabelOrderField sorts by the label's `__` path, not the id", () => {
    expect(groupLabelOrderField(PARTY_GROUP, PARTY_LABEL_METADATA)).toBe(
      "party__display_name",
    );
    expect(groupLabelOrderField(PARTY_GROUP, null)).toBeUndefined();
  });

  test("bucketValueLabels renders the carried name, not the raw id", () => {
    const bucket = { key: { partyId: "4422", party_DisplayName: "PRG Iva" }, count: 1 };
    expect(bucketValueLabels(bucket, [PARTY_GROUP], PARTY_LABEL_METADATA)).toEqual([
      "PRG Iva",
    ]);
  });

  test("bucketValueLabels needs the label axis to render the carried name", () => {
    const bucket = { key: { partyId: "pty_abc", party_DisplayName: "PRG Iva" }, count: 1 };
    // Without metadata there is no label key, so the carried name is not used —
    // the header falls back to the id key (the pre-Odoo behaviour).
    expect(bucketValueLabels(bucket, [PARTY_GROUP], null)).not.toEqual(["PRG Iva"]);
  });
});
