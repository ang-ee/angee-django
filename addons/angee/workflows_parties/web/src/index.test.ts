import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import decisions from "@angee/decisions";
import { resolveContainer } from "@angee/ui";
import { expect, test } from "vitest";

import workflowsParties from "./index";

test("composes identity and mapped pair content into the shared decisions inbox", () => {
  expect(() => expectValidBaseAddon(workflowsParties)).not.toThrow();
  const { containers } = composeAddons([decisions, { ...workflowsParties, dependsOn: ["decisions"] }],
    { canonicalModelLabel: (model) => model });
  // One child per decision kind, keyed by the kind the inbox renders; unsequenced siblings resolve in id order.
  expect(resolveContainer(containers, "decisions#content").map(({ id, key }) => ({ id, key }))).toEqual([
    { id: "workflows-parties.review-dupe-party", key: "review-dupe-party" },
    { id: "workflows-parties.review-party-identity", key: "review-party-identity" },
  ]);
});
