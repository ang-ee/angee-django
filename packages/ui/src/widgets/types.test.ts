import { createElement } from "react";
import { describe, expect, test } from "vitest";

import { groupOptions, optionLabel, optionTextLabel, relationValueId, type WidgetOption } from "./types";

describe("widget option helpers", () => {
  test("groups options by their group order, keeping first appearance among equals and ungrouped last", () => {
    const options: WidgetOption[] = [
      { value: "loose", label: "Loose" },
      { value: "partner", label: "Partner", group: "Relationship", groupOrder: 2 },
      { value: "unordered", label: "Unordered", group: "Misc" },
      { value: "enterprise", label: "Enterprise", group: "Segment", groupOrder: 1 },
      { value: "prospect", label: "Prospect", group: "Relationship", groupOrder: 2 },
    ];
    const groups = groupOptions(options);
    expect(groups.map((group) => [group.label, group.options.map((option) => option.value)])).toEqual([
      ["Segment", ["enterprise"]],
      ["Relationship", ["partner", "prospect"]],
      ["Misc", ["unordered"]],
      [null, ["loose"]],
    ]);
    // With no grouped option, one unlabelled group holds them all.
    const flat: WidgetOption[] = [{ value: "a", label: "A" }];
    expect(groupOptions(flat)).toEqual([{ label: null, options: flat }]);
    expect(groupOptions<WidgetOption>([])).toEqual([{ label: null, options: [] }]);
  });

  test("resolves option labels and falls back to the raw value", () => {
    expect(optionLabel([{ value: "draft", label: "Draft" }], "draft")).toBe(
      "Draft",
    );
    expect(optionLabel([{ value: "draft", label: "Draft" }], "done")).toBe(
      "done",
    );
    expect(optionLabel(undefined, null)).toBe("");
    expect(optionLabel([{ value: "open", label: "Open" }], "OPEN")).toBe("Open");
    expect(optionLabel([{ value: "Medium", label: "Normal" }], "medium")).toBe("Normal");
  });

  test("coerces text labels without stringifying React nodes", () => {
    expect(optionTextLabel("Draft")).toBe("Draft");
    expect(optionTextLabel(3)).toBe("3");
    expect(optionTextLabel(createElement("span"))).toBeUndefined();
    expect(optionTextLabel(createElement("span"), "record")).toBe("record");
  });

  test("extracts relation ids from scalar and record values", () => {
    expect(relationValueId("model-1")).toBe("model-1");
    expect(relationValueId({ id: "model-1" })).toBe("model-1");
    expect(relationValueId({ id: 7 })).toBe("7");
    expect(relationValueId({ displayName: "Missing id" })).toBe("");
    expect(relationValueId(null)).toBe("");
  });
});
