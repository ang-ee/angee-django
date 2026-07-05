import { describe, expect, test } from "vitest";

import { lineChildModelMetadata, lineReadSelectionPaths } from "./artifact";
import type {
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
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
});

describe("lineReadSelectionPaths", () => {
  const schema: SchemaFieldMetadata = {
    types: {
      ProductVariantType: {
        typeName: "ProductVariantType",
        fields: {},
        recordRepresentation: "name",
      },
    },
  };

  test("selects the child id, order column, scalars, enums, and relation id + label", () => {
    // The detail (`*_by_pk`) read must carry the lines' child columns so an
    // existing document's lines seed the composer instead of reading as absent.
    expect(lineReadSelectionPaths(LINES, schema)).toEqual([
      "id",
      "position",
      "product.id",
      "product.name",
      "priceUnit",
      "role",
    ]);
  });

  test("selects only the relation id when the target has no record representation", () => {
    expect(lineReadSelectionPaths(LINES, { types: {} })).toEqual([
      "id",
      "position",
      "product.id",
      "priceUnit",
      "role",
    ]);
  });

  test("omits the order column when the child carries none", () => {
    const withoutPosition: DataResourceLinesMetadata = {
      ...LINES,
      positionField: null,
    };
    expect(lineReadSelectionPaths(withoutPosition, schema)).not.toContain("position");
  });
});
