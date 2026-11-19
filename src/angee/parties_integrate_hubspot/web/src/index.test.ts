import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import parties from "@angee/parties";
import { describe, expect, test } from "vitest";

import partiesIntegrateHubSpot from "./index";

const TOOLBAR = "parties.directories#toolbar";

describe("angee.parties_integrate_hubspot addon manifest", () => {
  test("satisfies the rendered-addon invariants", () => {
    expect(() => expectValidBaseAddon(partiesIntegrateHubSpot)).not.toThrow();
  });

  test("contributes its connect verb to the directories toolbar, after CardDAV's", () => {
    // The parties owner declares the container; this addon adds its verb at the sequence after CardDAV's (10).
    const { containers } = composeAddons(
      [{ id: parties.id, containers: parties.containers }, { ...partiesIntegrateHubSpot, dependsOn: ["parties"] }],
      { canonicalModelLabel: (model) => model },
    );
    expect(containers.children[TOOLBAR]?.map(({ id, sequence }) => ({ id, sequence }))).toEqual([
      { id: "parties_integrate_hubspot.connect", sequence: 20 },
    ]);
  });
});
