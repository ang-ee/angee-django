import type { SelectedGroup } from "@angee/ui";
import { describe, expect, test } from "vitest";

import { selectionSplit } from "./merge-selection";

const ids = new Set(["ada", "grace", "joan", "mary"]);
const group = (key: string | null, groupIds: readonly string[], parents: readonly string[] = []): SelectedGroup => ({
  field: "display_name",
  key,
  label: key ?? "No value",
  parents: parents.map((label) => ({ field: "organization", key: label, label })),
  ids: groupIds,
});

describe("a parties list's selection as duplicate sets", () => {
  test("an ungrouped list's whole selection is one set", () => {
    expect(selectionSplit(ids, undefined)).toEqual({ kind: "ungrouped", ids: ["ada", "grace", "joan", "mary"] });
  });

  test("a grouped list whose selection cannot be placed gives no batch at all", () => {
    expect(selectionSplit(ids, null)).toEqual({ kind: "unplaceable" });
  });

  test("two groups of two are two sets, never one of four, each headed and marked by its levels", () => {
    expect(selectionSplit(ids, [group("Ada Lovelace", ["ada", "grace"], ["Example Ltd"]), group("Joan Example", ["joan", "mary"])]))
      .toEqual({
        kind: "grouped",
        sets: [
          {
            members: ["ada", "grace"],
            heading: "Example Ltd › Ada Lovelace",
            grouping: [{ field: "organization", key: "Example Ltd" }, { field: "display_name", key: "Ada Lovelace" }],
          },
          {
            members: ["joan", "mary"],
            heading: "Joan Example",
            grouping: [{ field: "display_name", key: "Joan Example" }],
          },
        ],
        unpaired: 0,
        blank: 0,
      });
  });

  test("a group of one is left out and counted, and a group without a value is never a set", () => {
    expect(selectionSplit(ids, [group("Ada Lovelace", ["ada"]), group(null, ["grace", "joan"]), group("", ["mary"])]))
      .toEqual({ kind: "grouped", sets: [], unpaired: 1, blank: 3 });
  });
});
