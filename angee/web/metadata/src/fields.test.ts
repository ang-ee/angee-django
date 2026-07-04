import { describe, expect, test } from "vitest";

import {
  defaultWidgetForModelField,
  filterFieldType,
} from "./fields";
import type { ModelFieldMetadata } from "./artifact";

describe("field metadata helpers", () => {
  test("treats Decimal as a number field with the numeric widget", () => {
    const field: ModelFieldMetadata = {
      name: "amount",
      kind: "scalar",
      scalar: "Decimal",
    };

    expect(defaultWidgetForModelField(field)).toBe("float");
    expect(filterFieldType("amount", field)).toBe("number");
  });
});
