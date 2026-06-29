import { createElement } from "react";
import { describe, expect, test } from "vitest";

import { optionLabel, optionTextLabel, relationValueId } from "./types";

describe("widget option helpers", () => {
  test("resolves option labels and falls back to the raw value", () => {
    expect(optionLabel([{ value: "draft", label: "Draft" }], "draft")).toBe(
      "Draft",
    );
    expect(optionLabel([{ value: "draft", label: "Draft" }], "done")).toBe(
      "done",
    );
    expect(optionLabel(undefined, null)).toBe("");
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
