import { describe, expect, test } from "vitest";
import type {
  ModelMetadata,
  Row,
  SchemaFieldMetadata,
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
  relationFieldInfo,
  relationListFieldInfo,
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

  test("derives group options from JSON path dimensions", () => {
    const messageMetadata: ModelMetadata = {
      typeName: "MessageType",
      fields: {},
      resource: {
        schemaName: "console",
        modelLabel: "messaging.Message",
        appLabel: "messaging",
        modelName: "message",
        publicIdField: "sqid",
        roots: {},
        typeNames: { node: "MessageType" },
        capabilities: ["list", "groups"],
        filterFields: [],
        orderFields: [],
        aggregateFields: ["id"],
        groupByFields: ["metadata.mailbox"],
        relationAxes: [],
        groupDimensions: [
          {
            field: "metadata.mailbox",
            input: "METADATA__MAILBOX",
            key: "metadata__mailbox",
            kind: "json",
          },
        ],
      },
    };

    expect(buildGroupOptions([], messageMetadata, null)).toEqual([
      {
        id: "metadata.mailbox",
        label: "Metadata Mailbox",
        group: { field: "metadata.mailbox" },
        type: "value",
      },
    ]);
  });
});

describe("relationFieldInfo / relationListFieldInfo", () => {
  const schema: SchemaFieldMetadata = {
    types: {
      TaxType: {
        typeName: "TaxType",
        recordRepresentation: "name",
        fields: {},
        rootFields: { list: "taxes", create: "insert_taxes_one" },
      },
      ProductVariantType: {
        typeName: "ProductVariantType",
        recordRepresentation: "displayName",
        fields: {},
        rootFields: { list: "product_variants" },
      },
      UnlistableType: { typeName: "UnlistableType", fields: {}, rootFields: {} },
      CompanyType: {
        typeName: "CompanyType",
        recordRepresentation: "name",
        fields: {},
        rootFields: { list: "companies" },
      },
    },
  };
  const model: ModelMetadata = {
    typeName: "JournalItemType",
    fields: {
      product: { name: "product", kind: "relation", relationTarget: "ProductVariantType" },
      taxes: { name: "taxes", kind: "list", scalar: "ID", relationTarget: "TaxType" },
      labels: { name: "labels", kind: "list", scalar: "String" },
      orphan: { name: "orphan", kind: "list", relationTarget: "UnlistableType" },
      // A to-one FK the node projects as a bare `ID!` scalar: a scalar leaf that
      // still carries a relation target + the scalar-id `select` widget.
      company: {
        name: "company",
        kind: "scalar",
        scalar: "ID",
        widget: "select",
        relationTarget: "CompanyType",
      },
      // The record's own opaque id — a bare `ID` scalar with no relation target.
      id: { name: "id", kind: "scalar", scalar: "ID" },
    },
  };

  test("resolves a to-one relation, but not a to-many, for relationFieldInfo", () => {
    expect(relationFieldInfo("product", model, schema)?.resource).toBe("ProductVariant");
    // An M2M is `kind: "list"`, so the to-one resolver ignores it (else it would
    // render a single picker over a many field).
    expect(relationFieldInfo("taxes", model, schema)).toBeNull();
  });

  test("resolves an ID-scalar FK as a scalar-id relation picker, but not a bare id", () => {
    // A `company` FK the node projects as a bare `ID!` still wires the picker/label
    // through the relation metadata, so the form gets a usable relation widget.
    const info = relationFieldInfo("company", model, schema);
    expect(info?.resource).toBe("Company");
    expect(info?.labelField).toBe("name");
    // Its metadata widget is `select` (not `many2one`), so the form selects it as a
    // scalar leaf — a valid detail query, never an object sub-selection.
    const [resolved] = fieldsWithMetadataDefaults([{ name: "company" }], model);
    expect(resolved?.widget).toBe("select");
    // The record's own bare `ID` scalar (no relation target) stays opaque.
    expect(relationFieldInfo("id", model, schema)).toBeNull();
  });

  test("resolves an M2M relation target for relationListFieldInfo", () => {
    const info = relationListFieldInfo("taxes", model, schema);
    expect(info?.resource).toBe("Tax");
    expect(info?.labelField).toBe("name");
    expect(info?.canCreate).toBe(true);
    // The to-many resolver ignores a to-one field.
    expect(relationListFieldInfo("product", model, schema)).toBeNull();
  });

  test("a plain string list (no relation target) stays a tag input, not a picker", () => {
    expect(relationListFieldInfo("labels", model, schema)).toBeNull();
  });

  test("an M2M whose target exposes no list root cannot be a picker", () => {
    expect(relationListFieldInfo("orphan", model, schema)).toBeNull();
  });
});

describe("money currencyField plumbing", () => {
  const metadata: ModelMetadata = {
    typeName: "InvoiceType",
    fields: {
      amountTotal: {
        name: "amountTotal",
        kind: "scalar",
        scalar: "Decimal",
        widget: "money",
        currencyField: "currency",
        label: "Total",
      },
    },
  };

  test("a bare column inherits the backend widget and currencyField from metadata", () => {
    const [column] = columnsWithMetadataDefaults<Row>([{ field: "amountTotal" }], metadata);
    expect(column?.widget).toBe("money");
    expect(column?.currencyField).toBe("currency");
  });

  test("an explicit column widget wins over the backend widget", () => {
    const [column] = columnsWithMetadataDefaults<Row>(
      [{ field: "amountTotal", widget: "float" }],
      metadata,
    );
    expect(column?.widget).toBe("float");
  });

  test("a bare column for an enum/boolean field inherits no kind-derived widget", () => {
    // List cells render enums, relations, and plain scalars natively; only an
    // explicit backend widget (like `money`) is inherited onto a column.
    const resolved = columnsWithMetadataDefaults<Row>(
      [{ field: "status" }, { field: "isStarred" }],
      NOTE_METADATA,
    );
    expect(resolved[0]?.widget).toBeUndefined();
    expect(resolved[1]?.widget).toBeUndefined();
  });

  test("a form field inherits the field's currencyField from metadata", () => {
    const [field] = fieldsWithMetadataDefaults([{ name: "amountTotal" }], metadata);
    expect(field?.currencyField).toBe("currency");
    expect(field?.widget).toBe("money");
  });

  test("an explicit descriptor currencyField wins over metadata", () => {
    const [field] = fieldsWithMetadataDefaults(
      [{ name: "amountTotal", currencyField: "order.currency" }],
      metadata,
    );
    expect(field?.currencyField).toBe("order.currency");
  });
});

describe("relation column read expansion", () => {
  const metadata: ModelMetadata = {
    typeName: "StockLevelType",
    fields: {
      product: {
        name: "product",
        kind: "relation",
        widget: "many2one",
        relationTarget: "ProductVariantType",
        relationObject: true,
        label: "Product",
      },
      // A to-one FK projected as a public-id scalar: `relation` semantics
      // (many2one widget, relation axis) but NOT a nested object — must stay a leaf.
      location: {
        name: "location",
        kind: "relation",
        widget: "many2one",
        relationTarget: "LocationType",
        label: "Location",
      },
      quantity: { name: "quantity", kind: "scalar", scalar: "Decimal" },
    },
  };
  const schema: SchemaFieldMetadata = {
    types: {
      ProductVariantType: {
        typeName: "ProductVariantType",
        recordRepresentation: "display_name",
        fields: {},
      },
      LocationType: {
        typeName: "LocationType",
        recordRepresentation: "name",
        fields: {},
      },
    },
  };

  test("a bare relation column reads its related type's label path, not a leaf object", () => {
    const [column] = columnsWithMetadataDefaults<Row>(
      [{ field: "product", header: "Product" }],
      metadata,
      schema,
    );
    expect(column?.field).toBe("product.display_name");
    // The label renders as a scalar, so the relation's many2one edit widget drops.
    expect(column?.widget).toBeUndefined();
    expect(column?.header).toBe("Product");
  });

  test("a relation column falls back to the related id when the type declares no representation", () => {
    const bare: SchemaFieldMetadata = {
      types: { ProductVariantType: { typeName: "ProductVariantType", fields: {} } },
    };
    const [column] = columnsWithMetadataDefaults<Row>([{ field: "product" }], metadata, bare);
    expect(column?.field).toBe("product.id");
  });

  test("without schema metadata a relation column still reads its id, never a leaf object", () => {
    const [column] = columnsWithMetadataDefaults<Row>([{ field: "product" }], metadata);
    expect(column?.field).toBe("product.id");
  });

  test("a to-one FK projected as a public-id scalar stays a leaf (not sub-selected)", () => {
    const [column] = columnsWithMetadataDefaults<Row>([{ field: "location" }], metadata, schema);
    // `location` is `kind: relation` but not `relationObject` — selecting
    // `location { name }` would fail ("ID has no subfields"), so it stays a leaf.
    expect(column?.field).toBe("location");
  });

  test("an explicit dotted relation path and scalar columns are left untouched", () => {
    const resolved = columnsWithMetadataDefaults<Row>(
      [{ field: "product.default_code" }, { field: "quantity" }],
      metadata,
      schema,
    );
    expect(resolved[0]?.field).toBe("product.default_code");
    expect(resolved[1]?.field).toBe("quantity");
  });

  test("a column that pins its own render or widget keeps its relation field verbatim", () => {
    const [rendered] = columnsWithMetadataDefaults<Row>(
      [{ field: "product", render: () => null }],
      metadata,
      schema,
    );
    expect(rendered?.field).toBe("product");
  });
});
