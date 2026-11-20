import { describe, expect, test } from "vitest";

import { optionGroups } from "./option-groups";

interface Option { value: string; group?: string; groupOrder?: number }

describe("optionGroups", () => {
  test("ordered groups come first; unordered groups and the ungrouped bucket follow in first appearance", () => {
    const options: Option[] = [
      { value: "loose" },
      { value: "partner", group: "Relationship", groupOrder: 2 },
      { value: "unordered", group: "Misc" },
      { value: "enterprise", group: "Segment", groupOrder: 1 },
      { value: "prospect", group: "Relationship", groupOrder: 2 },
      { value: "free" },
    ];
    expect(optionGroups(options).map((group) => [group.label, group.options.map((option) => option.value)])).toEqual([
      ["Segment", ["enterprise"]],
      ["Relationship", ["partner", "prospect"]],
      [null, ["loose", "free"]],
      ["Misc", ["unordered"]],
    ]);
  });

  test("categorised options with an order list the uncategorised last", () => {
    const options: Option[] = [{ value: "free" }, { value: "a", group: "A", groupOrder: 1 }];
    expect(optionGroups(options).map((group) => group.label)).toEqual(["A", null]);
  });

  test("with no group, one unlabelled group holds every option; none, nothing", () => {
    const single: Option[] = [{ value: "a" }];
    expect(optionGroups(single)).toEqual([{ label: null, options: single }]);
    expect(optionGroups<Option>([])).toEqual([]);
  });
});
