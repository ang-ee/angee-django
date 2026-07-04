import { describe, expect, test } from "vitest";

import { lineChildModelMetadata } from "./artifact";
import type { DataResourceFieldMetadata, DataResourceLinesMetadata } from "./artifact";

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
