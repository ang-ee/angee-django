import { expect, test } from "vitest";
import { selectedFieldsPresent } from "./selections";

test("declared selection completeness distinguishes nulls and nested omissions", () => {
  const fields = ["id", { lines: ["id", { account: ["name"] }] }];
  expect(selectedFieldsPresent({ id: "one", lines: [{ id: "line", account: null }] }, fields)).toBe(true);
  expect(selectedFieldsPresent({ id: "one", lines: [{ id: "line", account: {} }] }, fields)).toBe(false);
  expect(selectedFieldsPresent({ id: "one" }, fields)).toBe(false);
});
