import { expectValidBaseAddon } from "@angee/app/testing";
import { formViewSectionsSlot } from "@angee/ui";
import { expect, test } from "vitest";
import addon from "./index";

test("contributes channel scope through the trigger form owner's native section seam", () => {
  expectValidBaseAddon(addon);
  expect(addon.slots?.[0]).toMatchObject(formViewSectionsSlot("workflows.Trigger"));
});
