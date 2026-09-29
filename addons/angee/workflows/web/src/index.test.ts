import { expectValidBaseAddon } from "@angee/app/testing";
import { expect, test } from "vitest";
import { DECISION_ORIGIN_SLOT } from "@angee/decisions";
import addon from "./index";

test("registers read-only workflow and run pages with additive record and decision context", () => {
  expectValidBaseAddon(addon);
  expect(addon.routes?.map((route) => route.name)).toContain("workflows.runs.record");
  expect(addon.slots?.some((slot) => slot.slot === DECISION_ORIGIN_SLOT)).toBe(true);
  expect(addon.chatter?.map((entry) => entry.id)).toEqual(["workflows"]);
});
