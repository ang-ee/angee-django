import { expectValidBaseAddon } from "@angee/app/testing";
import { DECISION_CONTENT_SLOT } from "@angee/decisions";
import { expect, test } from "vitest";

import workflowsParties from "./index";

test("composes identity and mapped pair content into the shared decisions inbox", () => {
  expect(() => expectValidBaseAddon(workflowsParties)).not.toThrow();
  expect(workflowsParties.slots?.map(({ slot, id }) => ({ slot, id }))).toEqual([
    { slot: DECISION_CONTENT_SLOT, id: "review-party-identity" },
    { slot: DECISION_CONTENT_SLOT, id: "review-dupe-party" },
  ]);
});
