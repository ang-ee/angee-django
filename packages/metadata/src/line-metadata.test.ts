import { describe, expect, test } from "vitest";

import {
  RelationRepresentationError,
  lineReadSelectionPaths,
  relationRepresentationForPath,
  resourceReadSelectionPaths,
  schemaFieldMetadataFromDataResources,
} from "./artifact";
import type {
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
} from "./artifact";
import { testDataResource, testResourceQuery, testQueryField } from "./testing";

function field(
  name: string,
  kind: DataResourceFieldMetadata["kind"],
  extra: Partial<DataResourceFieldMetadata> = {},
): DataResourceFieldMetadata {
  return {
    name,
    kind,
    readable: true,
    aggregatable: false,
    creatable: true,
    updatable: true,
    requiredOnCreate: false,
    ...extra,
  };
}

const LINES: DataResourceLinesMetadata = {
  field: "lines",
  modelLabel: "accounting.JournalItem",
  positionField: "position",
  fields: [
    field("product", "relation", {
      relationModelLabel: "products.ProductVariant",
      relationObject: true,
    }),
    field("priceUnit", "scalar", {
      scalar: "Decimal",
      widget: "money",
      currencyField: "entry.currency",
    }),
    field("role", "enum", { values: [{ value: "product" }, { value: "tax" }] }),
    field("taxes", "list", {
      scalar: "ID",
      relationModelLabel: "accounting.Tax",
    }),
    field("ownerId", "relation", {
      relationModelLabel: "accounts.User",
      relationObject: false,
    }),
  ],
};

describe("line field references", () => {
  test("uses the original wire fields without a synthetic child model", () => {
    expect(LINES.fields?.[1]).toMatchObject({
      name: "priceUnit",
      widget: "money",
      currencyField: "entry.currency",
      scalar: "Decimal",
    });
    expect(LINES.fields?.[2]?.values).toEqual([
      { value: "product" },
      { value: "tax" },
    ]);
  });
});

describe("lineReadSelectionPaths", () => {
  const product = testDataResource("products.ProductVariant", {
    recordRepresentation: "name",
    fields: [field("name", "scalar", { scalar: "String" })],
  });
  const schema = schemaFieldMetadataFromDataResources([product]);

  test("selects scalar, object relation, M2M id list, and ID-projected relation shapes", () => {
    expect(lineReadSelectionPaths(LINES, schema)).toEqual([
      "id",
      "position",
      "product.id",
      "product.name",
      "priceUnit",
      "role",
      "taxes",
      "ownerId",
    ]);
  });

  test("fails by name when an object relation target is unavailable", () => {
    expect(() =>
      lineReadSelectionPaths(LINES, schemaFieldMetadataFromDataResources([]))
    ).toThrow(RelationRepresentationError);
  });

  test("omits the order column when the child carries none", () => {
    expect(lineReadSelectionPaths({ ...LINES, positionField: null }, schema))
      .not.toContain("position");
  });
});

describe("relationRepresentationForPath", () => {
  const product = testDataResource("catalog.Product", {
    recordRepresentation: "name",
    fields: [field("name", "scalar", { scalar: "String" })],
  });
  const project = testDataResource("projects.Project", {
    query: testResourceQuery({ fields: { product: testQueryField("product", { relation: { model: "catalog.Product", identityPath: "product.id", labelPath: "product.name" } }) } }),
    fields: [
      field("product", "relation", {
        relationModelLabel: "catalog.Product",
        relationObject: true,
      }),
    ],
  });
  const initiative = testDataResource("projects.Initiative", {
    query: testResourceQuery({ fields: { project: testQueryField("project", { relation: { model: "projects.Project", identityPath: "project.id", labelPath: null } }) } }),
    fields: [
      field("project", "relation", {
        relationModelLabel: "projects.Project",
        relationObject: true,
      }),
    ],
  });
  const schema = schemaFieldMetadataFromDataResources([
    initiative,
    project,
    product,
  ]);
  const model = schema.labels["projects.Initiative"]!;

  test("expands a nested relation-terminal path using canonical model labels", () => {
    expect(relationRepresentationForPath("project.product", model, schema)).toEqual({
      selectionPaths: ["project.product.id", "project.product.name"],
      displayPath: "project.product.name",
    });
  });

  test("leaves a scalar-terminal path to its caller", () => {
    expect(relationRepresentationForPath("project.product.name", model, schema))
      .toBeNull();
  });

  test("leaves an explicit dotted continuation structural without an indexed intermediate", () => {
    const message = testDataResource("messaging.Message", {
      fields: [
        field("thread", "relation", {
          relationModelLabel: "messaging.Thread",
          relationObject: true,
        }),
      ],
    });
    const messaging = schemaFieldMetadataFromDataResources([message]);
    expect(
      relationRepresentationForPath(
        "thread.title.text",
        messaging.labels["messaging.Message"]!,
        messaging,
      ),
    ).toBeNull();
  });

  test("uses finalized paths without requiring the target resource inventory", () => {
    const missing = schemaFieldMetadataFromDataResources([initiative]);
    expect(relationRepresentationForPath("project", missing.labels["projects.Initiative"]!, missing)).toEqual({
      selectionPaths: ["project.id"], displayPath: "project.id",
    });
  });

  test("does not guess a public identity absent from the actual relation projection", () => {
    const labelOnly = testDataResource("projects.Initiative", {
      fields: initiative.fields,
      query: testResourceQuery({ fields: { project: testQueryField("project", { relation: { model: "projects.Project", identityPath: null, labelPath: "project.title" } }) } }),
    });
    const schema = schemaFieldMetadataFromDataResources([labelOnly]);
    expect(relationRepresentationForPath("project", schema.labels["projects.Initiative"]!, schema)).toEqual({ selectionPaths: ["project.title"], displayPath: "project.title" });
  });

  test("fails when a relation has no finalized selectable representation", () => {
    const broken = schemaFieldMetadataFromDataResources([{ ...initiative, query: testResourceQuery() }]);
    expect(() => relationRepresentationForPath("project", broken.labels["projects.Initiative"]!, broken)).toThrow(/no finalized selectable representation/);
  });

});

describe("resourceReadSelectionPaths", () => {
  test("selects an explicitly ID-projected relation as a leaf", () => {
    const account = testDataResource("accounts.Account", {
      fields: [
        field("ownerId", "relation", {
          relationModelLabel: "accounts.User",
          relationObject: false,
        }),
      ],
    });
    const schema = schemaFieldMetadataFromDataResources([account]);

    expect(
      resourceReadSelectionPaths(schema.labels["accounts.Account"]!, schema),
    ).toEqual(["id", "ownerId"]);
    expect(
      relationRepresentationForPath(
        "ownerId",
        schema.labels["accounts.Account"]!,
        schema,
      ),
    ).toBeNull();
  });
});
