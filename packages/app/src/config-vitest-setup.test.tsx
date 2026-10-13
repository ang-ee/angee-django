// @vitest-environment happy-dom

import { getConfig } from "@testing-library/react";
import { expect, test } from "vitest";

test("the published setup gives waiting queries the cold-render limit", () => {
  // Read back through a test's own import: the setup must reach this instance.
  expect(getConfig().asyncUtilTimeout).toBe(4_000);
});
