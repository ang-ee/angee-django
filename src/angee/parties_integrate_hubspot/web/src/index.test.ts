import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import parties, { DIRECTORIES_TOOLBAR, DIRECTORY_CREATE } from "@angee/parties";
import { describe, expect, test } from "vitest";

import partiesIntegrateHubSpot from "./index";

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
    expect(containers.children[DIRECTORIES_TOOLBAR]?.map(({ id, sequence, requires }) => ({ id, sequence, requires }))).toEqual([
      // Present only for an identity that may connect a directory.
      { id: "parties_integrate_hubspot.connect", sequence: 20, requires: DIRECTORY_CREATE },
    ]);
  });
});
