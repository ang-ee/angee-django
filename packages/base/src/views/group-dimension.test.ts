// @vitest-environment happy-dom

import { describe, expect, test } from "vitest";

import type { ModelMetadata } from "@angee/sdk";

import {
  bucketFilterForGroup,
  bucketValueLabels,
  dataViewGroupToAggregateDimension,
  groupLabelDimension,
} from "./ListInternals";
import { validDataViewGroupStack } from "./list-view-utils";

// A model whose resource artifact owns group dimensions, including a relation
// label axis for `party__display_name`.
const GROUP_METADATA = {
  typeName: "ExampleType",
  fields: {
    status: {
      name: "status",
      kind: "enum",
      values: [{ value: "ACTIVE" }, { value: "IN_REVIEW" }],
    },
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
  resource: {
    groupDimensions: [
      {
        field: "status",
        input: "SERVER_STATUS",
        key: "serverStatus",
        kind: "column",
        scalar: "String",
      },
      {
        field: "createdAt",
        input: "CREATED_AT",
        key: "createdAt",
        kind: "column",
        scalar: "DateTime",
        extractions: [
          {
            name: "month",
            input: "MONTH",
            key: "createdAtMonth",
            rangeKey: "createdAtMonthRange",
          },
        ],
      },
      {
        field: "oauthClient_IsEnabled",
        input: "OAUTH_CLIENT__IS_ENABLED",
        key: "oauthClient_IsEnabled",
        kind: "column",
        scalar: "Boolean",
      },
      {
        field: "vendor",
        input: "VENDOR",
        key: "vendorId",
        kind: "relation",
        scalar: "ID",
      },
      {
        field: "party",
        input: "PARTY",
        key: "partyId",
        kind: "relation",
        scalar: "ID",
      },
      {
        field: "party_DisplayName",
        input: "PARTY__DISPLAY_NAME",
        key: "party_DisplayName",
        kind: "column",
        scalar: "String",
      },
      {
        field: "metadata",
        input: "METADATA",
        key: "metadata",
        kind: "column",
        scalar: "JSON",
      },
    ],
  },
} as unknown as ModelMetadata;

const PARTY_GROUP = {
  field: "party.displayName",
  aggregateField: "party",
  aggregateKey: "partyId",
};

const HASURA_SNAKE_METADATA = {
  typeName: "NoteType",
  fields: {},
  resource: {
    filterFields: ["updated_at"],
    groupByFields: ["updated_at"],
    groupDimensions: [
      {
        field: "updated_at",
        input: "UPDATED_AT",
        key: "updated_at",
        kind: "column",
        scalar: "DateTime",
        extractions: [
          {
            name: "month",
            input: "MONTH",
            key: "updated_at_month",
            rangeKey: "updated_at_month_range",
          },
        ],
      },
    ],
  },
} as unknown as ModelMetadata;

describe("dataViewGroupToAggregateDimension", () => {
  test("uses backend group dimension metadata verbatim", () => {
    expect(dataViewGroupToAggregateDimension({ field: "status" }, GROUP_METADATA)).toEqual({
      field: "SERVER_STATUS",
      key: "serverStatus",
    });
  });

  test("uses backend metadata for camelCase field dimensions", () => {
    expect(dataViewGroupToAggregateDimension({ field: "createdAt" }, GROUP_METADATA)).toEqual({
      field: "CREATED_AT",
      key: "createdAt",
    });
  });

  test("round-trips a to-one relation-path axis (camel key ↔ __ enum)", () => {
    // `oauthClient_IsEnabled` is Strawberry's camel form of the Django path
    // `oauth_client__is_enabled`; the group key reads the camel field while the
    // backend groupable-field enum is the double-underscore SNAKE_UPPER form.
    expect(
      dataViewGroupToAggregateDimension(
        { field: "oauthClient_IsEnabled" },
        GROUP_METADATA,
      ),
    ).toEqual({
      field: "OAUTH_CLIENT__IS_ENABLED",
      key: "oauthClient_IsEnabled",
    });
  });

  test("can group on one row field while querying a different aggregate axis", () => {
    expect(
      dataViewGroupToAggregateDimension({
        field: "vendor.displayName",
        aggregateField: "vendor",
        aggregateKey: "vendorId",
      }, GROUP_METADATA),
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
      }, GROUP_METADATA),
    ).toEqual({
      field: "CREATED_AT",
      key: "createdAtMonth",
      granularity: "MONTH",
      rangeKey: "createdAtMonthRange",
    });
  });

  test("accepts camel-case date groups for Hasura snake-case dimensions", () => {
    const group = { field: "updatedAt", granularity: "month" as const };

    expect(validDataViewGroupStack([group], HASURA_SNAKE_METADATA)).toEqual([
      { field: "updated_at", granularity: "month" },
    ]);
    expect(dataViewGroupToAggregateDimension(group, HASURA_SNAKE_METADATA)).toEqual({
      field: "UPDATED_AT",
      key: "updated_at_month",
      granularity: "MONTH",
      rangeKey: "updated_at_month_range",
    });
    expect(
      bucketFilterForGroup(
        { key: { updated_at_month: "2026-02-01 00:00:00+00:00" }, count: 2 },
        group,
        HASURA_SNAKE_METADATA,
      ),
    ).toEqual({
      updated_at: {
        gte: "2026-02-01T00:00:00.000Z",
        lt: "2026-03-01T00:00:00.000Z",
      },
    });
  });

  test("fails fast when a grouped axis is not in resource metadata", () => {
    expect(() =>
      dataViewGroupToAggregateDimension({ field: "missing" }, GROUP_METADATA)
    ).toThrow('group dimension "missing"');
  });
});

describe("relation group display label (Odoo (id, display_name))", () => {
  test("groupLabelDimension carries the registered label axis", () => {
    expect(groupLabelDimension(PARTY_GROUP, GROUP_METADATA)).toEqual({
      field: "PARTY__DISPLAY_NAME",
      key: "party_DisplayName",
    });
  });

  test("groupLabelDimension is null without a label axis", () => {
    expect(groupLabelDimension(PARTY_GROUP, null)).toBeNull();
    expect(groupLabelDimension({ field: "status" }, GROUP_METADATA)).toBeNull();
  });

  test("bucketValueLabels renders the carried name, not the raw id", () => {
    const bucket = { key: { partyId: "4422", party_DisplayName: "PRG Iva" }, count: 1 };
    expect(bucketValueLabels(bucket, [PARTY_GROUP], GROUP_METADATA)).toEqual([
      "PRG Iva",
    ]);
  });

  test("bucketValueLabels needs resource metadata for grouped buckets", () => {
    const bucket = { key: { partyId: "pty_abc", party_DisplayName: "PRG Iva" }, count: 1 };
    expect(() => bucketValueLabels(bucket, [PARTY_GROUP], null)).toThrow(
      'group dimension "party"',
    );
  });
});

describe("bucketFilterForGroup", () => {
  test("turns date granularity buckets into range filters", () => {
    expect(
      bucketFilterForGroup(
        { key: { createdAtMonth: "2026-02-01 00:00:00+00:00" }, count: 2 },
        { field: "createdAt", granularity: "month" },
        GROUP_METADATA,
      ),
    ).toEqual({
      createdAt: {
        gte: "2026-02-01T00:00:00.000Z",
        lt: "2026-03-01T00:00:00.000Z",
      },
    });
  });

  test("keeps empty date buckets expandable as null filters", () => {
    expect(
      bucketFilterForGroup(
        { key: { createdAtMonth: "" }, count: 1 },
        { field: "createdAt", granularity: "month" },
        GROUP_METADATA,
      ),
    ).toEqual({ createdAt: { isNull: true } });
  });

  test("parses structured JSON bucket values for containment drill-down", () => {
    expect(
      bucketFilterForGroup(
        { key: { metadata: "{\"kind\":\"note\",\"flags\":[\"pinned\"]}" }, count: 1 },
        { field: "metadata" },
        GROUP_METADATA,
      ),
    ).toEqual({
      metadata: { jsonContains: { kind: "note", flags: ["pinned"] } },
    });
  });

  test("normalizes enum key buckets to write-side filter values", () => {
    expect(
      bucketFilterForGroup(
        { key: { serverStatus: "IN_REVIEW" }, count: 1 },
        { field: "status" },
        GROUP_METADATA,
      ),
    ).toEqual({ status: "in_review" });
  });
});
