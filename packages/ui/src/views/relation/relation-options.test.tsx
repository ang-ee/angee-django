import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
// @vitest-environment happy-dom

import {
  cleanup,
  render,
  screen,
} from "@testing-library/react";
import { gql } from "graphql-tag";
import {
  ModelMetadataProvider,
  schemaFieldMetadataFromDataResources,
  type DataResourceFieldMetadata,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { afterEach, describe, expect, test, vi } from "vitest";

import { useRelationOptions } from "./relation-options";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { AppRuntimeProvider } from "../../runtime";

const sdkMocks = vi.hoisted(() => ({
  useListOptions: null as {
    resource?: string;
    dataProviderName?: string;
    filters?: readonly unknown[];
    sorters?: readonly unknown[];
    pagination?: unknown;
    meta?: { fields?: unknown; gqlQuery?: ReturnType<typeof gql>; gqlVariables?: Record<string, unknown> };
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
      pagination?: unknown;
      meta?: { fields?: unknown; gqlQuery?: ReturnType<typeof gql>; gqlVariables?: Record<string, unknown> };
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
    expect(sdkMocks.useListOptions?.pagination).toEqual({ mode: "server", currentPage: 1, pageSize: 200 });
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "display_name"]);
    expect(screen.getByText("rev_1: Acme")).toBeTruthy();
  });

  test("projects declared description and status fields into picker options", () => {
    sdkMocks.rows = [{
      id: "rev_1",
      display_name: "Acme Bank",
      description: "Checking •••• 4242",
      verification_state: "VERIFIED",
    }];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <AppRuntimeProvider runtime={{ statusTones: { verified: "success" } }}>
          <RelationOptionsProbe relation={reviewerRelation}
            optionDescriptionField="description" optionStatusField="verification_state" />
        </AppRuntimeProvider>
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.meta?.fields).toEqual([
      "id", "display_name", "description", "verification_state",
    ]);
    expect(screen.getByText("rev_1: Acme Bank | Checking •••• 4242 | Verified (success)")).toBeTruthy();
  });

  test("binds a computed option status to its surrounding context", () => {
    sdkMocks.rows = [{ id: "rev_1", display_name: "Acme Bank", verification_state: "VERIFIED" }];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={reviewerRelation}
          optionStatusField="verification_state"
          optionStatusArguments={[{ name: "company_id", type: "ID!", value: "company_7" }]} />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.meta?.gqlVariables).toEqual({ selection_0_0: "company_7" });
    expect(sdkMocks.useListOptions!.meta!.gqlQuery!.loc?.source.body)
      .toContain("verification_state(company_id: $selection_0_0)");
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
    sdkMocks.rows = [{ id: "rev_1", display_name: "Site owner" }];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={reviewerRelation} searchText="  admin  " />
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
    expect(screen.getByText("rev_1: Site owner")).toBeTruthy();
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

  test("matches loaded labels when search fields are explicitly empty", () => {
    sdkMocks.rows = [
      { id: "cur_1", display_name: "USD — US Dollar" },
      { id: "cur_2", display_name: "EUR — Euro" },
    ];
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
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "cur_1: USD — US Dollar",
    ]);
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

  test("matches loaded labels when the target has no executable text search", () => {
    sdkMocks.rows = [
      { id: "svc_admin", display_name: "Automation" },
      { id: "svc_2", display_name: "Admin service" },
      { id: "svc_3", display_name: "System ADMIN" },
    ];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={unsearchableRelation} searchText="  AdMiN  " />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([]);
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "display_name"]);
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "svc_2: Admin service",
      "svc_3: System ADMIN",
    ]);
  });

  test("returns no options when no loaded label matches", () => {
    sdkMocks.rows = [{ id: "svc_1", display_name: "Automation" }];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={unsearchableRelation} searchText="zzzzqq" />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([]);
    expect(screen.queryAllByRole("listitem")).toEqual([]);
  });

  test.each([undefined, "", "  "])("keeps the loaded page unchanged for empty search text (%s)", (searchText) => {
    sdkMocks.rows = [
      { id: "svc_1", display_name: "Automation" },
      { id: "svc_2", display_name: "Admin service" },
    ];
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={unsearchableRelation} searchText={searchText} />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.filters).toEqual([]);
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "svc_1: Automation",
      "svc_2: Admin service",
    ]);
  });
});

function RelationOptionsProbe({
  relation,
  searchFields,
  searchText,
  sorters,
  optionDescriptionField,
  optionStatusField,
  optionStatusArguments,
}: {
  relation: RelationFieldInfo;
  searchFields?: readonly string[];
  searchText?: string;
  sorters?: readonly { field: string; order: "asc" | "desc" }[];
  optionDescriptionField?: string;
  optionStatusField?: string;
  optionStatusArguments?: readonly { name: string; type: string; value: unknown }[];
}) {
  const { options } = useRelationOptions(relation, {
    searchFields, searchText, sorters, optionDescriptionField, optionStatusField, optionStatusArguments,
  });
  return (
    <ul>
      {options.map((option) => (
        <li key={option.value}>{`${option.value}: ${option.label}${option.description ? ` | ${option.description}` : ""}${
          option.status ? ` | ${option.status.label} (${option.status.tone})` : ""}`}</li>
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

const displayField = (
  name: string,
  kind: DataResourceFieldMetadata["kind"] = "scalar",
  values?: DataResourceFieldMetadata["values"],
): DataResourceFieldMetadata => ({
  name,
  kind,
  scalar: "String",
  ...(values ? { values } : {}),
  readable: true,
  aggregatable: false,
  creatable: false,
  updatable: false,
  requiredOnCreate: false,
});

const metadata: SchemaFieldMetadata = schemaFieldMetadataFromDataResources([
  testDataResource("example.Reviewer", {
    roots: { aggregate: "reviewers_aggregate" },
    typeNames: { filter: "ReviewerBoolExp", order: "ReviewerOrderBy" },
    recordRepresentation: "display_name",
    recordSearchFields: ["username"],
    fields: [
      displayField("display_name"),
      displayField("description"),
      displayField("verification_state", "enum", [{ value: "VERIFIED", description: "Verified" }]),
    ],
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
