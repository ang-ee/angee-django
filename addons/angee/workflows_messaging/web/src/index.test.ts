import { composeAddons } from "@angee/app";
import { expectValidBaseAddon } from "@angee/app/testing";
import { resolveContainer } from "@angee/ui";
import { TRIGGER_MODEL } from "@angee/workflows";
import { expect, test } from "vitest";
import addon from "./index";

test("contributes channel scope through the trigger form owner's native section seam", () => {
  expectValidBaseAddon(addon);
  const { containers } = composeAddons([addon], { canonicalModelLabel: (model) => model });
  expect(resolveContainer(containers, "form#sections", { models: [TRIGGER_MODEL] }).map(({ id, address }) => ({ id, address })))
    .toEqual([{ id: "workflows_messaging.channel", address: `${TRIGGER_MODEL}#sections` }]);
});
