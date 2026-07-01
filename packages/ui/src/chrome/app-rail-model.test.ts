import { describe, expect, test } from "vitest";

import {
  moveRailItem,
  orderedRailItems,
  railDefaultTarget,
  railItemIdForTarget,
  railSortableMove,
  sameRailOrder,
} from "./app-rail-model";

const ITEMS = [
  { id: "notes", target: "/notes" },
  { id: "ops", target: "/ops" },
  { id: "integrate", target: "/integrate" },
];

describe("app rail model", () => {
  test("orders known ids first and appends new items", () => {
    expect(
      orderedRailItems(ITEMS, ["ops", "missing", "ops"]).map((item) => item.id),
    ).toEqual(["ops", "notes", "integrate"]);
  });

  test("moves items before and after a target", () => {
    expect(moveRailItem(["notes", "ops", "integrate"], "integrate", "notes", "before"))
      .toEqual(["integrate", "notes", "ops"]);
    expect(moveRailItem(["notes", "ops", "integrate"], "notes", "ops", "after"))
      .toEqual(["ops", "notes", "integrate"]);
  });

  test("derives sortable placement from item direction", () => {
    expect(railSortableMove(["notes", "ops", "integrate"], "notes", "integrate"))
      .toEqual(["ops", "integrate", "notes"]);
    expect(railSortableMove(["notes", "ops", "integrate"], "integrate", "notes"))
      .toEqual(["integrate", "notes", "ops"]);
  });

  test("resolves default targets and ids", () => {
    expect(railDefaultTarget({ target: " /notes " })).toBe("/notes");
    expect(railDefaultTarget({ target: "#" })).toBeNull();
    expect(railItemIdForTarget(ITEMS, "/integrate")).toBe("integrate");
    expect(railItemIdForTarget(ITEMS, "/missing")).toBeNull();
  });

  test("compares canonical order arrays", () => {
    expect(sameRailOrder(["notes", "ops"], ["notes", "ops"])).toBe(true);
    expect(sameRailOrder(["notes", "ops"], ["ops", "notes"])).toBe(false);
  });
});
