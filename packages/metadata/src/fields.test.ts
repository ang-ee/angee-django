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

  test("resolves a money field to the money widget, filtering as a number", () => {
    const field: ModelFieldMetadata = {
      name: "amount",
      kind: "scalar",
      scalar: "Decimal",
      widget: "money",
      currencyField: "currency",
    };

    expect(defaultWidgetForModelField(field)).toBe("money");
    expect(filterFieldType("amount", field)).toBe("number");
  });
});
