import { expect, test } from "vitest";

import { statusIconVariants } from "./status-icon";

test.each([
  ["info", "text-info-text"],
  ["success", "text-success-text"],
  ["warning", "text-warning-text"],
  ["danger", "text-danger-text"],
  ["muted", "text-fg-muted"],
] as const)("keeps the %s status icon class", (tone, expected) => {
  expect(statusIconVariants({ tone })).toContain(expected);
});
