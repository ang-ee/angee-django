import { expect, test, vi } from "vitest";
import { FILTER_OPERATORS } from "@angee/metadata";
import { createLabelForResource, customFilterChipLabel, filterOperatorLabel } from "./labels";

test("default create label uses the route vocabulary model label", () => {
  const t = (_key: string, vars?: Record<string, string | number>) => `New ${vars?.resource}`;
  expect(createLabelForResource("notes.Note", t, "Entry")).toBe("New Entry");
  expect(createLabelForResource("notes.Note", t)).toBe("New note");
});

test("every operator resolves an i18n key and clause copy uses the supplied translator", () => {
  for (const operator of [...FILTER_OPERATORS, "isNotNull"] as const) {
    expect(filterOperatorLabel(operator)).not.toBe(`search.operator.${operator}`);
  }
  const t = vi.fn((key: string) => `translated:${key}`);
  expect(filterOperatorLabel("gte", t)).toBe("translated:search.operator.gte");
  expect(customFilterChipLabel({ fieldLabel: "Created", operator: "isNull", value: false, t }))
    .toBe("translated:search.emptyClause");
  expect(t).toHaveBeenCalledWith("search.notEmpty");
  expect(t).toHaveBeenCalledWith("search.emptyClause", { field: "Created", value: "translated:search.notEmpty" });
});
