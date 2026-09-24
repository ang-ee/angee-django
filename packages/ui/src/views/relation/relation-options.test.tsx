import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
// @vitest-environment happy-dom

import {
  cleanup,
  render,
  screen,
} from "@testing-library/react";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { afterEach, describe, expect, test, vi } from "vitest";

import { useRelationOptions } from "./relation-options";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";

const sdkMocks = vi.hoisted(() => ({
  useListOptions: null as {
    resource?: string;
    dataProviderName?: string;
    filters?: readonly unknown[];
    sorters?: readonly unknown[];
    meta?: { fields?: unknown };
  } | null,
  rows: [
    { id: "rev_1", display_name: "Acme" },
  ] as Record<string, unknown>[],
  refetch: vi.fn(),
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useList: (options?: {
      resource?: string;
      dataProviderName?: string;
      filters?: readonly unknown[];
      sorters?: readonly unknown[];
      meta?: { fields?: unknown };
    }) => {
      sdkMocks.useListOptions = options ?? null;
      return {
        result: {
          data: sdkMocks.rows,
          total: sdkMocks.rows.length,
        },
        query: {
          isFetching: false,
          refetch: sdkMocks.refetch,
        },
      };
    },
  };
});

afterEach(() => {
  cleanup();
  sdkMocks.useListOptions = null;
  sdkMocks.rows = [
    { id: "rev_1", display_name: "Acme" },
  ];
  sdkMocks.refetch.mockClear();
});

describe("useRelationOptions", () => {
  test("requests public id with the label field so rows become selectable options", () => {
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={reviewerRelation} />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.resource).toBe("reviewers");
    expect(sdkMocks.useListOptions?.dataProviderName).toBe("console");
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "display_name"]);
    expect(screen.getByText("rev_1: Acme")).toBeTruthy();
  });

  test("returns relation options in server order without client label sorting", () => {
    sdkMocks.rows = [
      { id: "stg_30", name: "Proposal" },
      { id: "stg_10", name: "New" },
      { id: "stg_20", name: "Qualified" },
    ];

    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe
          relation={stageRelation}
          sorters={[{ field: "position", order: "asc" }]}
        />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.resource).toBe("stages");
    expect(sdkMocks.useListOptions?.sorters).toEqual([
      { field: "position", order: "asc" },
    ]);
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "name"]);
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "stg_30: Proposal",
      "stg_10: New",
      "stg_20: Qualified",
    ]);
  });

  test("searches a declared text field when the computed label is not filterable", () => {
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={reviewerRelation} searchText="admin" />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([
      {
        operator: "or",
        value: [
          { field: "username", operator: "contains", value: "admin" },
        ],
      },
    ]);
  });

  test("falls back to the searchable record representation", () => {
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={stageRelation} searchText="new" />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([
      {
        operator: "or",
        value: [
          { field: "name", operator: "contains", value: "new" },
        ],
      },
    ]);
  });

  test("honors an explicit empty search-field override", () => {
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe
          relation={currencyRelation}
          searchFields={[]}
          searchText="USD"
        />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([]);
  });

  test("searches every resource-authored record field while retaining its computed label", () => {
    sdkMocks.rows = [
      { id: "cur_1", display_name: "USD — US Dollar" },
    ];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={currencyRelation} searchText="USD" />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([
      {
        operator: "or",
        value: [
          { field: "code", operator: "contains", value: "USD" },
          { field: "name", operator: "contains", value: "USD" },
        ],
      },
    ]);
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "display_name"]);
    expect(screen.getByText("cur_1: USD — US Dollar")).toBeTruthy();
  });

  test("does not manufacture a search filter when the computed label has no text comparison", () => {
    sdkMocks.rows = [
      { id: "svc_1", display_name: "Automation" },
    ];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={unsearchableRelation} searchText="admin" />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([]);
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "display_name"]);
    expect(screen.getByText("svc_1: Automation")).toBeTruthy();
  });
});

function RelationOptionsProbe({
  relation,
  searchFields,
  searchText,
  sorters,
}: {
  relation: RelationFieldInfo;
  searchFields?: readonly string[];
  searchText?: string;
  sorters?: readonly { field: string; order: "asc" | "desc" }[];
}) {
  const { options } = useRelationOptions(relation, { searchFields, searchText, sorters });
  return (
    <ul>
      {options.map((option) => (
        <li key={option.value}>{`${option.value}: ${option.label}`}</li>
      ))}
    </ul>
  );
}

const reviewerRelation: RelationFieldInfo = {
  resource: "example.Reviewer",
  labelField: "display_name",
  canCreate: false,
};

const stageRelation: RelationFieldInfo = {
  resource: "crm.Stage",
  labelField: "name",
  canCreate: false,
};

const unsearchableRelation: RelationFieldInfo = {
  resource: "iam.ServiceAccount",
  labelField: "display_name",
  canCreate: false,
};

const currencyRelation: RelationFieldInfo = {
  resource: "money.Currency",
  labelField: "display_name",
  canCreate: false,
};

const searchableField = (name: string) => testQueryField(name, {
  filter: {
    field: name,
    scalar: "String",
    values: [],
    operators: ["exact", "iContains"],
  },
});

const metadata: SchemaFieldMetadata = schemaFieldMetadataFromDataResources([
  testDataResource("example.Reviewer", {
    recordRepresentation: "display_name",
    recordSearchFields: ["username"],
    query: testResourceQuery({
      fields: {
        display_name: testQueryField("display_name", { filter: null }),
        username: searchableField("username"),
      },
    }),
  }),
  testDataResource("crm.Stage", {
    query: testResourceQuery({ fields: { name: searchableField("name") } }),
    recordRepresentation: "name",
  }),
  testDataResource("iam.ServiceAccount", {
    roots: { list: "service_accounts" },
    query: testResourceQuery({
      fields: {
        display_name: testQueryField("display_name", { filter: null }),
        hidden: searchableField("hidden"),
      },
    }),
    recordRepresentation: "display_name",
  }),
  testDataResource("money.Currency", {
    roots: { list: "currencies" },
    query: testResourceQuery({
      fields: {
        display_name: testQueryField("display_name", { filter: null }),
        code: searchableField("code"),
        name: searchableField("name"),
      },
    }),
    recordRepresentation: "display_name",
    recordSearchFields: ["code", "name"],
  }),
]);
