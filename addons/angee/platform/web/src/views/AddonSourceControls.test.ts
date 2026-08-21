import { describe, expect, test } from "vitest";

import { parseAddonSourceValues } from "./AddonSourceControls";

describe("AddonSourceControls values", () => {
  test("omits blank optional source coordinates", () => {
    const parsed = parseAddonSourceValues({
      vcsBridgeId: " bridge-1 ",
      name: " angee/framework ",
      ref: "   ",
      path: undefined,
    });

    expect(parsed).toEqual({
      data: {
        vcs_bridge_id: "bridge-1",
        name: "angee/framework",
      },
    });
    expect(parsed.data).not.toHaveProperty("ref");
    expect(parsed.data).not.toHaveProperty("path");
  });

  test("preserves trimmed non-empty source coordinates", () => {
    expect(
      parseAddonSourceValues({
        vcsBridgeId: "bridge-1",
        name: "angee/framework",
        ref: " main ",
        path: " addons/demo ",
      }),
    ).toEqual({
      data: {
        vcs_bridge_id: "bridge-1",
        name: "angee/framework",
        ref: "main",
        path: "addons/demo",
      },
    });
  });
});
