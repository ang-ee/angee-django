import { describe, expect, test } from "vitest";

import {
  RelationRepresentationError,
  lineChildModelMetadata,
  lineReadSelectionPaths,
  relationRepresentationForPath,
} from "./artifact";
import type {
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
  ModelMetadata,
  SchemaFieldMetadata,
} from "./artifact";

function field(
  name: string,
  kind: DataResourceFieldMetadata["kind"],
  extra: Partial<DataResourceFieldMetadata> = {},
): DataResourceFieldMetadata {
  return {
    name,
    kind,
    readable: true,
    filterable: false,
    sortable: false,
    aggregatable: false,
    groupable: false,
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
    field("product", "relation", { relationModelLabel: "products.ProductVariant" }),
    field("priceUnit", "scalar", {
      scalar: "Decimal",
      widget: "money",
      currencyField: "entry.currency",
    }),
    field("role", "enum", { values: [{ value: "product" }, { value: "tax" }] }),
    // An M2M child (F-b): a `kind: "list"` field carrying a relation target, read
    // and written as a list of the related rows' public ids.
    field("taxes", "list", { scalar: "ID", relationModelLabel: "accounting.Tax" }),
  ],
};

describe("lineChildModelMetadata", () => {
  const child = lineChildModelMetadata(LINES);

  test("names the child model type from its label", () => {
    expect(child.typeName).toBe("JournalItemType");
  });

  test("projects a relation column to its node type target", () => {
    const product = child.fields.product;
    expect(product?.kind).toBe("relation");
    expect(product?.relationTarget).toBe("ProductVariantType");
  });

  test("carries the money widget and currency path so the cell resolves currency", () => {
    const price = child.fields.priceUnit;
    expect(price?.widget).toBe("money");
    expect(price?.currencyField).toBe("entry.currency");
    expect(price?.scalar).toBe("Decimal");
  });

  test("passes through enum values for a select cell", () => {
    expect(child.fields.role?.values).toEqual([{ value: "product" }, { value: "tax" }]);
  });

  test("projects an M2M child to a list kind with its relation target", () => {
    const taxes = child.fields.taxes;
    expect(taxes?.kind).toBe("list");
    expect(taxes?.relationTarget).toBe("TaxType");
  });
});

describe("lineReadSelectionPaths", () => {
  const schema: SchemaFieldMetadata = {
    types: {
      ProductVariantType: {
        typeName: "ProductVariantType",
        fields: { name: { name: "name", kind: "scalar", scalar: "String" } },
        recordRepresentation: "name",
      },
    },
  };

  test("selects the child id, order column, scalars, enums, relation id + label, and the M2M id list", () => {
    // The detail (`*_by_pk`) read must carry the lines' child columns so an
    // existing document's lines seed the composer instead of reading as absent.
    // An M2M child reads as a scalar list of public ids, so it is selected by
    // name (no nested `.id`/`.label`), like the `list[ID]` node field it projects.
    expect(lineReadSelectionPaths(LINES, schema)).toEqual([
      "id",
      "position",
      "product.id",
      "product.name",
      "priceUnit",
      "role",
      "taxes",
    ]);
  });

  test("fails by name when the relation representation target is unavailable", () => {
    expect(() => lineReadSelectionPaths(LINES, { types: {} })).toThrow(
      RelationRepresentationError,
    );
  });

  test("omits the order column when the child carries none", () => {
    const withoutPosition: DataResourceLinesMetadata = {
      ...LINES,
      positionField: null,
    };
    expect(lineReadSelectionPaths(withoutPosition, schema)).not.toContain("position");
  });
});

describe("relationRepresentationForPath", () => {
  const model: ModelMetadata = {
    typeName: "InitiativeProjectType",
    fields: {
      project: {
        name: "project",
        kind: "relation",
        relationTarget: "ProjectType",
        relationObject: true,
      },
    },
  };
  const schema: SchemaFieldMetadata = {
    types: {
      ProjectType: {
        typeName: "ProjectType",
        fields: {
          product: {
            name: "product",
            kind: "relation",
            relationTarget: "ProductType",
            relationObject: false,
            relationFilter: {
              field: "product",
              mode: "lookup",
              lookup: "sqid",
            },
          },
        },
      },
      ProductType: {
        typeName: "ProductType",
        recordRepresentation: "name",
        fields: { name: { name: "name", kind: "scalar", scalar: "String" } },
      },
    },
  };

  test("expands a nested relation-terminal path to id plus representation", () => {
    expect(relationRepresentationForPath("project.product", model, schema)).toEqual({
      selectionPaths: ["project.product.id", "project.product.name"],
      displayPath: "project.product.name",
    });
  });

  test("leaves a scalar-terminal path to its caller", () => {
    expect(relationRepresentationForPath("project.product.name", model, schema))
      .toBeNull();
  });

  test("leaves an explicit continuation structural when an intermediate target has no metadata type", () => {
    const message: ModelMetadata = {
      typeName: "MessageType",
      fields: {
        thread: {
          name: "thread",
          kind: "relation",
          relationTarget: "ThreadType",
          relationObject: true,
        },
      },
    };
    const messagingSchema: SchemaFieldMetadata = {
      types: {
        ThreadType: {
          typeName: "ThreadType",
          fields: {
            title: {
              name: "title",
              kind: "relation",
              relationTarget: "FragmentType",
              relationObject: true,
            },
          },
        },
      },
    };

    expect(
      relationRepresentationForPath("thread.title.text", message, messagingSchema),
    ).toBeNull();
  });

  test("still fails by name when a relation-terminal target type is missing", () => {
    expect(() =>
      relationRepresentationForPath("project", model, { types: {} })
    ).toThrow(
      'Relation field "project" targets missing metadata type "ProjectType".',
    );
  });

  test("still fails by name when a relation-terminal representation is undeclared", () => {
    const missingRepresentation: SchemaFieldMetadata = {
      types: {
        ProjectType: {
          typeName: "ProjectType",
          fields: {},
          recordRepresentation: "title",
        },
      },
    };

    expect(() =>
      relationRepresentationForPath("project", model, missingRepresentation)
    ).toThrow(
      'Record representation "title" is not declared on "ProjectType".',
    );
  });
});
