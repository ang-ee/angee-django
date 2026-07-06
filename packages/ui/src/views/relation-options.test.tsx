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

import { useRelationLanes, useRelationOptions } from "./relation-options";
import type { RelationFieldInfo } from "./model-metadata-defaults";

const sdkMocks = vi.hoisted(() => ({
  useListOptions: null as {
    resource?: string;
    dataProviderName?: string;
    meta?: { fields?: unknown };
  } | null,
  rows: [
    { id: "vnd_1", display_name: "Acme" },
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
    { id: "vnd_1", display_name: "Acme" },
  ];
  sdkMocks.refetch.mockClear();
});

describe("useRelationOptions", () => {
  test("requests public id with the label field so rows become selectable options", () => {
    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationOptionsProbe relation={vendorRelation} />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.resource).toBe("vendors");
    expect(sdkMocks.useListOptions?.dataProviderName).toBe("console");
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "display_name"]);
    expect(screen.getByText("vnd_1: Acme")).toBeTruthy();
  });

  test("returns relation lanes in server order without client label sorting", () => {
    sdkMocks.rows = [
      { id: "stg_30", name: "Proposal" },
      { id: "stg_10", name: "New" },
      { id: "stg_20", name: "Qualified" },
    ];

    render(
      <ModelMetadataProvider metadata={metadata}>
        <RelationLanesProbe relation={stageRelation} />
      </ModelMetadataProvider>,
    );

    expect(sdkMocks.useListOptions?.resource).toBe("stages");
    expect(sdkMocks.useListOptions?.meta?.fields).toEqual(["id", "name"]);
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "stg_30: Proposal",
      "stg_10: New",
      "stg_20: Qualified",
    ]);
  });
});

function RelationOptionsProbe({
  relation,
}: {
  relation: RelationFieldInfo;
}) {
  const { options } = useRelationOptions(relation);
  return (
    <ul>
      {options.map((option) => (
        <li key={option.value}>{`${option.value}: ${option.label}`}</li>
      ))}
    </ul>
  );
}

function RelationLanesProbe({
  relation,
}: {
  relation: RelationFieldInfo;
}) {
  const { lanes } = useRelationLanes(relation);
  return (
    <ol>
      {lanes.map((lane) => (
        <li key={lane.value}>{`${lane.value}: ${lane.label}`}</li>
      ))}
    </ol>
  );
}

const vendorRelation: RelationFieldInfo = {
  resource: "integrate.Vendor",
  labelField: "display_name",
  canCreate: false,
};

const stageRelation: RelationFieldInfo = {
  resource: "crm.Stage",
  labelField: "name",
  canCreate: false,
};

const metadata: SchemaFieldMetadata = schemaFieldMetadataFromDataResources([
  {
    schemaName: "console",
    modelLabel: "integrate.Vendor",
    appLabel: "integrate",
    modelName: "vendor",
    publicIdField: "sqid",
    roots: { list: "vendors" },
    typeNames: { node: "VendorType" },
    capabilities: ["list"],
    fields: [
      {
        name: "id",
        kind: "scalar",
        scalar: "ID",
        readable: true,
        filterable: true,
        sortable: false,
        aggregatable: true,
        groupable: false,
        creatable: false,
        updatable: false,
        requiredOnCreate: false,
      },
      {
        name: "display_name",
        kind: "scalar",
        scalar: "String",
        readable: true,
        filterable: true,
        sortable: true,
        aggregatable: false,
        groupable: false,
        creatable: true,
        updatable: true,
        requiredOnCreate: true,
      },
    ],
    filterFields: ["id", "display_name"],
    orderFields: ["display_name"],
    aggregateFields: ["id"],
    groupByFields: [],
    relationAxes: [],
  },
  {
    schemaName: "console",
    modelLabel: "crm.Stage",
    appLabel: "crm",
    modelName: "stage",
    publicIdField: "sqid",
    roots: { list: "stages" },
    typeNames: { node: "StageType" },
    recordRepresentation: "name",
    capabilities: ["list"],
    fields: [
      {
        name: "id",
        kind: "scalar",
        scalar: "ID",
        readable: true,
        filterable: true,
        sortable: false,
        aggregatable: true,
        groupable: false,
        creatable: false,
        updatable: false,
        requiredOnCreate: false,
      },
      {
        name: "name",
        kind: "scalar",
        scalar: "String",
        readable: true,
        filterable: true,
        sortable: true,
        aggregatable: false,
        groupable: false,
        creatable: true,
        updatable: true,
        requiredOnCreate: true,
      },
    ],
    filterFields: ["id", "name"],
    orderFields: ["position", "id"],
    aggregateFields: ["id"],
    groupByFields: [],
    relationAxes: [],
  },
]);
