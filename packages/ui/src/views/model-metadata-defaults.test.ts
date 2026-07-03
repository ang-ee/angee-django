import { describe, expect, test } from "vitest";
import type {
  ModelMetadata,
  Row,
} from "@angee/metadata";

import {
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  resolveResourceViewGroup,
} from "./resource-view-utils";
import {
  columnsWithMetadataDefaults,
  fieldsWithMetadataDefaults,
} from "./model-metadata-defaults";
import { RESOURCE_VIEW_GROUP_GRANULARITIES } from "./resource-view-model";
import type { ColumnDescriptor, FieldDescriptor } from "./page";

const NOTE_METADATA: ModelMetadata = {
  typeName: "NoteType",
  recordRepresentation: "title",
  fields: {
    title: { name: "title", kind: "scalar", scalar: "String", label: "Title" },
    status: {
      name: "status",
      kind: "enum",
      enumName: "NoteStatus",
      label: "Status",
      values: [
        { value: "DRAFT", description: "Draft" },
        { value: "IN_REVIEW" },
        { value: "ACTIVE" },
      ],
    },
    isStarred: { name: "isStarred", kind: "scalar", scalar: "Boolean" },
    createdAt: { name: "createdAt", kind: "scalar", scalar: "DateTime" },
    updatedAt: { name: "updatedAt", kind: "scalar", scalar: "DateTime" },
    wordCount: { name: "wordCount", kind: "scalar", scalar: "Int" },
  },
  resource: {
    schemaName: "public",
    modelLabel: "notes.Note",
    appLabel: "notes",
    modelName: "note",
    publicIdField: "sqid",
    roots: {},
    typeNames: {
      node: "NoteType",
    },
    capabilities: ["list", "filter", "order", "aggregate", "groups"],
    filterFields: ["status", "isStarred", "title", "updatedAt"],
    orderFields: ["title", "status", "updatedAt", "createdAt", "wordCount"],
    aggregateFields: ["id", "wordCount"],
    groupByFields: ["status", "updatedAt", "createdAt"],
    relationAxes: [],
  },
};

// The widget options enumOptions derives: SDL description, else humanized value.
const STATUS_OPTIONS = [
  { value: "DRAFT", label: "Draft" },
  { value: "IN_REVIEW", label: "In Review" },
  { value: "ACTIVE", label: "Active" },
];

describe("resource metadata defaults", () => {
  const columns: readonly ColumnDescriptor<Row>[] = [
    { field: "title" },
    { field: "status", widget: "statusBadge" },
    { field: "updatedAt" },
    { field: "wordCount" },
  ];

  test("applies column and field labels plus enum options without overwriting props", () => {
    const resolvedColumns = columnsWithMetadataDefaults(
      [
        ...columns,
        {
          field: "status",
          header: "Lifecycle",
          widget: "statusBadge",
          options: [{ value: "CUSTOM", label: "Custom" }],
        },
      ],
      NOTE_METADATA,
    );

    expect(resolvedColumns[0]?.header).toBe("Title");
    expect(resolvedColumns[1]?.header).toBe("Status");
    expect(resolvedColumns[1]?.options).toEqual(STATUS_OPTIONS);
    expect(resolvedColumns[2]?.header).toBe("Updated At");
    expect(resolvedColumns[4]?.header).toBe("Lifecycle");
    expect(resolvedColumns[4]?.options).toEqual([
      { value: "CUSTOM", label: "Custom" },
    ]);

    const fields: readonly FieldDescriptor[] = [
      { name: "title", widget: "text" },
      { name: "status", widget: "statusbar" },
      {
        name: "status",
        widget: "select",
        label: "State",
        options: [{ value: "CUSTOM", label: "Custom" }],
      },
    ];
    const resolvedFields = fieldsWithMetadataDefaults(fields, NOTE_METADATA);

    expect(resolvedFields[0]?.label).toBe("Title");
    expect(resolvedFields[1]?.label).toBe("Status");
    expect(resolvedFields[1]?.options).toEqual(STATUS_OPTIONS);
    expect(resolvedFields[2]?.label).toBe("State");
    expect(resolvedFields[2]?.options).toEqual([
      { value: "CUSTOM", label: "Custom" },
    ]);
  });

  test("resolves the default widget for a bare field from its SDL kind/scalar", () => {
    const policyMetadata: ModelMetadata = {
      typeName: "OAuthClientType",
      fields: {
        isEnabled: { name: "isEnabled", kind: "scalar", scalar: "Boolean" },
        environment: { name: "environment", kind: "scalar", scalar: "String" },
        status: {
          name: "status",
          kind: "enum",
          enumName: "ConfigState",
          values: [{ value: "READY" }],
        },
        defaultScopes: { name: "defaultScopes", kind: "list", scalar: "String" },
        vendor: { name: "vendor", kind: "relation", relationTarget: "VendorType" },
      },
    };
    const resolved = fieldsWithMetadataDefaults(
      [
        { name: "isEnabled" },
        { name: "environment" },
        { name: "status" },
        { name: "defaultScopes" },
        { name: "vendor" },
        { name: "isEnabled", widget: "booleanBadge" },
      ],
      policyMetadata,
    );
    expect(resolved[0]?.widget).toBe("switch"); // Boolean → switch (was text → submitted "")
    expect(resolved[1]?.widget).toBeUndefined(); // plain String → FormView text fallback
    expect(resolved[2]?.widget).toBe("select"); // enum → select, with options
    expect(resolved[2]?.options).toHaveLength(1);
    expect(resolved[3]?.widget).toBe("tagInput"); // string list → tag input
    expect(resolved[4]?.widget).toBe("many2one"); // relation → picker
    expect(resolved[5]?.widget).toBe("booleanBadge"); // explicit widget is preserved
  });

  test("derives list filter fields, enum filter chips, and group options", () => {
    const resolvedColumns = columnsWithMetadataDefaults(columns, NOTE_METADATA);
    const filterFields = buildFilterFields(resolvedColumns, [], NOTE_METADATA);

    expect(filterFields).toEqual([
      {
        id: "title",
        field: "title",
        label: "Title",
        type: "text",
      },
      {
        id: "status",
        field: "status",
        label: "Status",
        type: "selection",
        options: STATUS_OPTIONS,
      },
      {
        id: "updatedAt",
        field: "updatedAt",
        label: "Updated At",
        type: "datetime",
      },
      {
        id: "isStarred",
        field: "isStarred",
        label: "Is Starred",
        type: "boolean",
      },
    ]);

    expect(buildFilterOptions(resolvedColumns, [], filterFields)).toEqual([
      {
        id: "status:DRAFT",
        label: "Draft",
        chipLabel: "Draft",
        filter: { status: { exact: "DRAFT" } },
      },
      {
        id: "status:IN_REVIEW",
        label: "In Review",
        chipLabel: "In Review",
        filter: { status: { exact: "IN_REVIEW" } },
      },
      {
        id: "status:ACTIVE",
        label: "Active",
        chipLabel: "Active",
        filter: { status: { exact: "ACTIVE" } },
      },
    ]);

    expect(buildGroupOptions(resolvedColumns, NOTE_METADATA, null)).toEqual([
      {
        id: "status",
        label: "Status",
        group: { field: "status" },
        type: "value",
      },
      {
        id: "updatedAt",
        label: "Updated",
        group: { field: "updatedAt", granularity: "day" },
        type: "date",
        granularities: RESOURCE_VIEW_GROUP_GRANULARITIES,
      },
      {
        id: "createdAt",
        label: "Created",
        group: { field: "createdAt", granularity: "day" },
        type: "date",
        granularities: RESOURCE_VIEW_GROUP_GRANULARITIES,
      },
    ]);
  });

  test("does not derive server selection filters from the current page rows", () => {
    const metadata: ModelMetadata = {
      typeName: "TicketType",
      fields: {
        status: {
          name: "status",
          kind: "enum",
          enumName: "TicketStatus",
          label: "Status",
          values: [],
        },
      },
      resource: {
        schemaName: "public",
        modelLabel: "support.Ticket",
        appLabel: "support",
        modelName: "ticket",
        publicIdField: "sqid",
        roots: {},
        typeNames: { node: "TicketType" },
        capabilities: ["list", "filter"],
        filterFields: ["status"],
        orderFields: [],
        aggregateFields: ["id"],
        groupByFields: [],
        relationAxes: [],
      },
    };
    const rows = [
      { id: "one", status: "OPEN" },
      { id: "two", status: "CLOSED" },
    ];

    const filterFields = buildFilterFields([{ field: "status" }], rows, metadata);

    expect(filterFields).toEqual([{
      id: "status",
      field: "status",
      label: "Status",
      type: "selection",
      options: [],
    }]);
    expect(buildFilterOptions([{ field: "status" }], rows, filterFields)).toEqual([]);
  });

  test("keeps local row selection filters row-derived", () => {
    const rows = [
      { id: "one", status: "OPEN" },
      { id: "two", status: "CLOSED" },
    ];
    const filterFields = buildFilterFields([{ field: "status" }], rows, null);

    expect(filterFields).toEqual([{
      id: "status",
      field: "status",
      label: "Status",
      type: "selection",
      options: [
        { value: "CLOSED", label: "Closed" },
        { value: "OPEN", label: "Open" },
      ],
    }]);
    expect(buildFilterOptions([{ field: "status" }], rows, filterFields)).toEqual([
      {
        id: "status:CLOSED",
        label: "Closed",
        chipLabel: "Closed",
        filter: { status: { exact: "CLOSED" } },
      },
      {
        id: "status:OPEN",
        label: "Open",
        chipLabel: "Open",
        filter: { status: { exact: "OPEN" } },
      },
    ]);
  });

  test("derives relation label group options from data-query relation metadata", () => {
    const handleMetadata: ModelMetadata = {
      typeName: "HandleType",
      fields: {
        party: {
          name: "party",
          kind: "relation",
          label: "Contact",
          relationTarget: "PartyType",
          relationFilter: {
            field: "party",
            mode: "lookup",
            aggregateKey: "partyId",
            labelKey: "party_DisplayName",
          },
        },
      },
      resource: {
        schemaName: "public",
        modelLabel: "parties.Handle",
        appLabel: "parties",
        modelName: "handle",
        publicIdField: "sqid",
        roots: {},
        typeNames: { node: "HandleType" },
        capabilities: ["list", "groups"],
        filterFields: ["party"],
        orderFields: [],
        aggregateFields: ["id"],
        groupByFields: ["party", "party_DisplayName"],
        relationAxes: [],
      },
    };

    expect(
      buildGroupOptions(
        [{ field: "party.displayName", header: "Contact" }],
        handleMetadata,
        null,
      ),
    ).toEqual([
      {
        id: "party.displayName",
        label: "Contact",
        group: {
          field: "party.displayName",
          aggregateField: "party",
          aggregateKey: "partyId",
        },
        type: "value",
      },
    ]);
    expect(
      resolveResourceViewGroup({ field: "party.displayName" }, handleMetadata),
    ).toEqual({
      field: "party.displayName",
      aggregateField: "party",
      aggregateKey: "partyId",
    });
    expect(
      buildGroupOptions([], handleMetadata, { field: "party.displayName" }),
    ).toEqual([
      {
        id: "party.displayName",
        label: "Contact",
        group: {
          field: "party.displayName",
          aggregateField: "party",
          aggregateKey: "partyId",
        },
        type: "value",
      },
    ]);
  });

  test("derives scalar group alias options from resource metadata", () => {
    const integrationMetadata: ModelMetadata = {
      typeName: "IntegrationType",
      fields: {
        implCategory: {
          name: "implCategory",
          kind: "scalar",
          scalar: "String",
          label: "Implementation",
        },
        implClass: {
          name: "implClass",
          kind: "enum",
          enumName: "IntegrationImplsImpl",
          label: "Impl Class",
          values: [{ value: "NONE", description: "None" }],
        },
        status: { name: "status", kind: "scalar", scalar: "String", label: "Status" },
      },
      resource: {
        schemaName: "console",
        modelLabel: "integrate.Integration",
        appLabel: "integrate",
        modelName: "integration",
        publicIdField: "sqid",
        roots: {},
        typeNames: { node: "IntegrationType" },
        capabilities: ["list", "groups"],
        filterFields: [],
        orderFields: [],
        aggregateFields: ["id"],
        groupByFields: ["implClass", "status"],
        relationAxes: [],
        groupAliases: [
          {
            field: "implCategory",
            aggregateField: "implClass",
            aggregateKey: "implClass",
          },
        ],
      },
    };

    expect(buildGroupOptions([], integrationMetadata, null)).toEqual([
      {
        id: "implCategory",
        label: "Implementation",
        group: {
          field: "implCategory",
          aggregateField: "implClass",
          aggregateKey: "implClass",
        },
        type: "value",
      },
      {
        id: "status",
        label: "Status",
        group: { field: "status" },
        type: "value",
      },
    ]);
    expect(resolveResourceViewGroup({ field: "implCategory" }, integrationMetadata)).toEqual({
      field: "implCategory",
      aggregateField: "implClass",
      aggregateKey: "implClass",
    });
  });
});
