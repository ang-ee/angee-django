import { describe, expect, test } from "vitest";

import { admittedAsideTabs, isSurfaceSlotAdmitted } from "./surface-policy";

describe("aside admission", () => {
  test("an omitted aside keeps the declared tab order", () => {
    expect(admittedAsideTabs({ chatter: { tabs: ["activity", "comments"] } }))
      .toEqual(["activity", "comments"]);
  });

  test("app admission limits route tab selection", () => {
    expect(admittedAsideTabs({ admit: { aside: ["comments"] }, chatter: { tabs: ["activity", "comments"] } }))
      .toEqual(["comments"]);
    expect(admittedAsideTabs({ admit: { aside: [] } })).toEqual([]);
  });

  test("hidden has no tabs", () => {
    expect(admittedAsideTabs({ chatter: "hidden" })).toEqual([]);
  });
});

test("a named slot is restricted while an omitted slot is unchanged", () => {
  const admission = { slots: { "record.chrome": ["share"] } };
  expect(isSurfaceSlotAdmitted(admission, "record.chrome", "share")).toBe(true);
  expect(isSurfaceSlotAdmitted(admission, "record.chrome", "workflow")).toBe(false);
  expect(isSurfaceSlotAdmitted(admission, "console.notice", "preview")).toBe(true);
  expect(isSurfaceSlotAdmitted({ slots: { "record.chrome": [] } }, "record.chrome", "share")).toBe(false);
});
