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

  test("classifies String scalars as the columns whose blank is a real value", () => {
    expect([...config.stringFields]).toEqual(["label"]);
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

  // FormView's blank-value rule at the line boundary: an untouched Decimal cell
  // ("") must not ride the create input — Strawberry Decimal coercion rejects ""
  // and the whole save dies — so the key is omitted and the input/model defaults
  // apply. A blank String cell IS the value "" and ships verbatim.
  test("omits blank non-String cells on a new row so input and model defaults apply", () => {
    const input = lineToInput(emptyLineRow(2, config), 2, config);
    expect(input).toEqual({ label: "", position: 2 });
    expect(input).not.toHaveProperty("quantity");
    expect(input).not.toHaveProperty("product");
  });

  test("ships null for a cleared non-String cell on an existing row", () => {
    const row: Row = { id: "ln_a", product: "", label: "A", quantity: "" };
    expect(lineToInput(row, 0, config)).toEqual({
      id: "ln_a",
      product: null,
      label: "A",
      quantity: null,
      position: 0,
    });
  });

  // A number/date widget clears to null (not ""): the same blank rule applies,
  // matching mutationData's `value == null` create omission.
  test("a widget-cleared (null) numeric cell is omitted on a new row", () => {
    const row: Row = { product: "p3", label: "C", quantity: null };
    const input = lineToInput(row, 0, config);
    expect(input).toEqual({ product: "p3", label: "C", position: 0 });
    expect(input).not.toHaveProperty("quantity");
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

  test("a new row with untouched numeric cells creates without those keys", () => {
    const current: Row[] = [
      ...baseline,
      { product: "p3", label: "C", quantity: "", position: 2 },
    ];
    const diff = diffLines(baseline, current, config);

    expect(diff.created).toEqual([{ product: "p3", label: "C", position: 2 }]);
    expect(diff.created[0]).not.toHaveProperty("quantity");
    expect(diff.payload[2]).not.toHaveProperty("quantity");
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

// A line with an M2M child (`taxes`) and an enum child (`kind`): the F6 M2M/enum
// cells. The M2M carries a relation target (so it is a multi-select, not a tag
// input) and serializes to an array of public ids; the enum reads as the
// UPPERCASE wire member and writes its lowercase model value.
const RICH_LINES: DataResourceLinesMetadata = {
  field: "items",
  modelLabel: "accounting.JournalItem",
  positionField: "position",
  fields: [
    field("taxes", "list", { relationModelLabel: "accounting.Tax", scalar: "ID" }),
    field("kind", "enum", { values: [{ value: "GOODS" }, { value: "SERVICE" }] }),
    field("labels", "list", { scalar: "String" }),
    field("position", "scalar", { scalar: "Int" }),
  ],
};

const richConfig = lineDiffConfig(RICH_LINES);

describe("lineDiffConfig — M2M + enum classification", () => {
  test("an M2M child (list + relation target) is a multi-relation; a plain list is not", () => {
    expect([...richConfig.multiRelationFields]).toEqual(["taxes"]);
    expect([...richConfig.enumFields]).toEqual(["kind"]);
    // A relation target is what distinguishes an M2M list from a string array.
    expect(richConfig.multiRelationFields.has("labels")).toBe(false);
  });
});

describe("lineToInput — M2M + enum normalization", () => {
  test("an M2M cell serializes to a de-duped id array; an enum writes its lowercase value", () => {
    const row: Row = {
      id: "it_a",
      // A read carries related records ({ id }); a fresh pick carries bare ids.
      taxes: [{ id: "tx1" }, "tx2", { id: "tx1" }],
      kind: "SERVICE",
      labels: ["urgent"],
      position: 4,
    };
    expect(lineToInput(row, 0, richConfig)).toEqual({
      id: "it_a",
      taxes: ["tx1", "tx2"],
      kind: "service",
      labels: ["urgent"],
      position: 0,
    });
  });

  test("a blank row seeds an empty id array for the M2M cell", () => {
    expect(emptyLineRow(1, richConfig)).toEqual({
      taxes: [],
      kind: "",
      labels: "",
      position: 1,
    });
  });

  // A blank enum ("" would fail the String input's choice validation) and a
  // blank plain-list cell are withheld on a new row; the empty M2M id array is
  // a real value ("no relations") and ships.
  test("a new row omits its blank enum and plain-list cells", () => {
    const input = lineToInput(emptyLineRow(1, richConfig), 1, richConfig);
    expect(input).toEqual({ taxes: [], position: 1 });
    expect(input).not.toHaveProperty("kind");
    expect(input).not.toHaveProperty("labels");
  });
});

describe("diffLines — M2M + enum", () => {
  const baseline: Row[] = [
    { id: "it_a", taxes: ["tx1"], kind: "GOODS", labels: [], position: 0 },
  ];

  test("an untouched enum (UPPERCASE read) and M2M (id-array read) are not dirty", () => {
    const current: Row[] = [
      { id: "it_a", taxes: [{ id: "tx1" }], kind: "GOODS", labels: [], position: 0 },
    ];
    expect(diffLines(baseline, current, richConfig).hasChanges).toBe(false);
  });

  test("adding a tax and switching the enum both mark the row updated", () => {
    const current: Row[] = [
      { id: "it_a", taxes: ["tx1", "tx2"], kind: "SERVICE", labels: [], position: 0 },
    ];
    const diff = diffLines(baseline, current, richConfig);
    expect(diff.updated.map((line) => line.id)).toEqual(["it_a"]);
    expect(diff.payload[0]).toEqual({
      id: "it_a",
      taxes: ["tx1", "tx2"],
      kind: "service",
      labels: [],
      position: 0,
    });
  });
});
