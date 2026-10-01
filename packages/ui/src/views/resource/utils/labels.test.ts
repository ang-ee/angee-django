import { expect, test } from "vitest";
import { createLabelForResource } from "./labels";

test("default create label uses the route vocabulary model label", () => {
  const t = (_key: string, vars?: Record<string, string | number>) => `New ${vars?.resource}`;
  expect(createLabelForResource("notes.Note", t, "Entry")).toBe("New Entry");
  expect(createLabelForResource("notes.Note", t)).toBe("New note");
});
