import { expectValidBaseAddon } from "@angee/app/testing";
import { expect, test } from "vitest";
import workflowsParties from "./index";

test("composes identity reviews through the generic decision card", () => {
  expect(() => expectValidBaseAddon(workflowsParties)).not.toThrow();
  expect(workflowsParties.containers).toBeUndefined();
});
