import { describe, expect, test } from "vitest";

import { writeEnumOptions } from "./proposal-form";

describe("proposal form enum writes", () => {
  test("normalizes GraphQL enum reads to model-value writes", () => {
    expect(
      writeEnumOptions([
        { value: "LOW", label: "Low" },
        { value: "MEDIUM", label: "Medium" },
      ]),
    ).toEqual([
      { value: "low", label: "Low" },
      { value: "medium", label: "Medium" },
    ]);
  });
});
