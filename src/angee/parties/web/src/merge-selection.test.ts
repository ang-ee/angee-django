import { describe, expect, test } from "vitest";

import { selectionSplit } from "./merge-selection";

const ids = new Set(["ada", "grace", "joan", "mary"]);
const group = (key: string | null, groupIds: readonly string[], parents: readonly string[] = []) => ({
  field: "display_name",
  key,
  label: key ?? "No value",
  parents: parents.map((label) => ({ field: "organization", key: label, label })),
  ids: groupIds,
});

describe("a parties list's selection as duplicate sets", () => {
  test("an ungrouped list's whole selection is one set, before and after the grouping seam", () => {
    expect(selectionSplit(ids)).toEqual({ kind: "ungrouped", ids: ["ada", "grace", "joan", "mary"] });
    expect(selectionSplit(ids, { filter: {}, selectedRows: [] })).toEqual({
      kind: "ungrouped",
      ids: ["ada", "grace", "joan", "mary"],
    });
  });

  test("a grouped list whose selection cannot be placed gives no batch at all", () => {
    expect(selectionSplit(ids, { filter: {}, selectedRows: [], selectedGroups: null })).toEqual({ kind: "unplaceable" });
  });

  test("a grouped list gives one set per group of two or more, headed by its ancestry", () => {
    const split = selectionSplit(ids, {
      selectedGroups: [group("Ada Lovelace", ["ada", "grace"], ["Example Ltd"]), group("Joan", ["joan"]), group(null, ["mary"])],
    });

    expect(split).toEqual({
      kind: "grouped",
      sets: [{ members: ["ada", "grace"], heading: "Example Ltd › Ada Lovelace" }],
      unpaired: 1,
      blank: 1,
    });
  });

  test("a group whose value is blank is never a duplicate set", () => {
    expect(selectionSplit(ids, { selectedGroups: [group("", ["ada", "grace"]), group("  ", ["joan", "mary"])] })).toEqual({
      kind: "grouped",
      sets: [],
      unpaired: 0,
      blank: 4,
    });
  });
});
