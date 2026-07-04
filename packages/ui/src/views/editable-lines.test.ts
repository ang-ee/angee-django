import { describe, expect, test } from "vitest";
import type {
  DataResourceFieldMetadata,
  DataResourceLinesMetadata,
  Row,
} from "@angee/metadata";

import {
  diffLines,
  duplicateLineRow,
  emptyLineRow,
  lineDiffConfig,
  lineToInput,
  recordLinesToRows,
} from "./editable-lines";

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
  modelLabel: "sales.SalesOrderLine",
  positionField: "position",
  fields: [
    field("product", "relation", { relationModelLabel: "products.ProductVariant" }),
    field("label", "scalar", { scalar: "String" }),
    field("quantity", "scalar", { scalar: "Decimal" }),
    field("position", "scalar", { scalar: "Int" }),
  ],
};

const config = lineDiffConfig(LINES);

describe("lineDiffConfig", () => {
  test("derives id, position, editable columns, and relation columns", () => {
    expect(config.idField).toBe("id");
    expect(config.positionField).toBe("position");
    expect(config.fieldNames).toEqual(["product", "label", "quantity", "position"]);
    expect([...config.relationFields]).toEqual(["product"]);
  });
});

describe("lineToInput", () => {
  test("keeps the id for an existing row and sets position from order", () => {
    const row: Row = { id: "ln_a", product: { id: "p1" }, label: "Widget", quantity: 2, position: 5 };
    expect(lineToInput(row, 0, config)).toEqual({
      id: "ln_a",
      product: "p1",
      label: "Widget",
      quantity: 2,
      position: 0,
    });
  });

  test("omits the id for a new row", () => {
    const row: Row = { product: "p3", label: "New", quantity: 1 };
    const input = lineToInput(row, 1, config);
    expect(input.id).toBeUndefined();
    expect(input).toEqual({ product: "p3", label: "New", quantity: 1, position: 1 });
  });
});

describe("diffLines", () => {
  const baseline: Row[] = [
    { id: "ln_a", product: { id: "p1" }, label: "A", quantity: 1, position: 0 },
    { id: "ln_b", product: { id: "p2" }, label: "B", quantity: 2, position: 1 },
  ];

  test("classifies create, update, and delete against the baseline", () => {
    const current: Row[] = [
      { id: "ln_a", product: "p1", label: "A", quantity: 5, position: 0 },
      { product: "p3", label: "C", quantity: 1, position: 1 },
    ];
    const diff = diffLines(baseline, current, config);

    expect(diff.updated.map((line) => line.id)).toEqual(["ln_a"]);
    expect(diff.created).toHaveLength(1);
    expect(diff.created[0]?.id).toBeUndefined();
    expect(diff.deleted).toEqual(["ln_b"]);
    expect(diff.hasChanges).toBe(true);
    // The payload is the full desired list — existing id kept, new row without id.
    expect(diff.payload.map((line) => line.id)).toEqual(["ln_a", undefined]);
  });

  test("an untouched set (relation read vs picked id) is not dirty", () => {
    const current: Row[] = [
      { id: "ln_a", product: { id: "p1" }, label: "A", quantity: 1, position: 0 },
      { id: "ln_b", product: "p2", label: "B", quantity: 2, position: 1 },
    ];
    const diff = diffLines(baseline, current, config);

    expect(diff.hasChanges).toBe(false);
    expect(diff.created).toEqual([]);
    expect(diff.updated).toEqual([]);
    expect(diff.deleted).toEqual([]);
  });

  test("reorder marks moved rows updated and rewrites position from order", () => {
    const current: Row[] = [
      { id: "ln_b", product: "p2", label: "B", quantity: 2, position: 1 },
      { id: "ln_a", product: "p1", label: "A", quantity: 1, position: 0 },
    ];
    const diff = diffLines(baseline, current, config);

    expect(diff.updated.map((line) => line.id).sort()).toEqual(["ln_a", "ln_b"]);
    expect(diff.deleted).toEqual([]);
    expect(diff.payload).toEqual([
      { id: "ln_b", product: "p2", label: "B", quantity: 2, position: 0 },
      { id: "ln_a", product: "p1", label: "A", quantity: 1, position: 1 },
    ]);
  });
});

describe("recordLinesToRows", () => {
  test("normalizes a record's lines into seed rows, defaulting position to order", () => {
    const rows = recordLinesToRows(
      [
        { id: "ln_a", product: { id: "p1" }, label: "A", quantity: 1, position: 0 },
        { id: "ln_b", product: { id: "p2" }, label: "B", quantity: 2 },
      ],
      config,
    );
    expect(rows).toEqual([
      { id: "ln_a", product: { id: "p1" }, label: "A", quantity: 1, position: 0 },
      { id: "ln_b", product: { id: "p2" }, label: "B", quantity: 2, position: 1 },
    ]);
  });

  test("returns an empty list for a missing lines collection", () => {
    expect(recordLinesToRows(undefined, config)).toEqual([]);
    expect(recordLinesToRows(null, config)).toEqual([]);
  });
});

describe("emptyLineRow / duplicateLineRow", () => {
  test("a blank row nulls relations, blanks scalars, and tails the position", () => {
    expect(emptyLineRow(2, config)).toEqual({
      product: null,
      label: "",
      quantity: "",
      position: 2,
    });
  });

  test("a duplicate drops the identity so it saves as a create", () => {
    const duplicate = duplicateLineRow(
      { id: "ln_a", product: "p1", label: "A", quantity: 1, position: 0 },
      config,
    );
    expect(duplicate.id).toBeUndefined();
    expect(duplicate).toEqual({ product: "p1", label: "A", quantity: 1, position: 0 });
  });
});
