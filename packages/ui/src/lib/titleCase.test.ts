import { expect, test } from "vitest";

import { titleCase } from "./titleCase";

test.each([
  ["URLAlias", "URL Alias"],
  ["WorkItem", "Work Item"],
])("titleCase humanizes %s as %s", (value, expected) => {
  expect(titleCase(value)).toBe(expected);
});
