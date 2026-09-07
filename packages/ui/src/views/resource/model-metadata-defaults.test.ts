import { describe, expect, test } from "vitest";
import type {
  DataResourceFieldMetadata,
  DataResourceMetadata,
  ModelFieldMetadata,
  ModelMetadata,
  Row,
} from "@angee/metadata";
import {
  RelationRepresentationError,
  rowValueAtPath,
  schemaFieldMetadataFromDataResources,
} from "@angee/metadata";
import { testDataResource, testResourceQuery, testQueryField, testQueryAxis } from "@angee/metadata/testing";
import { refineFieldsFromPaths } from "@angee/refine";

import {
  buildFilterFields,
  buildFilterOptions,
  buildGroupOptions,
  resolveResourceViewGroup,
  validResourceViewGroupStack,
} from "./resource-view-utils";
import {
  columnsWithMetadataDefaults,
  fieldsWithMetadataDefaults,
  relationFieldInfo,
  relationListFieldInfo,
} from "./model-metadata-defaults";
const DATE_EXTRACTIONS = ["day", "week", "month", "quarter", "year"];
import { requestedFieldPaths } from "./resource-view-codecs";
import type { ColumnDescriptor, FieldDescriptor } from "../page";

const STATUS_VALUES = [{ value: "DRAFT", description: "Draft" }, { value: "IN_REVIEW" }, { value: "ACTIVE" }];
const dateAxis = (field: string) => testQueryAxis(field, {
  kind: "date", server: { input: field.toUpperCase(), key: field },
  extractions: DATE_EXTRACTIONS.map((name) => ({ name, input: name.toUpperCase(), key: `${field}_${name}` })),
});
const NOTE_METADATA = canonicalModel({
  title: { name: "title", kind: "scalar", scalar: "String" },
  status: { name: "status", kind: "enum", values: STATUS_VALUES },
  isStarred: { name: "isStarred", kind: "scalar", scalar: "Boolean" },
  createdAt: { name: "createdAt", kind: "scalar", scalar: "DateTime" },
  updatedAt: { name: "updatedAt", kind: "scalar", scalar: "DateTime" },
  wordCount: { name: "wordCount", kind: "scalar", scalar: "Int" },
}, testDataResource("notes.Note", {
  schemaName: "public", recordRepresentation: "title",
  query: testResourceQuery({ fields: {
    title: testQueryField("title", { filter: { field: "title", scalar: "String", values: [], operators: ["contains", "iContains", "isNull"] }, sort: { field: "title" } }),
    status: testQueryField("status", { kind: "enum", values: STATUS_VALUES, filter: { field: "status", scalar: "Enum", values: STATUS_VALUES, operators: ["exact", "inList", "isNull"] } }),
    isStarred: testQueryField("isStarred", { scalar: "Boolean", filter: { field: "isStarred", scalar: "Boolean", values: [], operators: ["exact", "isNull"] } }),
    updatedAt: testQueryField("updatedAt", { scalar: "DateTime", filter: { field: "updatedAt", scalar: "DateTime", values: [], operators: ["gte", "lt", "isNull"] }, sort: { field: "updatedAt" } }),
    createdAt: testQueryField("createdAt", { scalar: "DateTime", filter: null, sort: { field: "createdAt" } }),
    wordCount: testQueryField("wordCount", { scalar: "Int", filter: null, sort: { field: "wordCount" } }),
  }, axes: {
    status: testQueryAxis("status", { server: { input: "STATUS", key: "status" } }),
    updatedAt: dateAxis("updatedAt"), createdAt: dateAxis("createdAt"),
  } }),
}));
const MESSAGE_METADATA = canonicalModel({
  sender: { name: "sender", kind: "relation", relationModelLabel: "messaging.Handle", relationObject: true },
  status: { name: "status", kind: "enum", values: [{ value: "SENT" }] },
}, testDataResource("messaging.Message", { query: testResourceQuery({
  fields: {
    sender: testQueryField("sender", { kind: "relation", scalar: "ID", relation: { model: "messaging.Handle", identityPath: "sender.id", labelPath: "sender.display_name" } }),
    status: testQueryField("status"),
  }, axes: {
    sender: testQueryAxis("sender", {
      kind: "relation", identityPath: "sender.id", labelPath: "sender.display_name", paths: ["sender.id", "sender.display_name"],
      server: { input: "SENDER", key: "sender_id", labelInput: "SENDER__DISPLAY_NAME", labelKey: "sender__display_name" },
      drill: { kind: "identity", field: "sender", valueKey: "sender_id", nullMode: "isNull", valueMap: [] },
    }),
    status: testQueryAxis("status", { server: { input: "STATUS", key: "status" } }),
  },
}) }));

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
    const policyMetadata = canonicalModel({
        isEnabled: { name: "isEnabled", kind: "scalar", scalar: "Boolean" },
        environment: { name: "environment", kind: "scalar", scalar: "String" },
        status: {
          name: "status",
          kind: "enum",
          values: [{ value: "READY" }],
        },
        defaultScopes: { name: "defaultScopes", kind: "list", scalar: "String" },
        vendor: { name: "vendor", kind: "relation", relationModelLabel: "Vendor" },
      }, testDataResource("policies.Policy"));
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

    expect(filterFields).toMatchObject([
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

    expect(filterFields.find((field) => field.id === "title")?.operators).toEqual(["contains", "iContains", "isNull", "isNotNull"]);
    expect(filterFields.find((field) => field.id === "status")?.operators).toEqual(["exact", "inList", "isNull", "isNotNull"]);

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
        label: "Updated At",
        group: { field: "updatedAt", granularity: "day" },
        type: "date",
        granularities: DATE_EXTRACTIONS,
      },
      {
        id: "createdAt",
        label: "Created",
        group: { field: "createdAt", granularity: "day" },
        type: "date",
        granularities: DATE_EXTRACTIONS,
      },
    ]);
  });

  test("does not derive server selection filters from the current page rows", () => {
    const metadata = canonicalModel({ status: { name: "status", kind: "enum", values: [] } },
      testDataResource("support.Ticket", { query: testResourceQuery({ fields: { status: testQueryField("status") } }) }));
    const rows = [
      { id: "one", status: "OPEN" },
      { id: "two", status: "CLOSED" },
    ];

    const filterFields = buildFilterFields([{ field: "status" }], rows, metadata);

    expect(filterFields).toMatchObject([{
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

    expect(filterFields).toMatchObject([{
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

  test("uses the canonical relation identity while preserving an explicit menu label", () => {
    const options = buildGroupOptions([{ field: "sender", header: "Contact" }], MESSAGE_METADATA, null);
    expect(options).toContainEqual({ id: "sender", label: "Contact", group: { field: "sender" }, type: "value" });
    expect(resolveResourceViewGroup({ field: "sender" }, MESSAGE_METADATA)).toEqual({ field: "sender" });
  });

  test("a display-only derived column cannot introduce an undeclared group", () => {
    const metadata = canonicalModel({ implCategory: { name: "implCategory", kind: "scalar", scalar: "String" } },
      testDataResource("integrate.Integration", { query: testResourceQuery({ axes: {
        implClass: testQueryAxis("implClass", { server: { input: "IMPL_CLASS", key: "impl_class" } }),
      } }) }));
    expect(buildGroupOptions([{ field: "implCategory" }], metadata, null).map((option) => option.group)).toEqual([{ field: "implClass" }]);
    expect(() => resolveResourceViewGroup({ field: "implCategory" }, metadata)).toThrow(/unknown group axis/);
  });

  test("offers declared JSON axes without exposing their wire names", () => {
    const metadata = canonicalModel({}, testDataResource("messaging.Message", { query: testResourceQuery({ axes: {
      "metadata.mailbox": testQueryAxis("metadata.mailbox", { kind: "json", paths: ["metadata"], server: { input: "METADATA__MAILBOX", key: "metadata__mailbox" } }),
    } }) }));
    expect(buildGroupOptions([], metadata, null)).toEqual([{ id: "metadata.mailbox", label: "Metadata Mailbox", group: { field: "metadata.mailbox" }, type: "value" }]);
  });
});

describe("relationFieldInfo / relationListFieldInfo", () => {
  const tax = canonicalModel({}, {
    ...relationResource("taxes.Tax", "taxes"),
    recordRepresentation: "name",
    roots: { list: "taxes", create: "insert_taxes_one" },
  });
  const productVariant = canonicalModel({}, {
    ...relationResource("catalog.ProductVariant", "product_variants"),
    recordRepresentation: "displayName",
  });
  const unlistable = canonicalModel({}, testDataResource("misc.Unlistable", {
    roots: { list: null },
    capabilities: [],
  }));
  const scope = canonicalModel({}, {
    ...relationResource("accounting.Scope", "scopes"),
    recordRepresentation: "name",
  });
  const schema = schemaFieldMetadataFromDataResources([
    tax.resource,
    productVariant.resource,
    unlistable.resource,
    scope.resource,
  ]);
  const model = canonicalModel({
      product: {
        name: "product",
        kind: "relation",
        relationModelLabel: "catalog.ProductVariant",
      },
      taxes: {
        name: "taxes",
        kind: "list",
        scalar: "ID",
        relationModelLabel: "taxes.Tax",
      },
      labels: { name: "labels", kind: "list", scalar: "String" },
      orphan: {
        name: "orphan",
        kind: "list",
        relationModelLabel: "misc.Unlistable",
      },
      // A to-one FK the node projects as a bare `ID!` scalar: a scalar leaf that
      // still carries a relation target + the scalar-id `select` widget.
      scope: {
        name: "scope",
        kind: "relation",
        relationObject: false,
        scalar: "ID",
        widget: "select",
        relationModelLabel: "accounting.Scope",
      },
      // The record's own opaque id — a bare `ID` scalar with no relation target.
      id: { name: "id", kind: "scalar", scalar: "ID" },
    }, testDataResource("orders.Line"));

  test("resolves a to-one relation, but not a to-many, for relationFieldInfo", () => {
    expect(relationFieldInfo("product", model, schema)?.resource).toBe(
      "catalog.ProductVariant",
    );
    // An M2M is `kind: "list"`, so the to-one resolver ignores it (else it would
    // render a single picker over a many field).
    expect(relationFieldInfo("taxes", model, schema)).toBeNull();
  });

  test("resolves an ID-scalar FK as a scalar-id relation picker, but not a bare id", () => {
    // A `scope` FK the node projects as a bare `ID!` still wires the picker/label
    // through the relation metadata, so the form gets a usable relation widget.
    const info = relationFieldInfo("scope", model, schema);
    expect(info?.resource).toBe("accounting.Scope");
    expect(info?.labelField).toBe("name");
    // Its metadata widget is `select` (not `many2one`), so the form selects it as a
    // scalar leaf — a valid detail query, never an object sub-selection.
    const [resolved] = fieldsWithMetadataDefaults([{ name: "scope" }], model);
    expect(resolved?.widget).toBe("select");
    // The record's own bare `ID` scalar (no relation target) stays opaque.
    expect(relationFieldInfo("id", model, schema)).toBeNull();
  });

  test("resolves an M2M relation target for relationListFieldInfo", () => {
    const info = relationListFieldInfo("taxes", model, schema);
    expect(info?.resource).toBe("taxes.Tax");
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
  const metadata = canonicalModel({
      amountTotal: {
        name: "amountTotal",
        kind: "scalar",
        scalar: "Decimal",
        widget: "money",
        currencyField: "currency",
      },
    }, testDataResource("orders.Order"));

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
  const metadata = canonicalModel({
      product: {
        name: "product",
        kind: "relation",
        widget: "many2one",
        relationModelLabel: "catalog.ProductVariant",
        relationObject: true,
      },
      // A to-one FK projected as a public-id scalar: `relation` semantics
      // (many2one widget, relation axis) but NOT a nested object — must stay a leaf.
      location: {
        name: "location",
        kind: "relation",
        widget: "many2one",
        relationModelLabel: "stock.Location",
        relationObject: false,
      },
      quantity: { name: "quantity", kind: "scalar", scalar: "Decimal" },
      project: {
        name: "project",
        kind: "relation",
        relationModelLabel: "projects.Project",
        relationObject: true,
      },
    }, testDataResource("orders.Line", {
      query: testResourceQuery({ fields: {
        product: testQueryField("product", { relation: { model: "catalog.ProductVariant", identityPath: "product.id", labelPath: "product.display_name" } }),
        project: testQueryField("project", { relation: { model: "projects.Project", identityPath: "project.id", labelPath: "project.title" } }),
      } }),
    }));
  const productVariant = canonicalModel(
    {
          display_name: {
            name: "display_name",
            kind: "scalar",
            scalar: "String",
          },
    },
    testDataResource("catalog.ProductVariant", {
      recordRepresentation: "display_name",
    }),
  );
  const location = canonicalModel(
    {},
    testDataResource("stock.Location", { recordRepresentation: "name" }),
  );
  const project = canonicalModel(
    {
          title: { name: "title", kind: "scalar", scalar: "String" },
          product: {
            name: "product",
            kind: "relation",
            relationModelLabel: "catalog.ProductVariant",
            relationObject: true,
          },
    },
    testDataResource("projects.Project", {
      query: testResourceQuery({ fields: { product: testQueryField("product", { relation: { model: "catalog.ProductVariant", identityPath: "product.id", labelPath: "product.display_name" } }) } }),
      recordRepresentation: "title",
    }),
  );
  const schema = schemaFieldMetadataFromDataResources([
    productVariant.resource,
    location.resource,
    project.resource,
  ]);

  test("a bare relation column reads its related type's label path, not a leaf object", () => {
    const [column] = columnsWithMetadataDefaults<Row>(
      [{ field: "product", header: "Product" }],
      metadata,
      schema,
    );
    expect(column?.field).toBe("product.display_name");
    expect(column?.selectionPaths).toEqual([
      "product.id",
      "product.display_name",
    ]);
    // The label renders as a scalar, so the relation's many2one edit widget drops.
    expect(column?.widget).toBeUndefined();
    expect(column?.header).toBe("Product");
  });

  test("finalized relation paths work without target resource metadata", () => {
    const [column] = columnsWithMetadataDefaults<Row>([{ field: "product" }], metadata);
    expect(column?.field).toBe("product.display_name");
    expect(column?.selectionPaths).toEqual(["product.id", "product.display_name"]);
  });

  test("a relation without finalized selectable paths fails with a named error", () => {
    const broken = { ...metadata, resource: { ...metadata.resource, query: testResourceQuery() } };
    expect(() => columnsWithMetadataDefaults<Row>([{ field: "product" }], broken)).toThrow(RelationRepresentationError);
  });

  test("a to-one FK projected as a public-id scalar stays a leaf (not sub-selected)", () => {
    const [column] = columnsWithMetadataDefaults<Row>([{ field: "location" }], metadata, schema);
    // `location` is `kind: relation` but not `relationObject` — selecting
    // `location { name }` would fail ("ID has no subfields"), so it stays a leaf.
    expect(column?.field).toBe("location");
  });

  test("expands a nested relation-terminal path and pins its GraphQL selection", () => {
    const resolved = columnsWithMetadataDefaults<Row>(
      [{ field: "project.product" }, { field: "quantity" }],
      metadata,
      schema,
    );
    expect(resolved[0]?.field).toBe("project.product.display_name");
    expect(resolved[0]?.selectionPaths).toEqual([
      "project.product.id",
      "project.product.display_name",
    ]);
    expect(resolved[1]?.field).toBe("quantity");
    expect(requestedFieldPaths(resolved, undefined, metadata)).toEqual([
      "id",
      "project.product.id",
      "project.product.display_name",
      "quantity",
    ]);
  });

  test("keeps an explicit scalar path structural when an intermediate relation target has no metadata", () => {
    const message = canonicalModel({
        thread: {
          name: "thread",
          kind: "relation",
          relationModelLabel: "messaging.Thread",
          relationObject: true,
        },
      }, testDataResource("messaging.Message"));
    const thread = canonicalModel(
      {
            title: {
              name: "title",
              kind: "relation",
              relationModelLabel: "messaging.Fragment",
              relationObject: true,
            },
      },
      testDataResource("messaging.Thread"),
    );
    const messagingSchema = schemaFieldMetadataFromDataResources([
      thread.resource,
    ]);
    const resolved = columnsWithMetadataDefaults<Row>(
      [{ field: "thread.title.text" }],
      message,
      messagingSchema,
    );

    expect(resolved[0]?.field).toBe("thread.title.text");
    expect(resolved[0]?.selectionPaths).toBeUndefined();
    const requested = requestedFieldPaths(resolved, undefined, message);
    expect(refineFieldsFromPaths(requested)).toEqual([
      "id",
      { thread: [{ title: ["text"] }] },
    ]);
    expect(
      rowValueAtPath(
        { thread: { title: { text: "Live inbox title" } } },
        resolved[0]?.field ?? "",
      ),
    ).toBe("Live inbox title");
  });

  test("a custom renderer keeps its relation field but still selects the representation", () => {
    const [rendered] = columnsWithMetadataDefaults<Row>(
      [{ field: "product", render: () => null }],
      metadata,
      schema,
    );
    expect(rendered?.field).toBe("product");
    expect(rendered?.selectionPaths).toEqual([
      "product.id",
      "product.display_name",
    ]);
  });
});

describe("canonical relation grouping", () => {
  test("offers one relation axis and never its label or backend key", () => {
    const options = buildGroupOptions([{ field: "sender.party.display_name" }, { field: "status" }], MESSAGE_METADATA, null);
    expect(options.map((option) => option.id)).toEqual(["sender", "status"]);
    expect(options).toContainEqual({ id: "sender", label: "Sender", group: { field: "sender" }, type: "value" });
  });
  test("reports stale label groups instead of silently dropping query state", () => {
    expect(() => validResourceViewGroupStack([{ field: "sender__display_name" }], MESSAGE_METADATA)).toThrow(/unknown group axis/);
    expect(() => validResourceViewGroupStack([{ field: "sender.display_name" }], MESSAGE_METADATA)).toThrow(/unknown group axis/);
    expect(validResourceViewGroupStack([{ field: "sender" }, { field: "status" }], MESSAGE_METADATA)).toEqual([{ field: "sender" }, { field: "status" }]);
  });
});

function relationResource(
  modelLabel: string,
  list: string,
): DataResourceMetadata {
  return testDataResource(modelLabel, {
    roots: { list },
    typeNames: {},
    capabilities: ["list"],
  });
}

function canonicalModel(
  fields: Readonly<Record<string, ModelFieldMetadata>>,
  resource: DataResourceMetadata,
): ModelMetadata {
  const resourceFields: DataResourceFieldMetadata[] = Object.values(fields).map(
    (field) => ({
      readable: true,
      aggregatable: false,
      creatable: false,
      updatable: false,
      requiredOnCreate: false,
      ...field,
    }),
  );
  const schema = schemaFieldMetadataFromDataResources([
    { ...resource, fields: resourceFields },
  ]);
  const model = schema.labels[resource.modelLabel];
  if (!model) throw new Error(`Missing test resource ${resource.modelLabel}.`);
  return model;
}
